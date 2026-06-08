"""PICK DO DIA - junta tudo num comando so.

Pipeline:
  1. Treina o NOSSO modelo (Poisson) por liga com o historico (modelo.py).
  2. (Opcional) Ajusta o ataque pelos desfalques atuais (desfalques.py).
  3. Calcula a tendencia de cada liga (Over/Under/BTTS/Casa) com o historico.
  4. Pega as odds reais do dia e calcula o VALUE = (nossa_prob * odd) - 1.
  5. (Opcional) Exige que o palpite esteja a favor da tendencia da liga (--perfil).
  6. Entrega as melhores apostas de valor do dia, ranqueadas.

Uso:
    python pick_do_dia.py --liga 99 --data 2026-06-08 --desfalques --perfil
    python pick_do_dia.py --data 2026-06-08 --min-edge 0.05 --top 5
    python pick_do_dia.py --liga 99 --data 2026-06-08 --salvar pick.json

DICA: --liga deixa tudo melhor (modelo e desfalques sao por liga). Sem --liga,
roda em todas as ligas do dia, mas sem o ajuste de desfalques.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score
from modelo import construir_forcas, prever, MERC_LABEL, acertou
from value import melhor_odd, indexar_odds, ODD_KEY

console = Console()

# para o filtro de perfil: a qual tendencia da liga cada mercado pertence
PERFIL_MERCADO = {
    "OVER25": "over", "UNDER25": "under", "BTS_YES": "btts",
    "HOME": "casa", "DRAW": None, "AWAY": None, "BTS_NO": None,
}

# ligas/competicoes pouco previsiveis: amistosos (times testam, placar aleatorio)
# e categorias de base/juniores. O modelo de historico nao modela isso bem.
LIGAS_NAO_CONFIAVEIS = (
    "friendl", "amist", "u20", "u21", "u23", "u19", "u18", "u17",
    "youth", "sub-20", "sub-21", "sub20", "sub21", "juvenil", "junior",
)


def liga_confiavel(nome: str) -> bool:
    n = (nome or "").lower()
    return not any(p in n for p in LIGAS_NAO_CONFIAVEIS)


def calibrar_liga(jogos: list[dict]) -> dict:
    """Auto-calibracao: separa um teste interno e mede o vies do modelo por
    mercado (media prevista - taxa real). Retorna {mercado: {bias_pp, n}}.

    Permite corrigir a probabilidade e dar nota de confianca por mercado/liga.
    """
    nao_reg = {"after et", "after pen.", "aet"}
    fin = []
    for j in jogos:
        if not _is_finished(j):
            continue
        if str(j.get("match_status", "")).strip().lower() in nao_reg:
            continue
        gh = _to_int_score(j.get("match_hometeam_score"))
        ga = _to_int_score(j.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        fin.append((j.get("match_date", ""), j, gh, ga))
    if len(fin) < 40:
        return {}
    fin.sort(key=lambda x: x[0])
    corte = int(len(fin) * 0.7)
    sub_tr = [x[1] for x in fin[:corte]]
    sub_te = fin[corte:]
    forcas, mc, mf = construir_forcas(sub_tr)
    if not forcas:
        return {}
    soma_prev = defaultdict(float)
    soma_real = defaultdict(int)
    n = 0
    for _, j, gh, ga in sub_te:
        pred = prever(forcas, mc, mf, j.get("match_hometeam_name"), j.get("match_awayteam_name"))
        if not pred:
            continue
        n += 1
        for mkt in ODD_KEY:
            soma_prev[mkt] += pred[mkt] / 100.0
            soma_real[mkt] += 1 if acertou(mkt, gh, ga) else 0
    if n < 10:
        return {}
    return {mkt: {"bias": (soma_prev[mkt] / n - soma_real[mkt] / n) * 100, "n": n} for mkt in ODD_KEY}


def nota_confianca(bias: float | None, n: int, mercado: str | None = None) -> str:
    if bias is None:
        return "sem dados"
    ab = abs(bias)
    # mercados onde o modelo e historicamente mais fraco (Under/BTTS): exige
    # vies menor e amostra maior para dar nota Alta.
    fraco = mercado in ("UNDER25", "OVER25", "BTS_YES", "BTS_NO")
    lim_alta = 3.0 if fraco else 4.0
    n_alta = 60 if fraco else 40
    if n >= n_alta and ab <= lim_alta:
        return "Alta"
    if n >= 20 and ab <= 8:
        return "Media"
    return "Baixa"


def treinar_e_perfilar(eventos: list[dict]):
    """Treina forcas por liga, calcula a tendencia (taxas) e a auto-calibracao."""
    por_liga: dict[str, list[dict]] = defaultdict(list)
    for ev in eventos:
        por_liga[str(ev.get("league_id", ""))].append(ev)

    modelos = {}
    perfis = {}
    calibs = {}
    for lid, jogos in por_liga.items():
        forcas, mc, mf = construir_forcas(jogos)
        if not forcas:
            continue
        modelos[lid] = (forcas, mc, mf)
        calibs[lid] = calibrar_liga(jogos)
        n = over = btts = casa = empate = 0
        nao_reg = {"after et", "after pen.", "aet"}
        for j in jogos:
            if not _is_finished(j):
                continue
            if str(j.get("match_status", "")).strip().lower() in nao_reg:
                continue
            gh = _to_int_score(j.get("match_hometeam_score"))
            ga = _to_int_score(j.get("match_awayteam_score"))
            if gh is None or ga is None:
                continue
            n += 1
            if gh + ga > 2.5:
                over += 1
            if gh > 0 and ga > 0:
                btts += 1
            if gh > ga:
                casa += 1
            elif gh == ga:
                empate += 1
        if n:
            draw_pct = empate / n * 100
            perfis[lid] = {
                "over": over / n * 100, "under": 100 - over / n * 100,
                "btts": btts / n * 100, "casa": casa / n * 100,
                "empate": draw_pct, "n": n,
                # empate implausivel => dados ruins p/ 1X2 (ex.: mata-mata com penaltis)
                "suspeito_1x2": draw_pct < 12 or draw_pct > 45,
            }
    return modelos, perfis, calibs


def passa_perfil(mercado: str, perfil: dict | None) -> bool:
    if perfil is None:
        return True
    chave = PERFIL_MERCADO.get(mercado)
    if chave is None:
        return True  # mercados sem tendencia clara nao sao filtrados
    limiar = 45 if chave == "casa" else 50
    return perfil.get(chave, 0) >= limiar


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pick do dia: modelo + desfalques + perfil + value.")
    p.add_argument("--liga", help="league_id (recomendado)")
    p.add_argument("--data", help="Data alvo YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--treino-dias", type=int, default=150)
    p.add_argument("--min-prob", type=float, default=55.0, help="Prob minima do nosso modelo (%)")
    p.add_argument("--min-edge", type=float, default=0.0, help="Value minimo (0.05 = 5%). 0 = lista as melhores")
    p.add_argument("--min-odd", type=float, default=1.3)
    p.add_argument("--max-odd", type=float, default=4.0)
    p.add_argument("--desfalques", action="store_true", help="Ajusta ataque por lesionados (so com --liga)")
    p.add_argument("--perfil", action="store_true", help="So aposta a favor da tendencia da liga")
    p.add_argument("--top", type=int, default=5, help="Quantas apostas mostrar (padrao: 5)")
    p.add_argument("--salvar")
    return p.parse_args()


def gerar_picks(client, liga, data, treino_dias=150, min_prob=55.0, min_edge=0.0,
                min_odd=1.3, max_odd=4.0, perfil=False, desfalques=False, log=None,
                incluir_finalizados=False):
    """Gera os picks do dia (lista de dicts, ja ranqueada). Reutilizavel (CLI/Discord).

    Retorna (alvo_date, candidatos). Lanca ApiFootballError em erro de API.
    incluir_finalizados=True: usado no backtest (considera jogos ja jogados do dia;
    o treino continua so com dados ANTERIORES ao dia, entao segue out-of-sample).
    """
    def _log(msg):
        if log:
            log(msg)

    alvo = date.fromisoformat(data) if data else date.today()
    treino_de = (alvo - timedelta(days=treino_dias)).isoformat()
    treino_ate = (alvo - timedelta(days=1)).isoformat()

    treino = client.get_events(treino_de, treino_ate, league_id=liga)
    fixtures = client.get_events(alvo.isoformat(), alvo.isoformat(), league_id=liga)
    odds = client.get_odds(date_from=alvo.isoformat(), date_to=alvo.isoformat())

    modelos, perfis, calibs = treinar_e_perfilar(treino)
    if not modelos:
        return alvo, []

    # o que o sistema APRENDEU ao vivo (mercados/ligas a evitar por ROI real ruim)
    try:
        from aprendizado import carregar_ajustes
        aprendido = carregar_ajustes()
    except Exception:
        aprendido = {"evitar_mercado": set(), "evitar_liga_mercado": set()}

    if desfalques and liga and liga in modelos:
        try:
            from desfalques import multiplicadores_ataque
            mults = multiplicadores_ataque(client, liga)
            forcas = modelos[liga][0]
            for nome, f in forcas.items():
                d = mults.get(nome)
                if d and d["mult"] < 1.0:
                    f.att_casa *= d["mult"]; f.att_fora *= d["mult"]
            _log("desfalques aplicados")
        except Exception as exc:
            _log(f"desfalques nao aplicados: {exc}")

    odds_idx = indexar_odds(odds)
    candidatos = []
    for ev in fixtures:
        if _is_finished(ev) and not incluir_finalizados:
            continue
        if not liga_confiavel(ev.get("league_name", "")):
            continue  # pula amistosos e categorias de base
        lid = str(ev.get("league_id", ""))
        if lid not in modelos:
            continue
        regs = odds_idx.get(str(ev.get("match_id", "")), [])
        if not regs:
            continue
        forcas, mc, mf = modelos[lid]
        pred = prever(forcas, mc, mf, ev.get("match_hometeam_name"), ev.get("match_awayteam_name"))
        if not pred:
            continue
        perfil_liga = perfis.get(lid)
        calib = calibs.get(lid, {})
        melhor_do_jogo = None
        for mercado, chave in ODD_KEY.items():
            prob = pred[mercado]
            info = calib.get(mercado)
            bias = info["bias"] if info else None
            nb = info["n"] if info else 0
            prob_cal = prob if bias is None else min(99.0, max(1.0, prob - bias))
            nota = nota_confianca(bias, nb, mercado)
            # aprendizado ao vivo: rebaixa mercado/liga que vem dando prejuizo real
            liga_nome = ev.get("league_name", "")
            if mercado in aprendido.get("evitar_mercado", set()) or \
               f"{liga_nome}|{mercado}" in aprendido.get("evitar_liga_mercado", set()):
                nota = "Baixa"
            if prob_cal < min_prob:
                continue
            if perfil_liga and perfil_liga.get("suspeito_1x2") and mercado in ("HOME", "DRAW", "AWAY"):
                continue
            if not passa_perfil(mercado, perfil_liga if perfil else None):
                continue
            odd, bk = melhor_odd(regs, chave)
            if odd <= 0 or not (min_odd <= odd <= max_odd):
                continue
            value = (prob_cal / 100.0) * odd - 1.0
            if value < min_edge:
                continue
            item = {
                "match_id": str(ev.get("match_id", "")),
                "codigo": mercado,
                "jogo": f"{ev.get('match_hometeam_name')} x {ev.get('match_awayteam_name')}",
                "hora": ev.get("match_time", ""), "liga": ev.get("league_name", ""),
                "aposta": MERC_LABEL[mercado], "nossa_prob": round(prob_cal, 1),
                "prob_bruta": round(prob, 1), "odd": odd, "casa": bk,
                "value": round(value, 4), "nota": nota,
            }
            if melhor_do_jogo is None or value > melhor_do_jogo["value"]:
                melhor_do_jogo = item
        if melhor_do_jogo:
            candidatos.append(melhor_do_jogo)

    ordem_nota = {"Alta": 3, "Media": 2, "Baixa": 1, "sem dados": 0}
    candidatos.sort(key=lambda x: (ordem_nota.get(x["nota"], 0), x["value"]), reverse=True)
    return alvo, candidatos


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)

    treino_de = (date.fromisoformat(args.data) if args.data else date.today()) - timedelta(days=args.treino_dias)
    console.print(Panel.fit(
        f"Liga: {args.liga or '(todas)'}  |  Alvo: {args.data or 'hoje'}\n"
        f"Filtros: prob>={args.min_prob:.0f}%  value>={args.min_edge:+.0%}  odd {args.min_odd}-{args.max_odd}"
        + ("  +desfalques" if args.desfalques else "") + ("  +perfil-liga" if args.perfil else ""),
        title="PICK DO DIA (modelo + desfalques + perfil + value)",
    ))

    try:
        with console.status("Treinando, buscando jogos e odds do dia..."):
            alvo, candidatos = gerar_picks(
                client, args.liga, args.data, args.treino_dias, args.min_prob,
                args.min_edge, args.min_odd, args.max_odd, args.perfil, args.desfalques,
                log=lambda m: console.print(f"[dim]{m}[/dim]"),
            )
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    if not candidatos:
        console.print("[yellow]Nenhum pick passou nos filtros hoje. Isso e saudavel: value e raro. "
                      "Tente outra data/liga, baixe --min-prob/--min-edge ou tire --perfil.[/yellow]")
        return

    tab = Table(title=f"Melhores picks de {alvo} (nota de confianca + value)", title_justify="left")
    tab.add_column("#", justify="right")
    tab.add_column("Jogo", overflow="fold"); tab.add_column("Hora")
    tab.add_column("Liga", overflow="fold"); tab.add_column("Aposta")
    tab.add_column("Prob calib.", justify="right"); tab.add_column("Odd", justify="right")
    tab.add_column("Casa"); tab.add_column("Value", justify="right"); tab.add_column("Nota")
    cor_nota = {"Alta": "green", "Media": "yellow", "Baixa": "red", "sem dados": "dim"}
    for i, c in enumerate(candidatos[:args.top], 1):
        cv = "green" if c["value"] >= 0 else "red"
        cn = cor_nota.get(c["nota"], "white")
        tab.add_row(str(i), c["jogo"], c["hora"], c["liga"], c["aposta"],
                    f"{c['nossa_prob']:.0f}%", f"{c['odd']:.2f}", c["casa"],
                    f"[{cv}]{c['value']:+.1%}[/{cv}]", f"[{cn}]{c['nota']}[/{cn}]")
    console.print(tab)

    # pick recomendado = melhor com nota Alta; se nao houver, avisa
    altas = [c for c in candidatos if c["nota"] == "Alta"]
    if altas:
        melhor = altas[0]
        console.print(
            f"\n[bold]Pick recomendado (confianca Alta):[/bold] {melhor['aposta']} em "
            f"[bold]{melhor['jogo']}[/bold] @ {melhor['odd']:.2f} ({melhor['casa']}) | "
            f"prob calibrada {melhor['nossa_prob']:.0f}% | value "
            f"[{'green' if melhor['value']>=0 else 'red'}]{melhor['value']:+.1%}[/]"
        )
    else:
        console.print("\n[yellow]Nenhum pick com nota ALTA hoje. O honesto e nao apostar "
                      "(ou so com stake minimo nos de nota Media). Forcar aposta sem confianca e como a casa lucra.[/yellow]")
    console.print(
        "[dim]Prob calib. = probabilidade do modelo JA corrigida pelo vies medido no teste interno "
        "da liga. Nota Alta = vies pequeno e amostra boa naquele mercado. Mesmo assim: aposta simples, "
        "stake pequeno, confirme a odd na casa. Nada e garantia.[/dim]"
    )

    if args.salvar:
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump({"data": alvo.isoformat(), "liga": args.liga, "picks": candidatos}, fh,
                      ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
