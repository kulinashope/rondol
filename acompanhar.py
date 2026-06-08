"""Acompanhamento + aprendizado: confere os resultados dos picks ja enviados,
recalcula o desempenho real por mercado/liga e (opcional) posta um resumo no
Discord. Roda periodicamente (ex.: 1x ao dia, depois dos jogos).

Uso:
    python acompanhar.py                 # atualiza resultados, aprende e mostra resumo
    python acompanhar.py --discord       # tambem posta o resumo no Discord
"""
from __future__ import annotations

import argparse
import os

import requests
from rich.console import Console

from api_client import ApiFootballClient
from config import Settings
from aprendizado import atualizar_resultados, aprender, resumo_texto

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Confere resultados, aprende e resume o desempenho real.")
    p.add_argument("--discord", action="store_true", help="Posta o resumo no Discord (DISCORD_WEBHOOK_URL)")
    p.add_argument("--webhook", help="URL do webhook (senao usa DISCORD_WEBHOOK_URL)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    client = ApiFootballClient(Settings.load())

    resolvidos = atualizar_resultados(client)
    ajustes = aprender()
    texto = resumo_texto()

    console.print(f"[dim]Picks resolvidos nesta rodada: {resolvidos}[/dim]\n")
    console.print(texto.replace("**", ""))

    evit = [k for k, v in ajustes.get("mercado", {}).items() if v.get("evitar")]
    if evit:
        console.print(f"\n[yellow]Mercados que o sistema esta evitando (ROI real ruim): {', '.join(evit)}[/yellow]")

    if args.discord:
        webhook = args.webhook or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook:
            console.print("[red]Sem DISCORD_WEBHOOK_URL para postar.[/red]")
            return
        for pedaco in [texto[i:i + 1900] for i in range(0, len(texto), 1900)]:
            requests.post(webhook, json={"content": pedaco, "username": "Acompanhamento"}, timeout=30).raise_for_status()
        console.print("[green]Resumo postado no Discord.[/green]")


if __name__ == "__main__":
    main()
