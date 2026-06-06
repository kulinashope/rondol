"""Perfil estatistico das ligas a partir dos dados da apifootball.com.

Cada liga tem uma tendencia: umas sao de muitos gols (boas p/ Over/BTTS), outras
de poucos gols (boas p/ Under). Em vez de confiar em blog, este script calcula a
taxa real de cada mercado por liga usando o historico de resultados (get_events):

  - Over 2.5 % e Under 2.5 %
  - BTTS % (ambas marcam)
  - Vitoria do mandante % (forca do fator casa)
  - Media de gols por jogo

Assim voce escolhe o LADO certo conforme a liga (ex.: Brasileirao tende a Under;
ligas nordicas tendem a Over/BTTS). Lembre: taxa-base alta NAO e lucro garantido
— a odd ja embute isso. Serve para nao remar contra a mare da liga.

Uso:
    python perfil_ligas.py --dias 21 --min-jogos 30
    python perfil_ligas.py --dias 21 --ordenar under
    python perfil_ligas.py --liga 99 --dias 60        # perfil de uma liga so
    python perfil_ligas.py --dias 21 --salvar perfil.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score

console = Console()


@dataclass
class PerfilLiga:
    nome: str
    n: int = 0
    over25: int = 0
    btts: int = 0
    casa: int = 0
    gols: int = 0

    def add(self, gh: int, ga: int) -> None:
        self.n += 1
        t = gh + ga
        self.gols += t
        if t > 2.5:
            self.over25 += 1
        if gh > 0 and ga > 0:
            self.btts += 1
        if gh > ga:
            self.casa += 1

    @property
    def pct_over(self) -> float:
        return self.over25 / self.n * 100 if self.n else 0

    @property
    def pct_under(self) -> float:
        return 100 - self.pct_over

    @property
    def pct_btts(self) -> float:
        return self.btts / self.n * 100 if self.n else 0

    @property
    def pct_casa(self) -> float:
        return self.casa / self.n * 100 if self.n else 0

    @property
    def media_gols(self) -> float:
        return self.gols / self.n if self.n else 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Perfil estatistico das ligas (Over/Under/BTTS/Casa).")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD")
    p.add_argument("--ate", help="Data final YYYY-MM-DD")
    p.add_argument("--dias", type=int, default=21, help="Dias ate ontem se --data ausente (padrao: 21)")
    p.add_argument("--liga", help="Perfil de uma liga so (league_id)")
    p.add_argument("--min-jogos", type=int, default=30, help="Min de jogos p/ a liga entrar no ranking (padrao: 30)")
    p.add_argument("--ordenar", choices=["over", "under", "btts", "casa", "gols"], default="over",
                   help="Criterio de ordenacao do ranking (padrao: over)")
    p.add_argument("--top", type=int, default=15, help="Quantas ligas mostrar por ranking (padrao: 15)")
    p.add_argument("--salvar", help="Salva em .json")
    return p.parse_args()


def resolve_dates(args) -> tuple[str, str]:
    if args.data:
        start = date.fromisoformat(args.data)
        end = date.fromisoformat(args.ate) if args.ate else start
    else:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def ranking(perfis: list[PerfilLiga], criterio: str, top: int) -> Table:
    chave = {
        "over": lambda p: p.pct_over, "under": lambda p: p.pct_under,
        "btts": lambda p: p.pct_btts, "casa": lambda p: p.pct_casa,
        "gols": lambda p: p.media_gols,
    }[criterio]
    titulo = {
        "over": "Mais Over 2.5", "under": "Mais Under 2.5", "btts": "Mais BTTS",
        "casa": "Mais vitoria do mandante", "gols": "Mais gols/jogo",
    }[criterio]
    t = Table(title=f"Ligas: {titulo}", title_justify="left")
    t.add_column("Liga", overflow="fold")
    t.add_column("Jogos", justify="right")
    t.add_column("Over", justify="right")
    t.add_column("Under", justify="right")
    t.add_column("BTTS", justify="right")
    t.add_column("Casa", justify="right")
    t.add_column("Gols/j", justify="right")
    for p in sorted(perfis, key=chave, reverse=True)[:top]:
        t.add_row(p.nome, str(p.n), f"{p.pct_over:.0f}%", f"{p.pct_under:.0f}%",
                  f"{p.pct_btts:.0f}%", f"{p.pct_casa:.0f}%", f"{p.media_gols:.2f}")
    return t


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]"
            + (f"  |  Liga: {args.liga}" if args.liga else "  |  Todas as ligas")
            + f"  |  Min jogos: {args.min_jogos}",
            title="Perfil estatistico das ligas - apifootball.com",
        )
    )

    try:
        with console.status("Baixando resultados (janelas de 5 dias)..."):
            eventos = client.get_events(d_from, d_to, league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    perfis: dict[str, PerfilLiga] = {}
    for ev in eventos:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        nome = f"{ev.get('country_name', '')} - {ev.get('league_name', '?')}".strip(" -")
        perfis.setdefault(nome, PerfilLiga(nome)).add(gh, ga)

    elegiveis = [p for p in perfis.values() if p.n >= args.min_jogos]
    if not elegiveis:
        console.print(f"[yellow]Nenhuma liga com >= {args.min_jogos} jogos no periodo. "
                      "Aumente --dias ou baixe --min-jogos.[/yellow]")
        return

    if args.liga:
        # perfil unico
        p = max(elegiveis, key=lambda x: x.n)
        t = Table(title=f"Perfil: {p.nome} ({p.n} jogos)", title_justify="left")
        t.add_column("Mercado"); t.add_column("Taxa", justify="right")
        t.add_row("Over 2.5", f"{p.pct_over:.0f}%")
        t.add_row("Under 2.5", f"{p.pct_under:.0f}%")
        t.add_row("BTTS (ambas marcam)", f"{p.pct_btts:.0f}%")
        t.add_row("Vitoria do mandante", f"{p.pct_casa:.0f}%")
        t.add_row("Media de gols/jogo", f"{p.media_gols:.2f}")
        console.print(t)
    else:
        console.print(ranking(elegiveis, args.ordenar, args.top))
        # mostra tambem o oposto util
        if args.ordenar == "over":
            console.print(ranking(elegiveis, "under", args.top))
            console.print(ranking(elegiveis, "btts", args.top))

    console.print(
        "[dim]Taxa-base ALTA nao e lucro: a odd ja precifica isso. Serve para escolher "
        "o LADO certo conforme a liga (nao apostar Over numa liga de Under, p.ex.) e, junto "
        "com o modelo e a odd, procurar value. Nada aqui e garantia.[/dim]"
    )

    if args.salvar:
        payload = {
            "periodo": {"de": d_from, "ate": d_to},
            "ligas": [
                {"liga": p.nome, "jogos": p.n, "over25_pct": round(p.pct_over, 1),
                 "under25_pct": round(p.pct_under, 1), "btts_pct": round(p.pct_btts, 1),
                 "casa_pct": round(p.pct_casa, 1), "media_gols": round(p.media_gols, 2)}
                for p in sorted(elegiveis, key=lambda x: x.n, reverse=True)
            ],
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
