"""Loop de aprendizado: registra os picks, confere os resultados e aprende ao
longo do tempo em quais mercados/ligas o sistema realmente acerta AO VIVO.

Arquivos (criados na pasta do projeto):
  historico_picks.json  -> todos os picks ja enviados (com resultado quando sai)
  ajustes_mercado.json  -> o que o sistema aprendeu (mercados/ligas a evitar)

Fluxo:
  1. registrar_picks(data, picks)      -> grava os picks do dia (status pendente)
  2. atualizar_resultados(client)      -> resolve os pendentes ja jogados
  3. aprender()                        -> recalcula ROI por mercado/liga e o que evitar
  4. carregar_ajustes()                -> usado pelo gerar_picks para nao repetir erro

Regra de aprendizado (conservadora p/ nao reagir a ruido):
  so 'evita' um mercado (ou liga+mercado) quando ha amostra suficiente AO VIVO
  (>= MIN_N apostas) E o ROI real ficou abaixo de MIN_ROI. Assim ele aprende com
  evidencia, nao com 2-3 azares.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from conferir import _is_finished, _to_int_score
from modelo import acertou

HISTORICO = "historico_picks.json"
AJUSTES = "ajustes_mercado.json"

MIN_N = 30          # amostra minima ao vivo para o sistema 'aprender' a evitar
MIN_ROI = -5.0      # se ROI real < -5% com amostra suficiente -> evitar


def _carregar(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def _salvar(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def registrar_picks(data: str, picks: list[dict]) -> int:
    """Grava os picks do dia (evita duplicar por data+match_id+codigo)."""
    hist = _carregar(HISTORICO, [])
    existentes = {(r["data"], r["match_id"], r["codigo"]) for r in hist}
    novos = 0
    for p in picks:
        chave = (data, p.get("match_id", ""), p.get("codigo", ""))
        if chave in existentes or not p.get("match_id"):
            continue
        hist.append({
            "data": data, "match_id": p["match_id"], "jogo": p.get("jogo", ""),
            "liga": p.get("liga", ""), "codigo": p.get("codigo", ""),
            "aposta": p.get("aposta", ""), "odd": p.get("odd", 0),
            "nota": p.get("nota", ""), "prob": p.get("nossa_prob", 0),
            "status": "pendente", "lucro_unit": None,
        })
        existentes.add(chave)
        novos += 1
    _salvar(HISTORICO, hist)
    return novos


def atualizar_resultados(client) -> list[dict]:
    """Resolve os picks pendentes cujos jogos ja terminaram. Retorna os resolvidos agora."""
    hist = _carregar(HISTORICO, [])
    pendentes_por_data = defaultdict(list)
    for r in hist:
        if r["status"] == "pendente":
            pendentes_por_data[r["data"]].append(r)
    if not pendentes_por_data:
        return []

    resolvidos = []
    for data, regs in pendentes_por_data.items():
        try:
            eventos = client.get_events(data, data)
        except Exception:
            continue
        resultados = {}
        for ev in eventos:
            if not _is_finished(ev):
                continue
            gh = _to_int_score(ev.get("match_hometeam_score"))
            ga = _to_int_score(ev.get("match_awayteam_score"))
            mid = str(ev.get("match_id", "")).strip()
            if mid and gh is not None and ga is not None:
                resultados[mid] = (gh, ga)
        for r in regs:
            res = resultados.get(str(r["match_id"]))
            if not res:
                continue
            gh, ga = res
            ok = acertou(r["codigo"], gh, ga)
            r["status"] = "green" if ok else "red"
            r["placar"] = f"{gh}-{ga}"
            r["lucro_unit"] = (r["odd"] - 1.0) if ok else -1.0
            resolvidos.append(r)
    _salvar(HISTORICO, hist)
    return resolvidos


def resumo_jogos_texto(resolvidos: list[dict]) -> str:
    """Mensagem com o resultado (GREEN/RED) de cada pick resolvido agora."""
    if not resolvidos:
        return ""
    linhas = ["✅❌ **Resultados dos picks** (conferidos agora):", ""]
    for r in resolvidos:
        emoji = "✅ GREEN" if r["status"] == "green" else "❌ RED"
        linhas.append(f"{emoji} — {r['aposta']} | {r['jogo']} {r.get('placar','')} (odd {r['odd']})")
    greens = sum(1 for r in resolvidos if r["status"] == "green")
    linhas.append("")
    linhas.append(f"Parcial de hoje: {greens}/{len(resolvidos)} green.")
    return "\n".join(linhas)


def aprender() -> dict:
    """Recalcula ROI real por mercado e por liga+mercado e marca o que evitar."""
    hist = _carregar(HISTORICO, [])
    resolvidos = [r for r in hist if r["status"] in ("green", "red") and r["lucro_unit"] is not None]

    por_mercado = defaultdict(list)
    por_liga_mercado = defaultdict(list)
    for r in resolvidos:
        por_mercado[r["codigo"]].append(r["lucro_unit"])
        por_liga_mercado[f"{r['liga']}|{r['codigo']}"].append(r["lucro_unit"])

    def stats(lucros):
        n = len(lucros)
        roi = (sum(lucros) / n * 100.0) if n else 0.0
        greens = sum(1 for x in lucros if x > 0)
        return {"n": n, "acerto_pct": round(greens / n * 100, 1) if n else 0,
                "roi_pct": round(roi, 1), "evitar": n >= MIN_N and roi < MIN_ROI}

    ajustes = {
        "mercado": {k: stats(v) for k, v in por_mercado.items()},
        "liga_mercado": {k: stats(v) for k, v in por_liga_mercado.items()},
        "total": stats([r["lucro_unit"] for r in resolvidos]),
    }
    _salvar(AJUSTES, ajustes)
    return ajustes


def carregar_ajustes() -> dict:
    """Para o gerar_picks: conjuntos de mercados e liga|mercado a evitar."""
    aj = _carregar(AJUSTES, {})
    evitar_mercado = {k for k, v in aj.get("mercado", {}).items() if v.get("evitar")}
    evitar_liga_mercado = {k for k, v in aj.get("liga_mercado", {}).items() if v.get("evitar")}
    return {"evitar_mercado": evitar_mercado, "evitar_liga_mercado": evitar_liga_mercado}


def resumo_texto() -> str:
    """Resumo do desempenho real acumulado (para Discord/console)."""
    aj = _carregar(AJUSTES, {})
    if not aj or not aj.get("total", {}).get("n"):
        return "Ainda sem picks resolvidos suficientes para um resumo."
    t = aj["total"]
    linhas = [f"📊 **Desempenho acumulado (picks reais)** — {t['n']} apostas",
              f"Acerto: {t['acerto_pct']}% | ROI: {t['roi_pct']:+.1f}%", "",
              "**Por mercado:**"]
    for m, s in sorted(aj.get("mercado", {}).items(), key=lambda kv: kv[1]["roi_pct"], reverse=True):
        flag = " ⛔(evitando)" if s["evitar"] else ""
        linhas.append(f"• {m}: {s['n']} apostas | acerto {s['acerto_pct']}% | ROI {s['roi_pct']:+.1f}%{flag}")
    evitando = [k for k, v in aj.get("liga_mercado", {}).items() if v.get("evitar")]
    if evitando:
        linhas.append("")
        linhas.append("**Liga+mercado que o sistema aprendeu a evitar:** " + ", ".join(evitando[:10]))
    return "\n".join(linhas)
