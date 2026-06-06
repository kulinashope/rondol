"""Ajuste por DESFALQUES: rebaixa a forca de ataque de um time conforme a fatia
de gols que esta de fora (lesionados) no momento.

Usa get_teams (que traz, por jogador, `player_goals` e `player_injured`). Para
cada time calcula:

    fatia_fora = soma(gols dos jogadores lesionados) / soma(gols do elenco)
    multiplicador_ataque = max(0.55, 1 - fatia_fora)

Ex.: se os lesionados representam 30% dos gols do time, o ataque cai ~30%. Isso
alimenta o modelo Poisson (modelo.py) para corrigir a previsao quando falta
artilheiro/jogador decisivo.

LIMITACAO HONESTA: a API informa quem esta lesionado AGORA, nao quem estava numa
data passada. Por isso este ajuste serve para previsao do DIA (pra frente), e NAO
pode ser usado em backtest (seria look-ahead bias / trapaca com o futuro).

Uso:
    python desfalques.py --liga 99                 # tabela de desfalques por time
    python desfalques.py --liga 99 --salvar desf.json
"""
from __future__ import annotations

import argparse
import json

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from api_client import ApiFootballClient, ApiFootballError
from config import Settings

console = Console()


def _int(v) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def multiplicadores_ataque(client: ApiFootballClient, league_id: str) -> dict[str, dict]:
    """Retorna {nome_time: {mult, fatia_fora, lesionados:[(nome,gols)], top:(nome,gols)}}."""
    times = client.raw("get_teams", league_id=league_id)
    res: dict[str, dict] = {}
    if not isinstance(times, list):
        return res
    for t in times:
        nome = t.get("team_name")
        jogadores = t.get("players") or []
        total = sum(_int(p.get("player_goals")) for p in jogadores)
        lesionados = [
            (p.get("player_name"), _int(p.get("player_goals")))
            for p in jogadores
            if str(p.get("player_injured", "")).lower() == "yes"
        ]
        gols_fora = sum(g for _, g in lesionados)
        fatia = (gols_fora / total) if total > 0 else 0.0
        mult = max(0.55, 1.0 - fatia)
        top = max(((p.get("player_name"), _int(p.get("player_goals"))) for p in jogadores),
                  key=lambda x: x[1], default=("-", 0))
        res[nome] = {
            "mult": round(mult, 3),
            "fatia_fora": round(fatia * 100, 1),
            "lesionados": sorted([l for l in lesionados if l[1] > 0], key=lambda x: -x[1]),
            "top": top,
            "gols_time": total,
        }
    return res


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ajuste por desfalques (lesionados x gols) por time.")
    p.add_argument("--liga", required=True, help="league_id")
    p.add_argument("--salvar")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)

    console.print(Panel.fit(f"Liga: {args.liga}", title="Desfalques por time (impacto no ataque)"))
    try:
        with console.status("Buscando elencos e lesoes..."):
            mult = multiplicadores_ataque(client, args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not mult:
        console.print("[yellow]Sem dados de elenco para essa liga.[/yellow]")
        return

    tab = Table(title="Times ordenados por impacto dos desfalques no ataque", title_justify="left")
    tab.add_column("Time", overflow="fold")
    tab.add_column("Gols time", justify="right")
    tab.add_column("Artilheiro", overflow="fold")
    tab.add_column("Lesionados (gols)", overflow="fold")
    tab.add_column("Gols fora", justify="right")
    tab.add_column("Mult. ataque", justify="right")
    for nome, d in sorted(mult.items(), key=lambda kv: kv[1]["fatia_fora"], reverse=True):
        les = ", ".join(f"{n} ({g})" for n, g in d["lesionados"][:3]) or "-"
        cor = "red" if d["fatia_fora"] >= 20 else ("yellow" if d["fatia_fora"] >= 8 else "green")
        tab.add_row(
            nome, str(d["gols_time"]), f"{d['top'][0]} ({d['top'][1]})", les,
            f"[{cor}]{d['fatia_fora']:.0f}%[/{cor}]", f"{d['mult']:.2f}",
        )
    console.print(tab)
    console.print(
        "[dim]Mult. ataque < 1 = time desfalcado (ataque rebaixado no modelo). "
        "Use com modelo.py p/ previsao do DIA. Nao serve para backtest (lesao e snapshot atual).[/dim]"
    )

    if args.salvar:
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(mult, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
