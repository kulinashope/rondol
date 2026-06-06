"""Busca de NICHOS com valor (EV+) nas previsoes da apifootball.com.

Ideia: de milhares de jogos, nao queremos 50 bilhetes — queremos achar o
pequeno subconjunto (mercado + faixa de probabilidade + liga) onde a API tem
vantagem real contra as odds. O script:

  1. Baixa previsoes + resultados + odds reais de um periodo.
  2. Trata CADA selecao (cada mercado de cada jogo) como uma aposta simples,
     com a melhor odd real disponivel e o resultado de verdade.
  3. Segmenta por mercado, por liga e por mercado x faixa de probabilidade.
  4. Calcula ROI, tamanho de amostra e significancia (t-stat) de cada segmento.
  5. Faz validacao FORA DA AMOSTRA (treino/teste por data): acha nichos
     positivos no treino e mede se eles continuam positivos no teste.

So sobrevivem nichos que dao lucro no treino E no teste, com amostra suficiente.
Isso e o que separa vantagem real de sorte (data mining).

Uso:
    python nichos.py --dias 40
    python nichos.py --data 2026-04-25 --ate 2026-06-04 --min-n 80
    python nichos.py --dias 40 --liga 152 --salvar nichos.json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from analysis import build_selections, Selection
from config import Settings
from conferir import selecao_acertou, _is_finished, _to_int_score

console = Console()

MARKET_LABEL = {
    "HOME": "1 (Casa)", "DRAW": "X (Empate)", "AWAY": "2 (Fora)",
    "DC_1X": "DC 1X", "DC_X2": "DC X2", "DC_12": "DC 12",
    "OVER15": "Over 1.5", "UNDER15": "Under 1.5",
    "OVER25": "Over 2.5", "UNDER25": "Under 2.5",
    "OVER35": "Over 3.5", "UNDER35": "Under 3.5",
    "BTS_YES": "BTTS Sim", "BTS_NO": "BTTS Nao",
}


def prob_bucket(p: float) -> str:
    lo = int(p // 10 * 10)
    lo = max(0, min(90, lo))
    return f"{lo}-{lo + 10}%"


@dataclass
class Aposta:
    """Uma selecao ja resolvida (ganhou/perdeu) com lucro por unidade de stake."""

    data: str
    league: str
    market_code: str
    prob: float
    odd: float
    ganhou: bool

    @property
    def lucro_unit(self) -> float:
        return (self.odd - 1.0) if self.ganhou else -1.0


@dataclass
class Segmento:
    chave: str
    apostas: list[float] = field(default_factory=list)  # lucros por unidade
    acertos: int = 0

    def add(self, lucro_unit: float, ganhou: bool) -> None:
        self.apostas.append(lucro_unit)
        if ganhou:
            self.acertos += 1

    @property
    def n(self) -> int:
        return len(self.apostas)

    @property
    def roi(self) -> float:
        return (sum(self.apostas) / self.n * 100.0) if self.n else 0.0

    @property
    def taxa(self) -> float:
        return (self.acertos / self.n * 100.0) if self.n else 0.0

    @property
    def t_stat(self) -> float:
        """t = media / erro-padrao. |t|>~2 sugere que nao e so ruido."""
        n = self.n
        if n < 2:
            return 0.0
        media = sum(self.apostas) / n
        var = sum((x - media) ** 2 for x in self.apostas) / (n - 1)
        se = math.sqrt(var / n)
        return (media / se) if se > 0 else 0.0


def agrupar(apostas: list[Aposta], chave_fn) -> dict[str, Segmento]:
    segs: dict[str, Segmento] = {}
    for a in apostas:
        k = chave_fn(a)
        segs.setdefault(k, Segmento(k)).add(a.lucro_unit, a.ganhou)
    return segs


def tabela_segmentos(titulo: str, segs: dict[str, Segmento], min_n: int, top: int = 15) -> Table:
    t = Table(title=titulo, title_justify="left")
    t.add_column("Segmento")
    t.add_column("N", justify="right")
    t.add_column("Acerto", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("t-stat", justify="right")
    elegiveis = [s for s in segs.values() if s.n >= min_n]
    elegiveis.sort(key=lambda s: s.roi, reverse=True)
    for s in elegiveis[:top]:
        cor = "green" if s.roi >= 0 else "red"
        forte = "bold " if abs(s.t_stat) >= 2 else ""
        t.add_row(
            s.chave, str(s.n), f"{s.taxa:.1f}%",
            f"[{forte}{cor}]{s.roi:+.1f}%[/{forte}{cor}]",
            f"{s.t_stat:+.2f}",
        )
    if not elegiveis:
        t.add_row(f"(nenhum com N>={min_n})", "-", "-", "-", "-")
    return t


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Busca nichos com EV+ e valida fora da amostra.")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD")
    p.add_argument("--ate", help="Data final YYYY-MM-DD")
    p.add_argument("--dias", type=int, default=40, help="Dias ate ontem se --data ausente (padrao: 40)")
    p.add_argument("--liga", help="Filtra por league_id")
    p.add_argument("--min-prob", type=float, default=0.0, help="Probabilidade minima da selecao (%)")
    p.add_argument("--min-n", type=int, default=50, help="Amostra minima por segmento (padrao: 50)")
    p.add_argument("--min-roi", type=float, default=3.0, help="ROI%% minimo no treino p/ candidato (padrao: 3)")
    p.add_argument("--salvar", help="Salva o relatorio em .json")
    return p.parse_args()


def resolve_dates(args) -> tuple[str, str]:
    if args.data:
        start = date.fromisoformat(args.data)
        end = date.fromisoformat(args.ate) if args.ate else start
    else:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def montar_apostas(predictions, odds, resultados) -> list[Aposta]:
    """Cada mercado de cada jogo finalizado vira uma aposta simples resolvida."""
    selections: list[Selection] = build_selections(
        predictions, odds, min_prob=0.0, min_value=None, use_fair_odds=False
    )
    apostas: list[Aposta] = []
    for s in selections:
        if s.match_id not in resultados:
            continue
        gh, ga = resultados[s.match_id]
        ok = selecao_acertou(s.market_code, gh, ga)
        if ok is None:
            continue
        data = (s.kickoff or "")[:10]
        apostas.append(
            Aposta(
                data=data, league=s.league, market_code=s.market_code,
                prob=s.model_prob, odd=s.odd, ganhou=ok,
            )
        )
    return apostas


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]"
            + (f"  |  Liga: {args.liga}" if args.liga else "")
            + f"  |  Min N: {args.min_n}  |  Min ROI treino: {args.min_roi:.0f}%",
            title="Busca de nichos (EV+) - apifootball.com",
        )
    )

    try:
        with console.status("Baixando previsoes, resultados e odds (janelas de 5 dias)..."):
            predictions = client.get_predictions(d_from, d_to, league_id=args.liga)
            events = client.get_events(d_from, d_to, league_id=args.liga)
            odds = client.get_odds(date_from=d_from, date_to=d_to)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    resultados: dict[str, tuple[int, int]] = {}
    for ev in events:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        mid = str(ev.get("match_id", "")).strip()
        if mid and gh is not None and ga is not None:
            resultados[mid] = (gh, ga)

    apostas = montar_apostas(predictions, odds, resultados)
    apostas = [a for a in apostas if a.prob >= args.min_prob and a.data]
    if not apostas:
        console.print("[yellow]Sem apostas com odds reais + resultado no periodo.[/yellow]")
        return

    console.print(f"[dim]Total de selecoes resolvidas (com odd real): {len(apostas)}[/dim]\n")

    # --- Visao geral por mercado e por mercado x faixa de probabilidade ---
    por_mercado = agrupar(apostas, lambda a: MARKET_LABEL.get(a.market_code, a.market_code))
    por_merc_prob = agrupar(
        apostas, lambda a: f"{MARKET_LABEL.get(a.market_code, a.market_code)} | {prob_bucket(a.prob)}"
    )
    console.print(tabela_segmentos("Por mercado (ordenado por ROI)", por_mercado, args.min_n))
    console.print(tabela_segmentos("Por mercado x faixa de prob (top 15 ROI)", por_merc_prob, args.min_n))

    # --- Validacao fora da amostra (treino = metade antiga, teste = metade nova) ---
    datas = sorted({a.data for a in apostas})
    if len(datas) >= 4:
        corte = datas[len(datas) // 2]
        treino = [a for a in apostas if a.data < corte]
        teste = [a for a in apostas if a.data >= corte]

        segs_treino = agrupar(
            treino, lambda a: f"{MARKET_LABEL.get(a.market_code, a.market_code)} | {prob_bucket(a.prob)}"
        )
        segs_teste = agrupar(
            teste, lambda a: f"{MARKET_LABEL.get(a.market_code, a.market_code)} | {prob_bucket(a.prob)}"
        )

        # candidatos: positivos e com amostra/significancia no TREINO
        candidatos = [
            s for s in segs_treino.values()
            if s.n >= args.min_n and s.roi >= args.min_roi and s.t_stat >= 1.0
        ]
        candidatos.sort(key=lambda s: s.roi, reverse=True)

        val = Table(
            title=f"Validacao fora da amostra (treino < {corte} <= teste)",
            title_justify="left",
        )
        val.add_column("Nicho (mercado | prob)")
        val.add_column("ROI treino", justify="right")
        val.add_column("N tr", justify="right")
        val.add_column("ROI teste", justify="right")
        val.add_column("N te", justify="right")
        val.add_column("Veredito")

        sobreviventes = []
        for s in candidatos[:20]:
            st = segs_teste.get(s.chave)
            if st is None or st.n < max(10, args.min_n // 3):
                veredito = "[yellow]sem amostra no teste[/yellow]"
                roi_te, n_te = (st.roi if st else 0.0), (st.n if st else 0)
            elif st.roi > 0:
                veredito = "[green]passou (lucro nos dois)[/green]"
                roi_te, n_te = st.roi, st.n
                sobreviventes.append((s, st))
            else:
                veredito = "[red]falhou (era sorte)[/red]"
                roi_te, n_te = st.roi, st.n
            cor_tr = "green" if s.roi >= 0 else "red"
            cor_te = "green" if roi_te >= 0 else "red"
            val.add_row(
                s.chave,
                f"[{cor_tr}]{s.roi:+.1f}%[/{cor_tr}]", str(s.n),
                f"[{cor_te}]{roi_te:+.1f}%[/{cor_te}]", str(n_te),
                veredito,
            )
        console.print(val)

        if sobreviventes:
            console.print(
                f"\n[bold green]{len(sobreviventes)} nicho(s) deram lucro no treino E no teste.[/bold green] "
                "[dim]Candidatos reais — ainda assim, valide ao vivo com stake pequeno antes de confiar.[/dim]"
            )
        else:
            console.print(
                "\n[bold]Nenhum nicho deu lucro consistente nos dois periodos.[/bold] "
                "[dim]Os ROIs positivos do treino sumiram no teste — eram variancia, nao vantagem.[/dim]"
            )
    else:
        console.print("[yellow]Periodo curto demais para treino/teste. Use mais --dias.[/yellow]")
        sobreviventes = []

    if args.salvar:
        payload = {
            "periodo": {"de": d_from, "ate": d_to},
            "liga": args.liga,
            "min_n": args.min_n,
            "total_selecoes": len(apostas),
            "por_mercado": {
                k: {"n": s.n, "taxa_pct": round(s.taxa, 2), "roi_pct": round(s.roi, 2), "t": round(s.t_stat, 2)}
                for k, s in por_mercado.items()
            },
            "por_mercado_prob": {
                k: {"n": s.n, "taxa_pct": round(s.taxa, 2), "roi_pct": round(s.roi, 2), "t": round(s.t_stat, 2)}
                for k, s in por_merc_prob.items() if s.n >= args.min_n
            },
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Relatorio salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
