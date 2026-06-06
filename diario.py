"""Estrategia 'banker do dia': 1 (ou poucas) aposta(s) simples por dia, na
selecao mais provavel, mirando um lucro pequeno (ex.: R$5-10).

Faz o backtest dia a dia com odds e resultados reais e mostra a verdade:
  - em quantos dias houve pelo menos 1 vitoria,
  - o lucro/prejuizo acumulado (stake fixo e modo "alvo de lucro"),
  - a pior sequencia de derrotas e o maior rombo (drawdown).

Tambem lista os bankers de uma data (hoje/amanha) para voce apostar.

Uso:
    python diario.py --dias 40                       # backtest dos ultimos 40 dias
    python diario.py --dias 40 --min-prob 85 --picks 1 --alvo 10
    python diario.py --data 2026-06-06 --listar       # lista os bankers do dia
    python diario.py --dias 40 --liga 152 --salvar diario.json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
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
    "HOME": "Casa (1)", "DRAW": "Empate (X)", "AWAY": "Fora (2)",
    "DC_1X": "Dupla chance 1X", "DC_X2": "Dupla chance X2", "DC_12": "Dupla chance 12",
    "OVER15": "Over 1.5", "UNDER15": "Under 1.5",
    "OVER25": "Over 2.5", "UNDER25": "Under 2.5",
    "OVER35": "Over 3.5", "UNDER35": "Under 3.5",
    "BTS_YES": "Ambas marcam: Sim", "BTS_NO": "Ambas marcam: Nao",
}


def bankers_do_dia(
    predictions: list[dict],
    odds,
    min_prob: float,
    min_odd: float,
    max_odd: float,
    picks: int,
) -> dict[str, list[Selection]]:
    """Para cada data, retorna as `picks` selecoes mais provaveis (uma por jogo)."""
    selections = build_selections(
        predictions, odds, min_prob=min_prob, min_value=None, use_fair_odds=False
    )
    # filtra faixa de odd e organiza por data
    por_data: dict[str, list[Selection]] = {}
    melhor_por_jogo: dict[str, Selection] = {}
    for s in selections:
        if not (min_odd <= s.odd <= max_odd):
            continue
        cur = melhor_por_jogo.get(s.match_id)
        if cur is None or s.model_prob > cur.model_prob:
            melhor_por_jogo[s.match_id] = s
    for s in melhor_por_jogo.values():
        d = (s.kickoff or "")[:10]
        if d:
            por_data.setdefault(d, []).append(s)
    for d in por_data:
        por_data[d].sort(key=lambda x: x.model_prob, reverse=True)
        por_data[d] = por_data[d][:picks]
    return por_data


@dataclass
class DiaResultado:
    data: str
    venceu_algum: bool
    lucro_flat: float
    lucro_alvo: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Estrategia 'banker do dia' + backtest honesto.")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD")
    p.add_argument("--ate", help="Data final YYYY-MM-DD")
    p.add_argument("--dias", type=int, default=40, help="Dias ate ontem se --data ausente (padrao: 40)")
    p.add_argument("--liga", help="Filtra por league_id")
    p.add_argument("--min-prob", type=float, default=85.0, help="Probabilidade minima do banker (%) (padrao: 85)")
    p.add_argument("--min-odd", type=float, default=1.2, help="Odd minima aceitavel (padrao: 1.2)")
    p.add_argument("--max-odd", type=float, default=2.0, help="Odd maxima aceitavel (padrao: 2.0)")
    p.add_argument("--picks", type=int, default=1, help="Quantos bankers por dia (padrao: 1)")
    p.add_argument("--stake", type=float, default=10.0, help="Stake fixo por aposta (padrao: 10)")
    p.add_argument("--alvo", type=float, default=10.0, help="Lucro-alvo por aposta no modo alvo (padrao: 10)")
    p.add_argument("--listar", action="store_true", help="So lista os bankers da data (nao faz backtest)")
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


def listar_bankers(por_data: dict[str, list[Selection]], args) -> None:
    for d in sorted(por_data):
        tab = Table(title=f"Bankers de {d} (top {args.picks} por probabilidade)", title_justify="left")
        tab.add_column("Jogo", overflow="fold")
        tab.add_column("Liga", overflow="fold")
        tab.add_column("Aposta")
        tab.add_column("Prob", justify="right")
        tab.add_column("Odd", justify="right")
        tab.add_column(f"Stake p/ +{args.alvo:.0f}", justify="right")
        for s in por_data[d]:
            stake_alvo = args.alvo / (s.odd - 1.0) if s.odd > 1.0 else 0.0
            tab.add_row(
                s.match_label, s.league, MARKET_LABEL.get(s.market_code, s.market_code),
                f"{s.model_prob:.0f}%", f"{s.odd:.2f}", f"{stake_alvo:.2f}",
            )
        console.print(tab)


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]"
            + (f"  |  Liga: {args.liga}" if args.liga else "")
            + f"\nBanker: prob >= {args.min_prob:.0f}%, odd {args.min_odd:.2f}-{args.max_odd:.2f}, "
            f"{args.picks} pick(s)/dia",
            title="Estrategia 'banker do dia' - apifootball.com",
        )
    )

    try:
        with console.status("Baixando previsoes, odds e resultados..."):
            predictions = client.get_predictions(d_from, d_to, league_id=args.liga)
            odds = client.get_odds(date_from=d_from, date_to=d_to)
            events = [] if args.listar else client.get_events(d_from, d_to, league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    por_data = bankers_do_dia(
        predictions, odds, args.min_prob, args.min_odd, args.max_odd, args.picks
    )
    if not por_data:
        console.print("[yellow]Nenhum banker no criterio. Baixe --min-prob ou amplie a faixa de odd.[/yellow]")
        return

    if args.listar:
        listar_bankers(por_data, args)
        return

    # --- Backtest dia a dia ---
    resultados: dict[str, tuple[int, int]] = {}
    for ev in events:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        mid = str(ev.get("match_id", "")).strip()
        if mid and gh is not None and ga is not None:
            resultados[mid] = (gh, ga)

    dias: list[DiaResultado] = []
    for d in sorted(por_data):
        picks = [s for s in por_data[d] if s.match_id in resultados]
        if not picks:
            continue
        venceu_algum = False
        lucro_flat = 0.0
        lucro_alvo = 0.0
        for s in picks:
            gh, ga = resultados[s.match_id]
            ok = selecao_acertou(s.market_code, gh, ga)
            if ok is None:
                continue
            # flat
            lucro_flat += (args.stake * (s.odd - 1.0)) if ok else -args.stake
            # alvo: stake dimensionado p/ ganhar exatamente 'alvo' se acertar
            stake_alvo = args.alvo / (s.odd - 1.0) if s.odd > 1.0 else 0.0
            lucro_alvo += args.alvo if ok else -stake_alvo
            venceu_algum = venceu_algum or ok
        dias.append(DiaResultado(d, venceu_algum, lucro_flat, lucro_alvo))

    if not dias:
        console.print("[yellow]Sem dias com banker + resultado no periodo.[/yellow]")
        return

    n = len(dias)
    dias_venceu = sum(1 for x in dias if x.venceu_algum)
    total_flat = sum(x.lucro_flat for x in dias)
    total_alvo = sum(x.lucro_alvo for x in dias)
    apostado_flat = n * args.picks * args.stake

    # pior sequencia de derrotas e maior rombo (no modo alvo, acumulado)
    seq = pior_seq = 0
    acum = pico = rombo = 0.0
    for x in dias:
        if not x.venceu_algum:
            seq += 1
            pior_seq = max(pior_seq, seq)
        else:
            seq = 0
        acum += x.lucro_alvo
        pico = max(pico, acum)
        rombo = min(rombo, acum - pico)

    resumo = Table(title="Resultado do backtest (banker do dia)", title_justify="left")
    resumo.add_column("Metrica")
    resumo.add_column("Valor", justify="right")
    resumo.add_row("Dias avaliados", str(n))
    resumo.add_row("Dias com >=1 vitoria", f"{dias_venceu} ({dias_venceu / n * 100:.1f}%)")
    cor_flat = "green" if total_flat >= 0 else "red"
    resumo.add_row(
        f"Lucro acumulado (stake fixo {args.stake:.0f})",
        f"[{cor_flat}]{total_flat:+.2f}[/{cor_flat}] (apostado {apostado_flat:.0f}, ROI {total_flat / apostado_flat * 100:+.1f}%)",
    )
    cor_alvo = "green" if total_alvo >= 0 else "red"
    resumo.add_row(f"Lucro acumulado (modo alvo +{args.alvo:.0f}/win)", f"[{cor_alvo}]{total_alvo:+.2f}[/{cor_alvo}]")
    resumo.add_row("Pior sequencia de dias perdendo", str(pior_seq))
    resumo.add_row("Maior rombo (drawdown, modo alvo)", f"[red]{rombo:.2f}[/red]")
    console.print(resumo)

    console.print(
        "[dim]No modo alvo, para ganhar pouco voce arrisca muito (stake = alvo/(odd-1)). "
        "Repare na 'pior sequencia' e no 'rombo': sao eles que quebram a banca, mesmo ganhando "
        "quase todo dia. ROI passado nao garante futuro.[/dim]"
    )

    if args.salvar:
        payload = {
            "periodo": {"de": d_from, "ate": d_to},
            "config": {
                "min_prob": args.min_prob, "min_odd": args.min_odd, "max_odd": args.max_odd,
                "picks": args.picks, "stake": args.stake, "alvo": args.alvo,
            },
            "dias": n,
            "dias_com_vitoria": dias_venceu,
            "taxa_dias_pct": round(dias_venceu / n * 100, 2),
            "lucro_flat": round(total_flat, 2),
            "roi_flat_pct": round(total_flat / apostado_flat * 100, 2),
            "lucro_modo_alvo": round(total_alvo, 2),
            "pior_seq_derrotas": pior_seq,
            "maior_rombo_alvo": round(rombo, 2),
            "detalhe_dias": [
                {"data": x.data, "venceu": x.venceu_algum, "lucro_flat": round(x.lucro_flat, 2),
                 "lucro_alvo": round(x.lucro_alvo, 2)}
                for x in dias
            ],
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Relatorio salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
