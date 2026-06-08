"""Confere os picks que o PROGRAMA teria gerado nos ultimos dias contra os
resultados reais. Mede acerto e ROI dos NOSSOS palpites (nota Alta), out-of-sample.

Para cada dia, treina o modelo SO com dados anteriores aquele dia (sem espiar o
futuro), gera os picks como a automacao faria (--so-alta) e confere com o placar
real. Mostra dia a dia e o total.

Uso:
    python conferir_picks.py --dias 3
    python conferir_picks.py --dias 3 --liga 41 --min-edge 0.05 --stake 10
    python conferir_picks.py --data 2026-06-04 --ate 2026-06-06 --so-alta
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score
from modelo import acertou
from pick_do_dia import gerar_picks

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Confere os picks do programa nos ultimos dias vs resultado real.")
    p.add_argument("--dias", type=int, default=3, help="Quantos dias (ate ontem) conferir (padrao: 3)")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD (sobrepoe --dias)")
    p.add_argument("--ate", help="Data final YYYY-MM-DD")
    p.add_argument("--liga", help="league_id especifico; vazio = todas as ligas")
    p.add_argument("--treino-dias", type=int, default=90)
    p.add_argument("--min-prob", type=float, default=55.0)
    p.add_argument("--min-edge", type=float, default=0.05)
    p.add_argument("--min-odd", type=float, default=1.3)
    p.add_argument("--max-odd", type=float, default=3.5)
    p.add_argument("--top", type=int, default=8, help="Picks por dia (como a automacao)")
    p.add_argument("--so-alta", action="store_true", default=True, help="So picks de confianca Alta (padrao)")
    p.add_argument("--todas-notas", action="store_true", help="Considera todas as notas (nao so Alta)")
    p.add_argument("--stake", type=float, default=10.0)
    return p.parse_args()


def resolve_dias(args) -> list[str]:
    if args.data:
        ini = date.fromisoformat(args.data)
        fim = date.fromisoformat(args.ate) if args.ate else ini
    else:
        fim = date.today() - timedelta(days=1)
        ini = fim - timedelta(days=max(1, args.dias) - 1)
    out = []
    d = ini
    while d <= fim:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def main() -> None:
    args = parse_args()
    client = ApiFootballClient(Settings.load())
    so_alta = args.so_alta and not args.todas_notas
    dias = resolve_dias(args)

    console.print(Panel.fit(
        f"Liga: {args.liga or '(todas)'}  |  Dias: {dias[0]} a {dias[-1]}  |  "
        f"{'so nota Alta' if so_alta else 'todas as notas'}  |  stake {args.stake:.0f}",
        title="Conferencia dos NOSSOS picks vs resultado real",
    ))

    tab = Table(title="Picks conferidos (jogo a jogo)", title_justify="left")
    tab.add_column("Data"); tab.add_column("Jogo", overflow="fold")
    tab.add_column("Aposta"); tab.add_column("Odd", justify="right")
    tab.add_column("Nota"); tab.add_column("Placar", justify="center")
    tab.add_column("Resultado")

    tot_apostas = tot_ganhas = 0
    tot_apostado = tot_retorno = 0.0

    for dia in dias:
        try:
            alvo, candidatos = gerar_picks(
                client, args.liga, dia, args.treino_dias, args.min_prob,
                args.min_edge, args.min_odd, args.max_odd, perfil=False, desfalques=False,
                incluir_finalizados=True,
            )
            eventos = client.get_events(dia, dia, league_id=args.liga)
        except ApiFootballError as exc:
            tab.add_row(dia, f"[red]erro: {exc}[/red]", "", "", "", "", "")
            continue

        if so_alta:
            candidatos = [c for c in candidatos if c["nota"] == "Alta"]
        candidatos = candidatos[:args.top]

        resultados = {}
        for ev in eventos:
            if not _is_finished(ev):
                continue
            gh = _to_int_score(ev.get("match_hometeam_score"))
            ga = _to_int_score(ev.get("match_awayteam_score"))
            mid = str(ev.get("match_id", "")).strip()
            if mid and gh is not None and ga is not None:
                resultados[mid] = (gh, ga)

        algum = False
        for c in candidatos:
            mid = c["match_id"]
            if mid not in resultados:
                continue  # jogo sem placar (adiado/sem dado)
            algum = True
            gh, ga = resultados[mid]
            ok = acertou(c["codigo"], gh, ga)
            tot_apostas += 1
            tot_apostado += args.stake
            if ok:
                tot_ganhas += 1
                tot_retorno += args.stake * c["odd"]
            res = "[green]GREEN[/green]" if ok else "[red]RED[/red]"
            tab.add_row(dia, c["jogo"], c["aposta"], f"{c['odd']:.2f}",
                        c["nota"], f"{gh}-{ga}", res)
        if not algum:
            tab.add_row(dia, "[dim](sem picks com resultado)[/dim]", "", "", "", "", "")

    console.print(tab)

    if tot_apostas == 0:
        console.print("[yellow]Nenhum pick com resultado no periodo. Talvez sem jogos/odds nesses dias.[/yellow]")
        return

    lucro = tot_retorno - tot_apostado
    roi = lucro / tot_apostado * 100
    resumo = Table(title="Resumo dos nossos picks", title_justify="left")
    resumo.add_column("Metrica"); resumo.add_column("Valor", justify="right")
    resumo.add_row("Apostas conferidas", str(tot_apostas))
    resumo.add_row("Acertos", f"{tot_ganhas} ({tot_ganhas/tot_apostas*100:.1f}%)")
    resumo.add_row("Apostado", f"{tot_apostado:.2f}")
    resumo.add_row("Retorno", f"{tot_retorno:.2f}")
    cor = "green" if lucro >= 0 else "red"
    resumo.add_row("Lucro", f"[{cor}]{lucro:+.2f}[/{cor}]")
    resumo.add_row("ROI", f"[{cor}]{roi:+.1f}%[/{cor}]")
    console.print(resumo)
    console.print("[dim]Amostra de poucos dias tem variancia enorme: 1-2 jogos mudam o ROI. "
                  "So muitos dias mostram a verdade. ROI passado nao garante futuro.[/dim]")


if __name__ == "__main__":
    main()
