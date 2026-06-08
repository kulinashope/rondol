"""Envia os picks do dia para um canal do Discord via WEBHOOK (gratis, sem bot).

Como obter o webhook (1 min, sem custo):
  Discord -> Configuracoes do canal -> Integracoes -> Webhooks -> Novo webhook
  -> Copiar URL do webhook. Cole no .env como DISCORD_WEBHOOK_URL=...

Uso:
    python enviar_discord.py --liga 41 --min-edge 0.05 --so-alta
    python enviar_discord.py --liga 99 --desfalques --perfil

Roda o mesmo motor do pick_do_dia e posta a mensagem formatada no Discord.
Pensado para rodar automatico (ex.: GitHub Actions, ver README).
"""
from __future__ import annotations

import argparse
import os
from datetime import date

import requests

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from pick_do_dia import gerar_picks

EMOJI_NOTA = {"Alta": "🟢", "Media": "🟡", "Baixa": "🔴", "sem dados": "⚪"}


def montar_mensagem(alvo, candidatos, top: int, so_alta: bool) -> str:
    if so_alta:
        candidatos = [c for c in candidatos if c["nota"] == "Alta"]
    candidatos = candidatos[:top]
    if not candidatos:
        return (f"📅 **Picks {alvo}**\n"
                "Nenhum pick com confiança suficiente hoje. "
                "O disciplinado é não apostar. 🤝")

    linhas = [f"📅 **Picks do dia — {alvo}**", ""]
    for i, c in enumerate(candidatos, 1):
        emoji = EMOJI_NOTA.get(c["nota"], "⚪")
        linhas.append(
            f"{emoji} **{i}. {c['aposta']}** — {c['jogo']}\n"
            f"   🏆 {c['liga']} | ⏰ {c['hora']} | 💰 odd {c['odd']:.2f} ({c['casa']})\n"
            f"   📊 prob {c['nossa_prob']:.0f}% | value {c['value']:+.1%} | confiança: {c['nota']}"
        )
    linhas.append("")
    linhas.append("_Aposta simples, stake pequeno. Prob calibrada; não é garantia. "
                  "Confirme a odd na casa. Aposte com responsabilidade._")
    return "\n".join(linhas)


def enviar(webhook: str, conteudo: str) -> int:
    # Discord limita a 2000 caracteres por mensagem
    for pedaco in [conteudo[i:i + 1900] for i in range(0, len(conteudo), 1900)]:
        r = requests.post(webhook, json={"content": pedaco, "username": "Picks do Dia"}, timeout=30)
        r.raise_for_status()
    return 200


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Envia picks do dia ao Discord (webhook).")
    p.add_argument("--liga", help="league_id especifico; VAZIO = todas as ligas do dia (auto)")
    p.add_argument("--data", help="Data alvo YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--treino-dias", type=int, default=150)
    p.add_argument("--min-prob", type=float, default=55.0)
    p.add_argument("--min-edge", type=float, default=0.05)
    p.add_argument("--min-odd", type=float, default=1.3)
    p.add_argument("--max-odd", type=float, default=4.0)
    p.add_argument("--desfalques", action="store_true")
    p.add_argument("--perfil", action="store_true")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--so-alta", action="store_true", help="Envia apenas picks de confianca Alta")
    p.add_argument("--webhook", help="URL do webhook (senao usa DISCORD_WEBHOOK_URL do ambiente)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    webhook = args.webhook or os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        raise SystemExit("ERRO: defina DISCORD_WEBHOOK_URL no .env ou passe --webhook.")

    client = ApiFootballClient(Settings.load())
    try:
        alvo, candidatos = gerar_picks(
            client, args.liga, args.data, args.treino_dias, args.min_prob,
            args.min_edge, args.min_odd, args.max_odd, args.perfil, args.desfalques,
        )
    except ApiFootballError as exc:
        # avisa no Discord que houve erro de API (ex.: sem jogos)
        enviar(webhook, f"⚠️ Picks {args.data or date.today()}: erro ao buscar dados ({exc}).")
        return

    msg = montar_mensagem(alvo, candidatos, args.top, args.so_alta)
    enviar(webhook, msg)
    # registra os picks enviados para o loop de aprendizado
    try:
        from aprendizado import registrar_picks
        enviados = [c for c in candidatos if (not args.so_alta or c["nota"] == "Alta")][:args.top]
        n_reg = registrar_picks(alvo.isoformat(), enviados)
        print(f"Registrados {n_reg} pick(s) no historico.")
    except Exception as exc:
        print(f"Aviso: nao registrou no historico: {exc}")
    print(f"Enviado ao Discord: {len([c for c in candidatos if not args.so_alta or c['nota']=='Alta'])} pick(s).")


if __name__ == "__main__":
    main()
