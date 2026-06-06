"""Gerador de bilhetes de apostas (uso pessoal) com dados da apifootball.com.

Exemplos de uso:
    python main.py                          # bilhetes de hoje, estrategia equilibrada
    python main.py --dias 2 --estrategia valor
    python main.py --liga 152 --selecoes 4 --bilhetes 3
    python main.py --simples                # bilhetes simples (1 aposta cada)
    python main.py --raw predictions        # inspeciona o JSON cru das previsoes
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from analysis import build_selections
from api_client import ApiFootballClient, ApiFootballError
from bilhetes import Bilhete, montar_bilhetes, montar_bilhetes_odd_alvo
from config import DEFAULT_MIN_PROB, DEFAULT_MIN_VALUE, Settings

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gera bilhetes de apostas a partir de previsoes e odds da apifootball.com"
    )
    p.add_argument("--data", help="Data inicial YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--ate", help="Data final YYYY-MM-DD (padrao: data inicial)")
    p.add_argument(
        "--dias",
        type=int,
        default=1,
        help="Qtd de dias a partir da data inicial (padrao: 1)",
    )
    p.add_argument("--liga", help="Filtrar por league_id especifico")
    p.add_argument(
        "--estrategia",
        choices=["seguro", "valor", "equilibrado"],
        default="equilibrado",
        help="Criterio de priorizacao das selecoes",
    )
    p.add_argument(
        "--selecoes",
        type=int,
        default=3,
        help="Selecoes por bilhete (combinada). Padrao: 3",
    )
    p.add_argument(
        "--simples",
        action="store_true",
        help="Gera bilhetes simples (1 aposta por bilhete)",
    )
    p.add_argument(
        "--bilhetes", type=int, default=5, help="Maximo de bilhetes (padrao: 5)"
    )
    p.add_argument(
        "--odd-alvo",
        type=float,
        help="Monta combinada(s) misturando jogos ate atingir esta odd total (ex.: 2.0)",
    )
    p.add_argument(
        "--tolerancia",
        type=float,
        default=0.15,
        help="Faixa aceitavel em torno da --odd-alvo (0.15 = +-15%%). Padrao: 0.15",
    )
    p.add_argument(
        "--max-selecoes",
        type=int,
        default=6,
        help="Teto de selecoes por bilhete no modo --odd-alvo (padrao: 6)",
    )
    p.add_argument(
        "--min-selecoes",
        type=int,
        default=2,
        help="Minimo de jogos por bilhete no modo --odd-alvo (padrao: 2)",
    )
    p.add_argument(
        "--min-prob",
        type=float,
        default=DEFAULT_MIN_PROB,
        help=f"Probabilidade minima do modelo em %% (padrao: {DEFAULT_MIN_PROB})",
    )
    p.add_argument(
        "--min-valor",
        type=float,
        default=DEFAULT_MIN_VALUE,
        help=f"Value bet (EV) minimo (padrao: {DEFAULT_MIN_VALUE})",
    )
    p.add_argument(
        "--sem-valor",
        action="store_true",
        help="Ignora o filtro de value bet (considera so a confianca)",
    )
    p.add_argument(
        "--sem-odds",
        action="store_true",
        help="Nao usa odds: monta bilhetes so com a previsao (odd justa = 100/prob)",
    )
    p.add_argument(
        "--stake",
        type=float,
        default=10.0,
        help="Valor apostado por bilhete para calcular o retorno (padrao: 10)",
    )
    p.add_argument("--salvar", help="Salva os bilhetes em arquivo (.txt ou .json)")
    p.add_argument(
        "--raw",
        choices=["events", "predictions", "odds"],
        help="Imprime o JSON cru de um endpoint (depuracao) e sai",
    )
    return p.parse_args()


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    start = date.fromisoformat(args.data) if args.data else date.today()
    if args.ate:
        end = date.fromisoformat(args.ate)
    else:
        end = start + timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def do_raw(client: ApiFootballClient, args: argparse.Namespace, d_from: str, d_to: str) -> None:
    if args.raw == "events":
        data = client.raw("get_events", **{"from": d_from, "to": d_to}, league_id=args.liga)
    elif args.raw == "predictions":
        data = client.raw("get_predictions", **{"from": d_from, "to": d_to}, league_id=args.liga)
    else:
        data = client.raw("get_odds", **{"from": d_from, "to": d_to})
    console.print_json(json.dumps(data[:3] if isinstance(data, list) else data, ensure_ascii=False))
    if isinstance(data, list):
        console.print(f"\n[dim]Total de registros: {len(data)} (mostrando ate 3)[/dim]")


def render_bilhete(idx: int, b: Bilhete, stake: float) -> Table:
    titulo = (
        f"Bilhete #{idx} - {b.tipo.upper()}  |  "
        f"Odd total: [bold]{b.odd_total:.2f}[/bold]  |  "
        f"Prob. modelo: {b.prob_total:.1f}%  |  "
        f"Valor (EV): {b.valor:+.2%}  |  "
        f"Stake {stake:.2f} -> Retorno [green]{b.retorno(stake):.2f}[/green]"
    )
    table = Table(title=titulo, title_justify="left", expand=True, show_lines=False)
    table.add_column("Jogo", overflow="fold")
    table.add_column("Liga", overflow="fold")
    table.add_column("Quando", no_wrap=True)
    table.add_column("Aposta")
    table.add_column("Odd", justify="right")
    table.add_column("Prob", justify="right")
    table.add_column("Valor", justify="right")
    table.add_column("Casa", overflow="fold")
    for s in b.selections:
        table.add_row(
            s.match_label,
            s.league,
            s.kickoff,
            s.market_label,
            f"{s.odd:.2f}",
            f"{s.model_prob:.0f}%",
            f"{s.value:+.1%}",
            s.bookmaker or "-",
        )
    return table


def bilhete_to_dict(b: Bilhete, stake: float) -> dict:
    return {
        "tipo": b.tipo,
        "odd_total": b.odd_total,
        "prob_total_pct": b.prob_total,
        "valor_ev": b.valor,
        "stake": stake,
        "retorno": b.retorno(stake),
        "selecoes": [
            {
                "jogo": s.match_label,
                "liga": s.league,
                "quando": s.kickoff,
                "aposta": s.market_label,
                "odd": s.odd,
                "prob_modelo_pct": s.model_prob,
                "prob_implicita_pct": s.implied_prob,
                "valor_ev": s.value,
                "bookmaker": s.bookmaker,
            }
            for s in b.selections
        ],
    }


def salvar(path: str, bilhetes: list[Bilhete], stake: float) -> None:
    if path.endswith(".json"):
        payload = [bilhete_to_dict(b, stake) for b in bilhetes]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    else:
        lines: list[str] = []
        for i, b in enumerate(bilhetes, 1):
            lines.append(
                f"== Bilhete #{i} ({b.tipo}) | Odd {b.odd_total:.2f} | "
                f"Prob {b.prob_total:.1f}% | EV {b.valor:+.2%} | "
                f"Retorno {b.retorno(stake):.2f} (stake {stake:.2f}) =="
            )
            for s in b.selections:
                lines.append(
                    f"  - {s.match_label} [{s.league}] {s.kickoff} | "
                    f"{s.market_label} @ {s.odd:.2f} "
                    f"(prob {s.model_prob:.0f}%, EV {s.value:+.1%}, casa {s.bookmaker or '-'})"
                )
            lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    console.print(f"[green]Salvo em {path}[/green]")


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]"
            + (f"  |  Liga: {args.liga}" if args.liga else "")
            + f"\nEstrategia: [bold]{args.estrategia}[/bold]  |  "
            f"Min prob: {args.min_prob:.0f}%  |  "
            + (
                "Modo: [bold]sem odds (odd justa)[/bold]"
                if args.sem_odds
                else f"Min valor: {'(desligado)' if args.sem_valor else f'{args.min_valor:+.2%}'}"
            ),
            title="Gerador de Bilhetes - apifootball.com",
        )
    )

    try:
        if args.raw:
            do_raw(client, args, d_from, d_to)
            return

        with console.status("Buscando previsoes..."):
            predictions = client.get_predictions(d_from, d_to, league_id=args.liga)
        if not predictions:
            console.print(
                "[yellow]Nenhuma previsao retornada para o periodo/liga. "
                "Verifique se ha jogos na data ou tente outra liga.[/yellow]"
            )
            return

        if args.sem_odds:
            odds = None
            selections = build_selections(
                predictions, None, args.min_prob, None, use_fair_odds=True
            )
        else:
            with console.status("Buscando odds..."):
                odds = client.get_odds(date_from=d_from, date_to=d_to)
            min_value = None if args.sem_valor else args.min_valor
            selections = build_selections(predictions, odds, args.min_prob, min_value)

        if not selections:
            console.print(
                "[yellow]Nenhuma selecao passou nos filtros. "
                "Tente baixar --min-prob, usar --sem-valor/--sem-odds ou reduzir --min-valor.[/yellow]"
            )
            return

        if args.odd_alvo:
            bilhetes = montar_bilhetes_odd_alvo(
                selections,
                odd_alvo=args.odd_alvo,
                tolerancia=args.tolerancia,
                estrategia=args.estrategia,
                max_selecoes=args.max_selecoes,
                min_selecoes=args.min_selecoes,
                max_bilhetes=args.bilhetes,
            )
        else:
            bilhetes = montar_bilhetes(
                selections,
                estrategia=args.estrategia,
                selecoes_por_bilhete=1 if args.simples else args.selecoes,
                max_bilhetes=args.bilhetes,
            )

        if not bilhetes:
            if args.odd_alvo:
                console.print(
                    f"[yellow]Nao consegui montar combinada perto de odd "
                    f"{args.odd_alvo:.2f}. Tente aumentar --tolerancia, subir "
                    f"--max-selecoes, baixar --min-prob ou usar --sem-odds.[/yellow]"
                )
            else:
                console.print("[yellow]Selecoes insuficientes para montar bilhetes.[/yellow]")
            return

        console.print(
            f"\n[bold]{len(bilhetes)} bilhete(s) gerado(s)[/bold] "
            f"a partir de {len(selections)} selecao(oes) analisada(s).\n"
        )
        for i, b in enumerate(bilhetes, 1):
            console.print(render_bilhete(i, b, args.stake))
            console.print()

        console.print(
            "[dim]Aviso: previsoes e value bets sao estimativas estatisticas, "
            "nao garantias. Aposte com responsabilidade.[/dim]"
        )

        if args.salvar:
            salvar(args.salvar, bilhetes, args.stake)

    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")


if __name__ == "__main__":
    main()
