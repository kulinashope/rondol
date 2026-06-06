"""Value betting: combina NOSSO modelo (Poisson) com as ODDS reais para achar
apostas com valor (EV+). Esta e a abordagem honesta dos profissionais: nao tenta
"cravar o dia", e sim apostar apenas quando a casa paga MAIS do que o preco justo
que o nosso modelo calcula.

Para cada jogo:
    nossa_prob (modelo Poisson treinado no historico da liga)
    odd_mercado (melhor odd entre bookmakers)
    value (EV) = (nossa_prob/100) * odd_mercado - 1
Listamos apenas quando value >= --min-edge (ex.: 0.05 = 5% de valor esperado).

Modos:
    # apostas de valor de uma data:
    python value.py --liga 99 --data 2026-06-07 --treino-dias 150 --min-edge 0.05
    # backtest honesto: as apostas de valor realmente lucraram no teste?
    python value.py --liga 99 --treino-dias 150 --teste-dias 40 --backtest

Rode com --liga (o modelo so faz sentido dentro da liga).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score
from modelo import construir_forcas, prever, acertou, MERC_LABEL

console = Console()

# nosso mercado -> chave da odd no get_odds
ODD_KEY = {
    "HOME": "odd_1", "DRAW": "odd_x", "AWAY": "odd_2",
    "OVER25": "o+2.5", "UNDER25": "u+2.5",
    "BTS_YES": "bts_yes", "BTS_NO": "bts_no",
}


def _to_float(v):
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def melhor_odd(regs: list[dict], chave: str) -> tuple[float, str]:
    melhor, bk = 0.0, ""
    for r in regs:
        o = _to_float(r.get(chave))
        if o is not None and o > melhor:
            melhor, bk = o, str(r.get("odd_bookmakers") or "?")
    return melhor, bk


def indexar_odds(odds) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = defaultdict(list)
    for r in (odds if isinstance(odds, list) else []):
        if isinstance(r, dict):
            mid = str(r.get("match_id", "")).strip()
            if mid:
                idx[mid].append(r)
    return idx


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Value betting: nosso modelo x odds reais.")
    p.add_argument("--liga", help="league_id (recomendado)")
    p.add_argument("--data", help="Data alvo YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--treino-dias", type=int, default=150, help="Dias de historico p/ treinar (padrao: 150)")
    p.add_argument("--min-edge", type=float, default=0.05, help="Value (EV) minimo p/ listar (0.05 = 5%)")
    p.add_argument("--min-prob", type=float, default=50.0, help="Prob minima do nosso modelo (%)")
    p.add_argument("--min-odd", type=float, default=1.3, help="Odd minima (evita favoritos sem retorno)")
    p.add_argument("--max-odd", type=float, default=6.0, help="Odd maxima (evita zebras instaveis)")
    p.add_argument("--backtest", action="store_true", help="Mede ROI das apostas de valor no teste")
    p.add_argument("--teste-dias", type=int, default=40, help="No backtest: dias de teste (padrao: 40)")
    p.add_argument("--stake", type=float, default=10.0, help="Stake por aposta no backtest")
    p.add_argument("--salvar", help="Salva em .json")
    return p.parse_args()


def treinar_por_liga(eventos: list[dict]) -> dict:
    """Agrupa por league_id e treina um modelo de forcas por liga."""
    por_liga: dict[str, list[dict]] = defaultdict(list)
    for ev in eventos:
        por_liga[str(ev.get("league_id", ""))].append(ev)
    modelos = {}
    for lid, jogos in por_liga.items():
        forcas, mc, mf = construir_forcas(jogos)
        if forcas:
            modelos[lid] = (forcas, mc, mf)
    return modelos


def value_de_jogo(modelos, ev, regs, args) -> list[dict]:
    lid = str(ev.get("league_id", ""))
    if lid not in modelos:
        return []
    forcas, mc, mf = modelos[lid]
    pred = prever(forcas, mc, mf, ev.get("match_hometeam_name"), ev.get("match_awayteam_name"))
    if not pred:
        return []
    achados = []
    for mercado, chave in ODD_KEY.items():
        nossa_prob = pred[mercado]
        if nossa_prob < args.min_prob:
            continue
        odd, bk = melhor_odd(regs, chave)
        if odd <= 0 or not (args.min_odd <= odd <= args.max_odd):
            continue
        value = (nossa_prob / 100.0) * odd - 1.0
        if value >= args.min_edge:
            achados.append({
                "mercado": mercado, "label": MERC_LABEL[mercado],
                "nossa_prob": round(nossa_prob, 1), "odd": odd, "bookmaker": bk,
                "value": round(value, 4),
            })
    return achados


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    if args.backtest:
        backtest(client, args)
        return

    alvo = date.fromisoformat(args.data) if args.data else date.today()
    treino_de = (alvo - timedelta(days=args.treino_dias)).isoformat()
    treino_ate = (alvo - timedelta(days=1)).isoformat()

    console.print(Panel.fit(
        f"Liga: {args.liga or '(todas)'}  |  Treino: {treino_de} a {treino_ate}  |  "
        f"Alvo: {alvo}  |  Min value: {args.min_edge:+.0%}",
        title="Value betting (nosso modelo x odds) - apifootball.com",
    ))

    try:
        with console.status("Treinando e baixando jogos/odds do dia..."):
            treino = client.get_events(treino_de, treino_ate, league_id=args.liga)
            fixtures = client.get_events(alvo.isoformat(), alvo.isoformat(), league_id=args.liga)
            odds = client.get_odds(date_from=alvo.isoformat(), date_to=alvo.isoformat())
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    modelos = treinar_por_liga(treino)
    odds_idx = indexar_odds(odds)
    if not modelos:
        console.print("[yellow]Treino insuficiente. Aumente --treino-dias.[/yellow]")
        return

    linhas = []
    for ev in fixtures:
        if _is_finished(ev):
            continue
        regs = odds_idx.get(str(ev.get("match_id", "")), [])
        if not regs:
            continue
        for a in value_de_jogo(modelos, ev, regs, args):
            linhas.append({
                "jogo": f"{ev.get('match_hometeam_name')} x {ev.get('match_awayteam_name')}",
                "hora": ev.get("match_time", ""), **a,
            })
    linhas.sort(key=lambda x: x["value"], reverse=True)

    if not linhas:
        console.print("[yellow]Nenhuma aposta de valor encontrada hoje nesse criterio. "
                      "Isso e normal e ate saudavel: value e raro. Tente outra data/liga ou baixe --min-edge.[/yellow]")
        return

    tab = Table(title=f"Apostas de VALOR para {alvo} (nossa prob x odd da casa)", title_justify="left")
    tab.add_column("Jogo", overflow="fold"); tab.add_column("Hora")
    tab.add_column("Aposta"); tab.add_column("Nossa prob", justify="right")
    tab.add_column("Odd", justify="right"); tab.add_column("Casa")
    tab.add_column("Value", justify="right")
    for l in linhas:
        tab.add_row(l["jogo"], l["hora"], l["label"], f"{l['nossa_prob']:.0f}%",
                    f"{l['odd']:.2f}", l["bookmaker"], f"[green]{l['value']:+.1%}[/green]")
    console.print(tab)
    console.print(
        "[dim]Value = (nossa_prob x odd) - 1. Positivo = a casa paga mais que o nosso preco justo. "
        "Aposte SIMPLES e com stake pequeno; rode --backtest p/ ver se o value realmente lucrou. "
        "Confirme a odd na casa antes (elas mudam). Nada disso e garantia.[/dim]"
    )

    if args.salvar:
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump({"data": alvo.isoformat(), "liga": args.liga, "apostas": linhas}, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


def backtest(client: ApiFootballClient, args) -> None:
    fim = date.today() - timedelta(days=1)
    ini_teste = fim - timedelta(days=max(1, args.teste_dias) - 1)
    treino_de = (ini_teste - timedelta(days=args.treino_dias)).isoformat()
    treino_ate = (ini_teste - timedelta(days=1)).isoformat()

    console.print(Panel.fit(
        f"Liga: {args.liga or '(todas)'}  |  Treino ate {treino_ate}  |  "
        f"Teste: {ini_teste} a {fim}  |  Min value: {args.min_edge:+.0%}",
        title="Value betting - backtest honesto",
    ))
    try:
        with console.status("Baixando treino, teste e odds..."):
            treino = client.get_events(treino_de, treino_ate, league_id=args.liga)
            teste = client.get_events(ini_teste.isoformat(), fim.isoformat(), league_id=args.liga)
            odds = client.get_odds(date_from=ini_teste.isoformat(), date_to=fim.isoformat())
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    modelos = treinar_por_liga(treino)
    odds_idx = indexar_odds(odds)
    if not modelos:
        console.print("[yellow]Treino insuficiente.[/yellow]")
        return

    n = ganhas = 0
    apostado = retornado = 0.0
    for ev in teste:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        regs = odds_idx.get(str(ev.get("match_id", "")), [])
        if not regs:
            continue
        for a in value_de_jogo(modelos, ev, regs, args):
            n += 1
            apostado += args.stake
            if acertou(a["mercado"], gh, ga):
                ganhas += 1
                retornado += args.stake * a["odd"]

    if n == 0:
        console.print("[yellow]Nenhuma aposta de valor no teste (value e raro). Baixe --min-edge ou amplie o periodo.[/yellow]")
        return

    lucro = retornado - apostado
    roi = lucro / apostado * 100
    tab = Table(title="Resultado do backtest de value betting", title_justify="left")
    tab.add_column("Metrica"); tab.add_column("Valor", justify="right")
    tab.add_row("Apostas de valor feitas", str(n))
    tab.add_row("Acerto", f"{ganhas} ({ganhas / n * 100:.1f}%)")
    tab.add_row("Apostado", f"{apostado:.2f}")
    tab.add_row("Retornado", f"{retornado:.2f}")
    cor = "green" if lucro >= 0 else "red"
    tab.add_row("Lucro", f"[{cor}]{lucro:+.2f}[/{cor}]")
    tab.add_row("ROI", f"[{cor}]{roi:+.1f}%[/{cor}]")
    console.print(tab)
    console.print(
        "[dim]Se o ROI deu negativo, o 'value' que o modelo viu era ilusao (modelo pior que o mercado). "
        "Se deu positivo, ainda valide ao vivo: a odd usada aqui e um retrato e pode nao existir na hora.[/dim]"
    )


if __name__ == "__main__":
    main()
