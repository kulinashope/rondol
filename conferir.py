"""Confere se as PREVISOES da apifootball.com acertaram em dias anteriores.

Cruza o endpoint `get_predictions` (probabilidades do modelo da API) com os
resultados reais de `get_events` (jogos finalizados) e calcula a taxa de acerto
por mercado:

  - 1X2 (Casa/Empate/Fora): palpite = maior probabilidade entre prob_HW/prob_D/prob_AW.
  - Over/Under 2.5 gols:     palpite = maior entre prob_O (over) e prob_U (under).
  - Ambas Marcam (BTTS):     palpite = maior entre prob_bts (sim) e prob_ots (nao).

Para cada mercado so contam os jogos finalizados com placar valido. Um filtro
opcional (--min-prob) considera apenas palpites em que a probabilidade escolhida
fica acima do limite (ex.: avaliar so os palpites "confiantes").

Uso:
    python conferir.py                       # ultimos 7 dias ate ontem
    python conferir.py --dias 14             # ultimos 14 dias ate ontem
    python conferir.py --data 2026-06-01 --ate 2026-06-05
    python conferir.py --liga 152 --min-prob 60
    python conferir.py --detalhe             # mostra jogo a jogo
    python conferir.py --salvar relatorio.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from analysis import build_selections, dedupe_one_per_match, Selection
from bilhetes import _sort_key
from config import Settings

console = Console()

STATUS_FINALIZADO = {"finished", "after et", "after pen.", "ft", "aet"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_float(value) -> float | None:
    try:
        f = float(str(value).replace(",", "."))
        return f
    except (TypeError, ValueError):
        return None


def _to_int_score(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _is_finished(ev: dict) -> bool:
    status = str(ev.get("match_status", "")).strip().lower()
    return status in STATUS_FINALIZADO


def _resultado_1x2(gh: int, ga: int) -> str:
    if gh > ga:
        return "HOME"
    if gh < ga:
        return "AWAY"
    return "DRAW"


@dataclass
class ContagemMercado:
    """Acumula acertos/erros de um mercado."""

    nome: str
    acertos: int = 0
    total: int = 0

    def registrar(self, acertou: bool) -> None:
        self.total += 1
        if acertou:
            self.acertos += 1

    @property
    def taxa(self) -> float:
        return (self.acertos / self.total * 100.0) if self.total else 0.0


# --------------------------------------------------------------------------- #
# Avaliacao de um jogo
# --------------------------------------------------------------------------- #
def palpite_1x2(pred: dict) -> tuple[str, float] | None:
    opcoes = {
        "HOME": _to_float(pred.get("prob_HW")),
        "DRAW": _to_float(pred.get("prob_D")),
        "AWAY": _to_float(pred.get("prob_AW")),
    }
    opcoes = {k: v for k, v in opcoes.items() if v is not None}
    if not opcoes:
        return None
    escolha = max(opcoes, key=opcoes.get)
    return escolha, opcoes[escolha]


def palpite_ou25(pred: dict) -> tuple[str, float] | None:
    o = _to_float(pred.get("prob_O"))
    u = _to_float(pred.get("prob_U"))
    if o is None or u is None:
        return None
    return ("OVER", o) if o >= u else ("UNDER", u)


def palpite_btts(pred: dict) -> tuple[str, float] | None:
    sim = _to_float(pred.get("prob_bts"))
    nao = _to_float(pred.get("prob_ots"))
    if sim is None or nao is None:
        return None
    return ("YES", sim) if sim >= nao else ("NO", nao)


LABEL = {
    "HOME": "Casa", "DRAW": "Empate", "AWAY": "Fora",
    "OVER": "Over 2.5", "UNDER": "Under 2.5",
    "YES": "BTTS Sim", "NO": "BTTS Nao",
}


# --------------------------------------------------------------------------- #
# Resolucao de resultado por mercado (para o backtest de ROI)
# --------------------------------------------------------------------------- #
def selecao_acertou(market_code: str, gh: int, ga: int) -> bool | None:
    """Diz se uma selecao (codigo de mercado em analysis.MARKETS) ganhou.

    Retorna None se o mercado nao for reconhecido.
    """
    total = gh + ga
    tabela = {
        "HOME": gh > ga,
        "DRAW": gh == ga,
        "AWAY": gh < ga,
        "DC_1X": gh >= ga,
        "DC_X2": ga >= gh,
        "DC_12": gh != ga,
        "OVER15": total > 1.5,
        "UNDER15": total < 1.5,
        "OVER25": total > 2.5,
        "UNDER25": total < 2.5,
        "OVER35": total > 3.5,
        "UNDER35": total < 3.5,
        "BTS_YES": gh > 0 and ga > 0,
        "BTS_NO": not (gh > 0 and ga > 0),
    }
    return tabela.get(market_code)


@dataclass
class ResultadoSimulacao:
    """Resultado de uma estrategia no backtest de ROI."""

    nome: str
    apostas: int = 0
    ganhas: int = 0
    apostado: float = 0.0
    retornado: float = 0.0

    def registrar(self, stake: float, ganhou: bool, odd_total: float) -> None:
        self.apostas += 1
        self.apostado += stake
        if ganhou:
            self.ganhas += 1
            self.retornado += stake * odd_total

    @property
    def lucro(self) -> float:
        return self.retornado - self.apostado

    @property
    def roi(self) -> float:
        return (self.lucro / self.apostado * 100.0) if self.apostado else 0.0

    @property
    def taxa_acerto(self) -> float:
        return (self.ganhas / self.apostas * 100.0) if self.apostas else 0.0



# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Confere a taxa de acerto das previsoes da apifootball.com em datas passadas."
    )
    p.add_argument("--data", help="Data inicial YYYY-MM-DD")
    p.add_argument("--ate", help="Data final YYYY-MM-DD")
    p.add_argument(
        "--dias",
        type=int,
        default=7,
        help="Qtd de dias ate ontem, se --data nao for informada (padrao: 7)",
    )
    p.add_argument("--liga", help="Filtrar por league_id especifico")
    p.add_argument(
        "--min-prob",
        type=float,
        default=0.0,
        help="So avalia palpites com probabilidade escolhida >= este valor (%%). Padrao: 0",
    )
    p.add_argument("--detalhe", action="store_true", help="Mostra a conferencia jogo a jogo")
    p.add_argument(
        "--roi",
        action="store_true",
        help="Backtest de ROI: simula apostar (com odds reais) simples vs combinadas",
    )
    p.add_argument(
        "--estrategia",
        choices=["seguro", "valor", "equilibrado"],
        default="valor",
        help="No --roi: como escolher a melhor selecao por jogo (padrao: valor)",
    )
    p.add_argument(
        "--min-valor",
        type=float,
        help="No --roi: exige value bet (EV) >= este valor (ex.: 0.05). Padrao: sem filtro",
    )
    p.add_argument(
        "--stake", type=float, default=10.0, help="No --roi: valor por aposta/bilhete (padrao: 10)"
    )
    p.add_argument(
        "--combo-max",
        type=int,
        default=5,
        help="No --roi: maior tamanho de combinada testado (padrao: 5)",
    )
    p.add_argument("--salvar", help="Salva o relatorio em arquivo (.json)")
    return p.parse_args()


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    if args.data:
        start = date.fromisoformat(args.data)
        end = date.fromisoformat(args.ate) if args.ate else start
    else:
        end = date.today() - timedelta(days=1)  # ate ontem (jogos ja finalizados)
        start = end - timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def rodar_roi(
    predictions: list[dict],
    odds,
    resultados: dict[str, tuple[int, int]],
    args,
) -> dict:
    """Backtest de ROI com odds reais: compara aposta simples vs combinadas.

    Usa os MESMOS palpites (a melhor selecao por jogo, segundo --estrategia) nas
    duas abordagens, mudando apenas como sao agrupados. Assim a comparacao isola
    o efeito de combinar jogos.
    """
    min_value = args.min_valor  # None = sem filtro de EV
    selections = build_selections(
        predictions, odds, args.min_prob, min_value, use_fair_odds=False
    )
    # uma selecao por jogo (a melhor), ordenada pela estrategia
    pool = dedupe_one_per_match(selections)
    pool.sort(key=_sort_key(args.estrategia), reverse=True)
    # so o que tem resultado real (jogo finalizado)
    pool = [s for s in pool if s.match_id in resultados]

    if not pool:
        console.print(
            "[yellow]Sem selecoes com odds reais + resultado no periodo. "
            "Tente outro intervalo/liga, baixar --min-prob ou remover --min-valor.[/yellow]"
        )
        return {}

    stake = args.stake

    # --- Simples: cada palpite e uma aposta ---
    simples = ResultadoSimulacao("Simples (1 jogo)")
    for s in pool:
        gh, ga = resultados[s.match_id]
        ok = selecao_acertou(s.market_code, gh, ga)
        if ok is None:
            continue
        simples.registrar(stake, ok, s.odd)

    sims = [simples]

    # --- Combinadas de 2..combo_max pernas ---
    for k in range(2, max(2, args.combo_max) + 1):
        sim = ResultadoSimulacao(f"Combinada ({k} jogos)")
        # agrupa o pool em blocos sequenciais de k jogos
        for i in range(0, len(pool) - (len(pool) % k), k):
            grupo = pool[i : i + k]
            if len(grupo) < k:
                break
            odd_total = 1.0
            ganhou = True
            for s in grupo:
                gh, ga = resultados[s.match_id]
                ok = selecao_acertou(s.market_code, gh, ga)
                odd_total *= s.odd
                if not ok:
                    ganhou = False
            sim.registrar(stake, ganhou, odd_total)
        if sim.apostas > 0:
            sims.append(sim)

    # --- Tabela comparativa ---
    tabela = Table(
        title=f"Backtest de ROI com odds reais (estrategia: {args.estrategia}, stake {stake:.2f})",
        title_justify="left",
    )
    tabela.add_column("Abordagem")
    tabela.add_column("Bilhetes", justify="right")
    tabela.add_column("Acerto", justify="right")
    tabela.add_column("Apostado", justify="right")
    tabela.add_column("Retorno", justify="right")
    tabela.add_column("Lucro", justify="right")
    tabela.add_column("ROI", justify="right")
    for sim in sims:
        cor = "green" if sim.roi >= 0 else "red"
        tabela.add_row(
            sim.nome,
            str(sim.apostas),
            f"{sim.taxa_acerto:.1f}%",
            f"{sim.apostado:.2f}",
            f"{sim.retornado:.2f}",
            f"[{cor}]{sim.lucro:+.2f}[/{cor}]",
            f"[{cor}]{sim.roi:+.1f}%[/{cor}]",
        )
    console.print(tabela)
    console.print(
        "[dim]ROI = lucro / total apostado. Mesmos palpites nas duas abordagens; "
        "a unica diferenca e combinar ou nao. Resultado passado nao garante futuro.[/dim]"
    )

    return {
        "estrategia": args.estrategia,
        "min_prob": args.min_prob,
        "min_valor": args.min_valor,
        "stake": stake,
        "palpites_no_pool": len(pool),
        "abordagens": [
            {
                "nome": s.nome,
                "bilhetes": s.apostas,
                "taxa_acerto_pct": round(s.taxa_acerto, 2),
                "apostado": round(s.apostado, 2),
                "retornado": round(s.retornado, 2),
                "lucro": round(s.lucro, 2),
                "roi_pct": round(s.roi, 2),
            }
            for s in sims
        ],
    }


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]"
            + (f"  |  Liga: {args.liga}" if args.liga else "")
            + (f"  |  Min prob: {args.min_prob:.0f}%" if args.min_prob else ""),
            title="Conferencia de acertos - apifootball.com (get_predictions x resultados)",
        )
    )

    try:
        with console.status("Buscando previsoes e resultados..."):
            predictions = client.get_predictions(d_from, d_to, league_id=args.liga)
            events = client.get_events(d_from, d_to, league_id=args.liga)
            odds = client.get_odds(date_from=d_from, date_to=d_to) if args.roi else None
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not predictions:
        console.print("[yellow]Nenhuma previsao retornada para o periodo/liga.[/yellow]")
        return

    # indexa resultados reais (so finalizados) por match_id
    resultados: dict[str, tuple[int, int]] = {}
    for ev in events:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        mid = str(ev.get("match_id", "")).strip()
        if mid and gh is not None and ga is not None:
            resultados[mid] = (gh, ga)

    # --- Modo backtest de ROI (odds reais, simples vs combinadas) ---
    if args.roi:
        relatorio_roi = rodar_roi(predictions, odds, resultados, args)
        if args.salvar and relatorio_roi:
            with open(args.salvar, "w", encoding="utf-8") as fh:
                json.dump(relatorio_roi, fh, ensure_ascii=False, indent=2)
            console.print(f"[green]Relatorio salvo em {args.salvar}[/green]")
        return

    cont = {
        "1X2": ContagemMercado("1X2"),
        "OU25": ContagemMercado("Over/Under 2.5"),
        "BTTS": ContagemMercado("Ambas Marcam"),
    }
    detalhes: list[dict] = []
    jogos_avaliados = 0

    for pred in predictions:
        mid = str(pred.get("match_id", "")).strip()
        if mid not in resultados:
            continue  # sem resultado real (nao finalizado ou ausente)
        gh, ga = resultados[mid]
        jogos_avaliados += 1
        total = gh + ga
        casa = pred.get("match_hometeam_name", "Casa")
        fora = pred.get("match_awayteam_name", "Fora")
        linha = {
            "match_id": mid,
            "jogo": f"{casa} x {fora}",
            "liga": pred.get("league_name", ""),
            "data": pred.get("match_date", ""),
            "placar": f"{gh}-{ga}",
            "palpites": {},
        }

        # --- 1X2 ---
        p1x2 = palpite_1x2(pred)
        if p1x2 and p1x2[1] >= args.min_prob:
            escolha, prob = p1x2
            real = _resultado_1x2(gh, ga)
            ok = escolha == real
            cont["1X2"].registrar(ok)
            linha["palpites"]["1X2"] = {
                "palpite": LABEL[escolha], "prob": prob,
                "real": LABEL[real], "acertou": ok,
            }

        # --- Over/Under 2.5 ---
        pou = palpite_ou25(pred)
        if pou and pou[1] >= args.min_prob:
            escolha, prob = pou
            real = "OVER" if total > 2.5 else "UNDER"
            ok = escolha == real
            cont["OU25"].registrar(ok)
            linha["palpites"]["OU25"] = {
                "palpite": LABEL[escolha], "prob": prob,
                "real": LABEL[real], "acertou": ok,
            }

        # --- BTTS ---
        pbt = palpite_btts(pred)
        if pbt and pbt[1] >= args.min_prob:
            escolha, prob = pbt
            real = "YES" if (gh > 0 and ga > 0) else "NO"
            ok = escolha == real
            cont["BTTS"].registrar(ok)
            linha["palpites"]["BTTS"] = {
                "palpite": LABEL[escolha], "prob": prob,
                "real": LABEL[real], "acertou": ok,
            }

        detalhes.append(linha)

    if jogos_avaliados == 0:
        console.print(
            "[yellow]Nenhum jogo finalizado com previsao+resultado no periodo. "
            "Use datas passadas (ja jogadas) e confira se ha jogos na liga.[/yellow]"
        )
        return

    # --- Tabela resumo ---
    resumo = Table(title=f"Resumo (jogos finalizados avaliados: {jogos_avaliados})", title_justify="left")
    resumo.add_column("Mercado")
    resumo.add_column("Acertos", justify="right")
    resumo.add_column("Total", justify="right")
    resumo.add_column("Taxa de acerto", justify="right")
    for c in cont.values():
        cor = "green" if c.taxa >= 50 else "red"
        resumo.add_row(c.nome, str(c.acertos), str(c.total), f"[{cor}]{c.taxa:.1f}%[/{cor}]")
    console.print(resumo)

    if args.detalhe:
        det = Table(title="Conferencia jogo a jogo", title_justify="left", expand=True)
        det.add_column("Data", no_wrap=True)
        det.add_column("Jogo", overflow="fold")
        det.add_column("Placar", justify="center")
        det.add_column("1X2")
        det.add_column("O/U 2.5")
        det.add_column("BTTS")

        def fmt(p: dict | None) -> str:
            if not p:
                return "-"
            mark = "[green]OK[/green]" if p["acertou"] else "[red]X[/red]"
            return f"{p['palpite']} {mark}"

        for ln in detalhes:
            pal = ln["palpites"]
            det.add_row(
                ln["data"], ln["jogo"], ln["placar"],
                fmt(pal.get("1X2")), fmt(pal.get("OU25")), fmt(pal.get("BTTS")),
            )
        console.print(det)

    console.print(
        "[dim]Observacao: a 'taxa de acerto' mede quantas vezes a maior probabilidade "
        "da API bateu com o resultado real. Nao e garantia de desempenho futuro.[/dim]"
    )

    if args.salvar:
        payload = {
            "periodo": {"de": d_from, "ate": d_to},
            "liga": args.liga,
            "min_prob": args.min_prob,
            "jogos_avaliados": jogos_avaliados,
            "resumo": {
                k: {"acertos": c.acertos, "total": c.total, "taxa_pct": round(c.taxa, 2)}
                for k, c in cont.items()
            },
            "detalhes": detalhes,
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Relatorio salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
