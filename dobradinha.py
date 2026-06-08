"""Dobradinha do dia: 2 jogos de jogos diferentes, mirando uma odd combinada
(padrao ~2.0), escolhendo o PAR MAIS PROVAVEL possivel dentro da faixa de odd.

Prioriza probabilidade (acerto), nao value. Envia ao Discord se houver webhook.
Registra no historico para o acompanhamento conferir depois.

Uso:
    python dobradinha.py --alvo-odd 2.0 --stake 10
    python dobradinha.py --liga 99 --alvo-odd 1.8
    python dobradinha.py --enviar           # posta no Discord (DISCORD_WEBHOOK_URL)
"""
from __future__ import annotations

import argparse
import os
from datetime import date
from itertools import combinations

import requests

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from pick_do_dia import gerar_picks
from aprendizado import registrar_picks


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dobradinha do dia (2 jogos, par mais provavel perto da odd-alvo).")
    p.add_argument("--data", help="Data YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--liga", help="league_id (vazio = todas)")
    p.add_argument("--treino-dias", type=int, default=90)
    p.add_argument("--alvo-odd", type=float, default=2.0, help="Odd combinada desejada (padrao: 2.0)")
    p.add_argument("--tol", type=float, default=0.2, help="Tolerancia da odd combinada (0.2 = +-20%)")
    p.add_argument("--min-prob", type=float, default=58.0, help="Prob minima de cada perna (%)")
    p.add_argument("--so-alta", action="store_true", help="So usa picks de confianca Alta")
    p.add_argument("--stake", type=float, default=10.0)
    p.add_argument("--enviar", action="store_true", help="Posta no Discord")
    p.add_argument("--webhook")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    client = ApiFootballClient(Settings.load())
    alvo = args.data or date.today().isoformat()

    try:
        d, cand = gerar_picks(client, args.liga, alvo, args.treino_dias, args.min_prob,
                              min_edge=-1.0, min_odd=1.2, max_odd=2.0,
                              perfil=False, desfalques=False)
    except ApiFootballError as exc:
        print(f"Erro: {exc}")
        return

    if args.so_alta:
        cand = [c for c in cand if c["nota"] == "Alta"]
    # um pick por jogo (o mais provavel), perna com odd <= alvo (senao par estoura)
    melhor_por_jogo = {}
    for c in cand:
        mid = c["match_id"]
        if c["odd"] > args.alvo_odd * 1.2:
            continue
        if mid not in melhor_por_jogo or c["nossa_prob"] > melhor_por_jogo[mid]["nossa_prob"]:
            melhor_por_jogo[mid] = c
    pool = list(melhor_por_jogo.values())

    low = args.alvo_odd * (1 - args.tol)
    high = args.alvo_odd * (1 + args.tol)

    melhor_par = None
    melhor_prob = -1.0
    for a, b in combinations(pool, 2):
        odd_comb = a["odd"] * b["odd"]
        if not (low <= odd_comb <= high):
            continue
        prob_comb = (a["nossa_prob"] / 100) * (b["nossa_prob"] / 100)
        if prob_comb > melhor_prob:
            melhor_prob = prob_comb
            melhor_par = (a, b, odd_comb)

    d_str = d.isoformat() if hasattr(d, "isoformat") else str(d)
    if not melhor_par:
        msg = (f"📅 {d_str}: nao achei hoje um par de jogos provaveis que feche odd "
               f"~{args.alvo_odd:.1f}. Dia ruim para dobradinha — o disciplinado e nao forcar.")
        print(msg)
        if args.enviar:
            wh = args.webhook or os.getenv("DISCORD_WEBHOOK_URL", "")
            if wh:
                requests.post(wh, json={"content": msg, "username": "Dobradinha"}, timeout=30)
        return

    a, b, odd_comb = melhor_par
    retorno = args.stake * odd_comb
    prob_pct = melhor_prob * 100
    msg = (
        f"🎟️ **DOBRADINHA do dia — {d_str}**\n\n"
        f"⚽ **1. {a['aposta']}** — {a['jogo']}\n"
        f"   {a['liga']} | {a['hora']} | odd {a['odd']:.2f} ({a['casa']}) | nossa prob {a['nossa_prob']:.0f}%\n"
        f"⚽ **2. {b['aposta']}** — {b['jogo']}\n"
        f"   {b['liga']} | {b['hora']} | odd {b['odd']:.2f} ({b['casa']}) | nossa prob {b['nossa_prob']:.0f}%\n\n"
        f"📈 Odd combinada: **{odd_comb:.2f}** | R${args.stake:.0f} → R${retorno:.2f}\n"
        f"🎯 Chance REAL das duas baterem (nosso modelo): **{prob_pct:.0f}%**\n"
        f"_Honesto: odd ~{odd_comb:.1f} = ~{100/odd_comb:.0f}% pelo mercado. "
        f"Nao e 'quase nunca erra'. Stake pequeno, lazer._"
    )
    print(msg)
    registrar_picks(d_str, [a, b])
    if args.enviar:
        wh = args.webhook or os.getenv("DISCORD_WEBHOOK_URL", "")
        if wh:
            requests.post(wh, json={"content": msg, "username": "Dobradinha"}, timeout=30).raise_for_status()
            print("ENVIADO")


if __name__ == "__main__":
    main()
