"""Alertas de GOL ao vivo no Discord para os jogos dos nossos picks do dia.

Fica monitorando o livescore (get_events com match_live=1) e, quando sai gol num
jogo que esta nos nossos picks de hoje, manda um alerta no Discord — dizendo se
o gol AJUDA ou ATRAPALHA o nosso palpite.

Precisa ficar rodando durante os jogos (nao e instantaneo no GitHub Actions de
agendamento simples). Rode localmente antes dos jogos, ou num host/Action longo.

Uso:
    python alertas_ao_vivo.py --horas 6 --intervalo 60
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import date

import requests

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from aprendizado import _carregar, HISTORICO

LABEL = {"UNDER25": "Under 2.5", "OVER25": "Over 2.5", "HOME": "Casa (1)",
         "AWAY": "Fora (2)", "DRAW": "Empate", "BTS_NO": "BTTS Nao", "BTS_YES": "BTTS Sim"}


def picks_de_hoje() -> dict:
    """match_id -> {jogo, codigo} dos picks pendentes de hoje."""
    hoje = date.today().isoformat()
    hist = _carregar(HISTORICO, [])
    return {str(r["match_id"]): {"jogo": r.get("jogo", ""), "codigo": r.get("codigo", "")}
            for r in hist if r.get("data") == hoje and r.get("status") == "pendente"}


def nota_pick(codigo: str, gh: int, ga: int, marcou_casa: bool) -> str:
    total = gh + ga
    if codigo == "OVER25":
        return "✅ bateu o Over 2.5!" if total > 2 else f"faltam {3 - total} gol(s) p/ Over 2.5"
    if codigo == "UNDER25":
        return "⚠️ Under 2.5 perde se sair mais gol" if total <= 2 else "❌ ja passou de 2.5 (Under era)"
    if codigo == "BTS_YES":
        return "✅ os dois marcaram (BTTS Sim)!" if gh > 0 and ga > 0 else "falta o outro time marcar"
    if codigo == "BTS_NO":
        return "❌ alguem marcou (BTTS Nao em risco)" if (gh > 0 and ga > 0) else "⚠️ BTTS Nao: nao pode os dois marcarem"
    if codigo == "HOME":
        return "✅ casa na frente" if gh > ga else ("empate" if gh == ga else "❌ casa perdendo")
    if codigo == "AWAY":
        return "✅ visitante na frente" if ga > gh else ("empate" if gh == ga else "❌ visitante perdendo")
    return ""


def parse_args():
    p = argparse.ArgumentParser(description="Alertas de gol ao vivo no Discord para os picks do dia.")
    p.add_argument("--horas", type=float, default=6.0, help="Quanto tempo monitorar (padrao: 6h)")
    p.add_argument("--intervalo", type=int, default=60, help="Segundos entre checagens (padrao: 60)")
    p.add_argument("--webhook")
    return p.parse_args()


def main():
    args = parse_args()
    webhook = args.webhook or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise SystemExit("Defina DISCORD_WEBHOOK_URL no .env ou passe --webhook.")
    client = ApiFootballClient(Settings.load())

    alvos = picks_de_hoje()
    if not alvos:
        print("Sem picks pendentes de hoje para monitorar.")
        return
    print(f"Monitorando {len(alvos)} jogo(s) dos picks de hoje...")
    placar = {}  # match_id -> (gh, ga)
    fim = time.time() + args.horas * 3600

    while time.time() < fim:
        try:
            vivos = client.get_events(date.today().isoformat(), date.today().isoformat(), match_live=True)
        except ApiFootballError:
            time.sleep(args.intervalo); continue
        for ev in vivos:
            mid = str(ev.get("match_id", ""))
            if mid not in alvos:
                continue
            try:
                gh = int(ev.get("match_hometeam_score")); ga = int(ev.get("match_awayteam_score"))
            except (TypeError, ValueError):
                continue
            ant = placar.get(mid)
            if ant is None:
                placar[mid] = (gh, ga); continue
            if (gh, ga) != ant:
                marcou_casa = gh > ant[0]
                quem = ev.get("match_hometeam_name") if marcou_casa else ev.get("match_awayteam_name")
                pk = alvos[mid]
                msg = (f"⚽🚨 **GOL!** {quem} marcou!\n"
                       f"{ev.get('match_hometeam_name')} {gh}-{ga} {ev.get('match_awayteam_name')} "
                       f"({ev.get('match_status','')}')\n"
                       f"Nosso pick: {LABEL.get(pk['codigo'], pk['codigo'])} → {nota_pick(pk['codigo'], gh, ga, marcou_casa)}")
                try:
                    requests.post(webhook, json={"content": msg, "username": "Gol ao vivo"}, timeout=20)
                except requests.RequestException:
                    pass
                placar[mid] = (gh, ga)
        time.sleep(args.intervalo)
    print("Monitoramento encerrado.")


if __name__ == "__main__":
    main()
