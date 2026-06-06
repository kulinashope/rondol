"""Diagnostico do modelo: ele e ENVIESADO? Compara, por mercado, a media das
probabilidades que o modelo PREVE com a frequencia REAL no teste.

Se 'media prevista' ~ 'taxa real' -> modelo nao enviesado (eventuais 'values'
sao ruido/vig, nao bug). Se 'media prevista' >> 'taxa real' -> o modelo
superestima aquele mercado (bug/vies a corrigir).

Uso:
    python diag_modelo.py --liga 212 --treino-dias 90 --teste-dias 60
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score
from modelo import construir_forcas, prever, acertou

console = Console()
MERCADOS = ["HOME", "DRAW", "AWAY", "OVER25", "UNDER25", "BTS_YES", "BTS_NO"]
LABEL = {"HOME": "Casa (1)", "DRAW": "Empate (X)", "AWAY": "Fora (2)",
         "OVER25": "Over 2.5", "UNDER25": "Under 2.5", "BTS_YES": "BTTS Sim", "BTS_NO": "BTTS Nao"}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--liga", required=True)
    p.add_argument("--treino-dias", type=int, default=90)
    p.add_argument("--teste-dias", type=int, default=60)
    return p.parse_args()


def main():
    args = parse_args()
    client = ApiFootballClient(Settings.load())
    fim = date.today() - timedelta(days=1)
    ini_teste = fim - timedelta(days=args.teste_dias - 1)
    treino_de = (ini_teste - timedelta(days=args.treino_dias)).isoformat()
    treino_ate = (ini_teste - timedelta(days=1)).isoformat()

    console.print(Panel.fit(
        f"Liga: {args.liga}  |  Treino: {treino_de} a {treino_ate}  |  Teste: {ini_teste} a {fim}",
        title="Diagnostico de vies do modelo (previsto x real)"))

    try:
        with console.status("Treinando e testando..."):
            treino = client.get_events(treino_de, treino_ate, league_id=args.liga)
            teste = client.get_events(ini_teste.isoformat(), fim.isoformat(), league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]"); return

    forcas, mc, mf = construir_forcas(treino)
    if not forcas:
        console.print("[yellow]Treino insuficiente.[/yellow]"); return

    soma_prev = defaultdict(float)
    soma_real = defaultdict(int)
    n = 0
    for ev in teste:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        pred = prever(forcas, mc, mf, ev.get("match_hometeam_name"), ev.get("match_awayteam_name"))
        if not pred:
            continue
        n += 1
        for mkt in MERCADOS:
            soma_prev[mkt] += pred[mkt] / 100.0
            soma_real[mkt] += 1 if acertou(mkt, gh, ga) else 0

    if n == 0:
        console.print("[yellow]Sem jogos no teste.[/yellow]"); return

    t = Table(title=f"Previsto x Real ({n} jogos de teste)", title_justify="left")
    t.add_column("Mercado"); t.add_column("Media prevista", justify="right")
    t.add_column("Taxa real", justify="right"); t.add_column("Vies (prev-real)", justify="right")
    for mkt in MERCADOS:
        prev = soma_prev[mkt] / n * 100
        real = soma_real[mkt] / n * 100
        vies = prev - real
        cor = "green" if abs(vies) <= 4 else ("yellow" if abs(vies) <= 8 else "red")
        t.add_row(LABEL[mkt], f"{prev:.1f}%", f"{real:.1f}%", f"[{cor}]{vies:+.1f}pp[/{cor}]")
    console.print(t)
    console.print("[dim]Vies pequeno (verde, <=4pp) = modelo calibrado naquele mercado. "
                  "Vies grande (vermelho) = o modelo erra sistematicamente ali.[/dim]")


if __name__ == "__main__":
    main()
