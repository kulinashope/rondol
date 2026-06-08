"""Modelo proprio (Poisson) a partir dos dados brutos da apifootball.com.

Em vez de confiar na probabilidade pronta da API, construimos NOSSO modelo a
partir do historico de resultados (get_events) — o mesmo principio que as casas
usam: estimar a forca de ataque e defesa de cada time e a vantagem de jogar em
casa, e derivar a probabilidade de cada placar via distribuicao de Poisson.

A partir do placar provavel saem: 1X2, Over/Under 2.5 e Ambas Marcam. O modelo
ranqueia o palpite MAIS confiavel de cada dia (a ideia de "escolher 1 entre
varios jogos") e, se houver odds, compara com o mercado para achar VALUE.

Forca dos times (dentro de cada liga):
    att_casa  = (gols feitos em casa / media de gols de mandante da liga)
    def_casa  = (gols sofridos em casa / media de gols de visitante da liga)
    (idem para fora). Com encolhimento p/ a media quando ha poucos jogos.

Previsao de um jogo (mandante M, visitante V):
    lambda_casa  = media_casa_liga * att_casa[M] * def_fora[V]
    lambda_fora  = media_fora_liga * att_fora[V] * def_casa[M]
    P(placar i-j) = Poisson(i; lambda_casa) * Poisson(j; lambda_fora)

Uso:
    python modelo.py --liga 71 --treino-dias 120 --data 2026-06-07   # palpites do dia
    python modelo.py --liga 71 --treino-dias 120 --backtest --teste-dias 30
    python modelo.py --liga 71 --data 2026-06-07 --salvar palpites.json

DICA: rode SEMPRE com --liga (a forca dos times so faz sentido dentro da liga).
Pegue o league_id com:  python main.py --raw events   (ou a doc da apifootball)
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from config import Settings
from conferir import _is_finished, _to_int_score

console = Console()
MAX_GOLS = 8  # teto do somatorio de Poisson


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


@dataclass
class Forca:
    att_casa: float = 1.0
    def_casa: float = 1.0
    att_fora: float = 1.0
    def_fora: float = 1.0
    jogos_casa: int = 0
    jogos_fora: int = 0
    over_rate: float = 0.5   # fracao (ponderada) de jogos do time com Over 2.5
    btts_rate: float = 0.5   # fracao (ponderada) de jogos do time com BTTS


def _stat_evento(j: dict, tipo: str):
    """Extrai (home, away) de um tipo de estatistica do jogo (ex.: 'On Target')."""
    for s in (j.get("statistics") or []):
        if str(s.get("type", "")).strip().lower() == tipo.lower():
            try:
                return int(s.get("home")), int(s.get("away"))
            except (TypeError, ValueError):
                return None
    return None


def construir_forcas(jogos: list[dict], half_life_dias: float = 40.0) -> tuple[dict[str, Forca], float, float]:
    """Estima forcas dos times e medias da liga, com PESO POR RECENCIA.

    Jogos recentes pesam mais (forma atual): o peso cai pela metade a cada
    `half_life_dias`. Exclui jogos decididos em prorrogacao/penaltis (placar nao
    e de 90 min). Encolhe p/ a media quando ha poucos dados.

    Alem dos gols, usa FINALIZACOES NO GOL ('On Target') quando disponiveis: o
    rating de ataque/defesa e uma mistura gols (60%) + chutes no gol (40%), pois
    chutes sao mais estaveis e preditivos (achado academico p/ Over/Under).
    """
    import datetime

    STATUS_NAO_REGULAMENTAR = {"after et", "after pen.", "aet"}
    registros = []  # (date, home, away, gh, ga, oth, ota)
    for j in jogos:
        if not _is_finished(j):
            continue
        if str(j.get("match_status", "")).strip().lower() in STATUS_NAO_REGULAMENTAR:
            continue
        gh = _to_int_score(j.get("match_hometeam_score"))
        ga = _to_int_score(j.get("match_awayteam_score"))
        h = j.get("match_hometeam_name")
        a = j.get("match_awayteam_name")
        if gh is None or ga is None or not h or not a:
            continue
        try:
            d = datetime.date.fromisoformat(str(j.get("match_date", ""))[:10])
        except ValueError:
            d = None
        ot = _stat_evento(j, "On Target")  # (chutes no gol casa, fora) ou None
        registros.append((d, h, a, gh, ga, ot))

    if not registros:
        return {}, 0.0, 0.0

    datas = [d for (d, *_) in registros if d is not None]
    ref = max(datas) if datas else None

    def peso(d) -> float:
        if d is None or ref is None or half_life_dias <= 0:
            return 1.0
        return 0.5 ** ((ref - d).days / half_life_dias)

    sw = swh = swa = 0.0
    sw_over = sw_btts = 0.0
    sw_ot = swh_ot = swa_ot = 0.0  # medias de chutes no gol da liga
    gols_casa = defaultdict(list); sofr_casa = defaultdict(list)
    gols_fora = defaultdict(list); sofr_fora = defaultdict(list)
    ot_casa = defaultdict(list); ots_casa = defaultdict(list)   # chutes feitos/sofridos em casa
    ot_fora = defaultdict(list); ots_fora = defaultdict(list)   # chutes feitos/sofridos fora
    eventos_time = defaultdict(list)
    for (d, h, a, gh, ga, ot) in registros:
        w = peso(d)
        sw += w; swh += w * gh; swa += w * ga
        over_flag = 1 if (gh + ga) > 2 else 0
        btts_flag = 1 if (gh > 0 and ga > 0) else 0
        sw_over += w * over_flag; sw_btts += w * btts_flag
        gols_casa[h].append((w, gh)); sofr_casa[h].append((w, ga))
        gols_fora[a].append((w, ga)); sofr_fora[a].append((w, gh))
        eventos_time[h].append((w, over_flag, btts_flag))
        eventos_time[a].append((w, over_flag, btts_flag))
        if ot is not None:
            oth, ota = ot
            sw_ot += w; swh_ot += w * oth; swa_ot += w * ota
            ot_casa[h].append((w, oth)); ots_casa[h].append((w, ota))
            ot_fora[a].append((w, ota)); ots_fora[a].append((w, oth))

    if sw == 0:
        return {}, 0.0, 0.0
    media_casa = swh / sw
    media_fora = swa / sw
    liga_over = sw_over / sw
    liga_btts = sw_btts / sw
    media_ot_casa = (swh_ot / sw_ot) if sw_ot > 0 else 0.0
    media_ot_fora = (swa_ot / sw_ot) if sw_ot > 0 else 0.0
    k = 4.0

    def razao(vals, media) -> float:
        if not vals or media <= 0:
            return 1.0
        pt = sum(w for w, _ in vals)
        if pt <= 0:
            return 1.0
        media_pond = sum(w * g for w, g in vals) / pt
        return (pt * (media_pond / media) + k * 1.0) / (pt + k)

    def taxa(vals, media_liga) -> float:
        if not vals:
            return media_liga
        pt = sum(w for w, *_ in vals)
        if pt <= 0:
            return media_liga
        return (sum(w * fl for w, fl in vals) + k * media_liga) / (pt + k)

    def blend(rating_gols, vals_ot, media_ot):
        # mistura 60% gols + 40% chutes no gol, se houver dados de chutes
        if media_ot <= 0 or sum(1 for _ in vals_ot) < 3:
            return rating_gols
        return 0.6 * rating_gols + 0.4 * razao(vals_ot, media_ot)

    times = set(gols_casa) | set(gols_fora) | set(sofr_casa) | set(sofr_fora)
    forcas: dict[str, Forca] = {}
    for t in times:
        f = Forca()
        f.jogos_casa = len(gols_casa.get(t, []))
        f.jogos_fora = len(gols_fora.get(t, []))
        f.att_casa = blend(razao(gols_casa.get(t, []), media_casa), ot_casa.get(t, []), media_ot_casa)
        f.def_casa = blend(razao(sofr_casa.get(t, []), media_fora), ots_casa.get(t, []), media_ot_fora)
        f.att_fora = blend(razao(gols_fora.get(t, []), media_fora), ot_fora.get(t, []), media_ot_fora)
        f.def_fora = blend(razao(sofr_fora.get(t, []), media_casa), ots_fora.get(t, []), media_ot_casa)
        evs = eventos_time.get(t, [])
        f.over_rate = taxa([(w, o) for (w, o, _b) in evs], liga_over)
        f.btts_rate = taxa([(w, b) for (w, _o, b) in evs], liga_btts)
        forcas[t] = f
    return forcas, media_casa, media_fora


def _dc_tau(i: int, j: int, lam: float, mu: float, rho: float) -> float:
    """Correcao de Dixon-Coles para placares baixos (corrige 0-0,1-0,0-1,1-1).

    Captura a dependencia que o Poisson puro ignora: jogos truncados/defensivos
    tem mais 0-0 e 1-1 do que a independencia preve. rho negativo (~-0.13).
    """
    if i == 0 and j == 0:
        return 1.0 - lam * mu * rho
    if i == 0 and j == 1:
        return 1.0 + lam * rho
    if i == 1 and j == 0:
        return 1.0 + mu * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def prever(forcas, media_casa, media_fora, home: str, away: str, rho: float = -0.13) -> dict | None:
    fh = forcas.get(home)
    fa = forcas.get(away)
    if fh is None or fa is None:
        return None
    lam_casa = media_casa * fh.att_casa * fa.def_fora
    lam_fora = media_fora * fa.att_fora * fh.def_casa
    lam_casa = min(max(lam_casa, 0.05), 6.0)
    lam_fora = min(max(lam_fora, 0.05), 6.0)

    # matriz de placares com correcao de Dixon-Coles, depois renormaliza
    matriz = []
    total = 0.0
    for i in range(MAX_GOLS + 1):
        pi = poisson_pmf(i, lam_casa)
        linha = []
        for jx in range(MAX_GOLS + 1):
            p = pi * poisson_pmf(jx, lam_fora) * _dc_tau(i, jx, lam_casa, lam_fora, rho)
            p = max(p, 0.0)
            linha.append(p)
            total += p
        matriz.append(linha)
    if total <= 0:
        return None

    p_home = p_draw = p_away = p_over25 = p_btts = 0.0
    for i in range(MAX_GOLS + 1):
        for jx in range(MAX_GOLS + 1):
            p = matriz[i][jx] / total
            if i > jx:
                p_home += p
            elif i == jx:
                p_draw += p
            else:
                p_away += p
            if i + jx > 2:
                p_over25 += p
            if i >= 1 and jx >= 1:
                p_btts += p

    # ENSEMBLE: mistura o modelo com a taxa empirica dos dois times (Over/BTTS).
    # Ataca o vies residual usando a propensao real de marcar/sofrer de cada time.
    alpha = 0.6  # peso do modelo; (1-alpha) = peso da taxa empirica
    emp_over = (fh.over_rate + fa.over_rate) / 2.0
    emp_btts = (fh.btts_rate + fa.btts_rate) / 2.0
    p_over25 = alpha * p_over25 + (1 - alpha) * emp_over
    p_btts = alpha * p_btts + (1 - alpha) * emp_btts

    return {
        "lam_casa": lam_casa, "lam_fora": lam_fora,
        "HOME": p_home * 100, "DRAW": p_draw * 100, "AWAY": p_away * 100,
        "OVER25": p_over25 * 100, "UNDER25": (1 - p_over25) * 100,
        "BTS_YES": p_btts * 100, "BTS_NO": (1 - p_btts) * 100,
    }


MERC_LABEL = {
    "HOME": "Casa (1)", "DRAW": "Empate (X)", "AWAY": "Fora (2)",
    "OVER25": "Over 2.5", "UNDER25": "Under 2.5", "BTS_YES": "Ambas marcam: Sim",
    "BTS_NO": "Ambas marcam: Nao",
}
# resolvedor de resultado p/ backtest
def acertou(mc: str, gh: int, ga: int) -> bool:
    t = gh + ga
    return {
        "HOME": gh > ga, "DRAW": gh == ga, "AWAY": gh < ga,
        "OVER25": t > 2.5, "UNDER25": t < 2.5,
        "BTS_YES": gh > 0 and ga > 0, "BTS_NO": not (gh > 0 and ga > 0),
    }[mc]


def melhor_palpite(pred: dict) -> tuple[str, float]:
    cand = {m: pred[m] for m in ("HOME", "DRAW", "AWAY", "OVER25", "UNDER25", "BTS_YES", "BTS_NO")}
    m = max(cand, key=cand.get)
    return m, cand[m]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Modelo proprio (Poisson) a partir dos dados da apifootball.com")
    p.add_argument("--liga", help="league_id (MUITO recomendado)")
    p.add_argument("--data", help="Data alvo YYYY-MM-DD p/ palpites (padrao: hoje)")
    p.add_argument("--treino-dias", type=int, default=120, help="Dias de historico p/ treinar (padrao: 120)")
    p.add_argument("--min-prob", type=float, default=65.0, help="Prob minima do nosso modelo p/ listar (%)")
    p.add_argument("--desfalques", action="store_true",
                   help="Ajusta o ataque dos times pelos lesionados atuais (so com --liga; nao usar em datas passadas)")
    p.add_argument("--backtest", action="store_true", help="Avalia o modelo dia a dia em vez de prever")
    p.add_argument("--teste-dias", type=int, default=30, help="No backtest: dias de teste apos o treino (padrao: 30)")
    p.add_argument("--salvar", help="Salva em .json")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)

    if args.backtest:
        rodar_backtest(client, args)
        return

    alvo = date.fromisoformat(args.data) if args.data else date.today()
    treino_ate = alvo - timedelta(days=1)
    treino_de = alvo - timedelta(days=args.treino_dias)

    console.print(
        Panel.fit(
            f"Liga: {args.liga or '(todas - NAO recomendado)'}  |  "
            f"Treino: {treino_de} a {treino_ate}  |  Alvo: {alvo}",
            title="Modelo proprio (Poisson) - palpites do dia",
        )
    )

    try:
        with console.status("Treinando com historico e buscando jogos do dia..."):
            treino = client.get_events(treino_de.isoformat(), treino_ate.isoformat(), league_id=args.liga)
            fixtures = client.get_events(alvo.isoformat(), alvo.isoformat(), league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    forcas, mc, mf = construir_forcas(treino)
    if not forcas:
        console.print("[yellow]Sem historico suficiente p/ treinar. Aumente --treino-dias ou troque a liga.[/yellow]")
        return

    # ajuste opcional por desfalques (lesionados atuais) - so para previsao do dia
    if args.desfalques and args.liga:
        try:
            from desfalques import multiplicadores_ataque
            mults = multiplicadores_ataque(client, args.liga)
            ajustados = 0
            for nome, f in forcas.items():
                d = mults.get(nome)
                if d and d["mult"] < 1.0:
                    f.att_casa *= d["mult"]
                    f.att_fora *= d["mult"]
                    ajustados += 1
            console.print(f"[dim]Ajuste por desfalques aplicado a {ajustados} time(s).[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Nao foi possivel aplicar desfalques: {exc}[/yellow]")

    palpites = []
    for fx in fixtures:
        if _is_finished(fx):
            continue
        h = fx.get("match_hometeam_name")
        a = fx.get("match_awayteam_name")
        pred = prever(forcas, mc, mf, h, a)
        if not pred:
            continue
        m, prob = melhor_palpite(pred)
        if prob >= args.min_prob:
            palpites.append({
                "jogo": f"{h} x {a}", "hora": fx.get("match_time", ""),
                "palpite": MERC_LABEL[m], "prob": round(prob, 1),
                "odd_justa": round(100 / prob, 2) if prob > 0 else 0,
                "lam_casa": round(pred["lam_casa"], 2), "lam_fora": round(pred["lam_fora"], 2),
            })
    palpites.sort(key=lambda x: x["prob"], reverse=True)

    if not palpites:
        console.print("[yellow]Nenhum palpite acima do limite. Baixe --min-prob.[/yellow]")
        return

    tab = Table(title=f"Palpites do modelo p/ {alvo} (ordenado por confianca)", title_justify="left")
    tab.add_column("Jogo", overflow="fold")
    tab.add_column("Hora")
    tab.add_column("Palpite")
    tab.add_column("Prob", justify="right")
    tab.add_column("Odd justa", justify="right")
    tab.add_column("Gols esp. (C-F)", justify="right")
    for p in palpites:
        tab.add_row(p["jogo"], p["hora"], p["palpite"], f"{p['prob']:.0f}%",
                    f"{p['odd_justa']:.2f}", f"{p['lam_casa']:.1f}-{p['lam_fora']:.1f}")
    console.print(tab)
    console.print(
        "[dim]Odd justa = 100/prob do NOSSO modelo. Se a casa pagar MAIS que isso, "
        "ha value. Use o backtest p/ ver se o modelo realmente acerta antes de confiar.[/dim]"
    )

    if args.salvar:
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump({"data": alvo.isoformat(), "liga": args.liga, "palpites": palpites}, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


def rodar_backtest(client: ApiFootballClient, args) -> None:
    """Treina ate uma data e mede acerto/ROI do palpite mais confiavel por dia."""
    fim = date.today() - timedelta(days=1)
    ini_teste = fim - timedelta(days=max(1, args.teste_dias) - 1)
    treino_de = ini_teste - timedelta(days=args.treino_dias)

    console.print(
        Panel.fit(
            f"Liga: {args.liga or '(todas)'}  |  Treino: {treino_de} a {ini_teste - timedelta(days=1)}  |  "
            f"Teste: {ini_teste} a {fim}",
            title="Modelo proprio (Poisson) - backtest",
        )
    )
    try:
        with console.status("Baixando treino e teste..."):
            treino = client.get_events(treino_de.isoformat(), (ini_teste - timedelta(days=1)).isoformat(), league_id=args.liga)
            teste = client.get_events(ini_teste.isoformat(), fim.isoformat(), league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    forcas, mc, mf = construir_forcas(treino)
    if not forcas:
        console.print("[yellow]Treino insuficiente.[/yellow]")
        return

    # agrupa jogos de teste por dia
    por_dia: dict[str, list[dict]] = defaultdict(list)
    for j in teste:
        if _is_finished(j):
            por_dia[j.get("match_date", "")].append(j)

    dias = acertos = 0
    total_palpites = total_ok = 0
    for d in sorted(por_dia):
        # melhor palpite do dia (maior prob do nosso modelo)
        melhor = None
        for j in por_dia[d]:
            pred = prever(forcas, mc, mf, j.get("match_hometeam_name"), j.get("match_awayteam_name"))
            if not pred:
                continue
            m, prob = melhor_palpite(pred)
            if melhor is None or prob > melhor[1]:
                melhor = (m, prob, j)
        if melhor is None:
            continue
        m, prob, j = melhor
        gh = _to_int_score(j.get("match_hometeam_score"))
        ga = _to_int_score(j.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        dias += 1
        ok = acertou(m, gh, ga)
        acertos += 1 if ok else 0
        total_palpites += 1
        total_ok += 1 if ok else 0

    if dias == 0:
        console.print("[yellow]Sem dias avaliaveis no teste.[/yellow]")
        return

    tab = Table(title="Backtest do modelo (palpite mais confiavel por dia)", title_justify="left")
    tab.add_column("Metrica")
    tab.add_column("Valor", justify="right")
    tab.add_row("Dias avaliados", str(dias))
    tab.add_row("Dias em que o palpite acertou", f"{acertos} ({acertos / dias * 100:.1f}%)")
    console.print(tab)
    console.print(
        "[dim]Isto mede so o ACERTO do nosso palpite mais confiavel do dia. "
        "Para saber se LUCRA, e preciso comparar com a odd real (value). ROI passado nao garante futuro.[/dim]"
    )


if __name__ == "__main__":
    main()
