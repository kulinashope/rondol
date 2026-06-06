"""Calibracao e poder do NOSSO modelo (Poisson), em VARIAS ligas.

Nao usa a probabilidade da API. Treina o nosso modelo por liga (forca de
ataque/defesa) com o historico e, num periodo de teste:

  1. CALIBRACAO: agrupa as previsoes por faixa de probabilidade do nosso modelo
     (50-60, 60-70, ...) e mostra a taxa de acerto REAL de cada faixa. Um modelo
     bom e calibrado: quando diz 80%, acerta ~80%.
  2. PICK DO DIA: pega o palpite mais confiavel do nosso modelo entre TODAS as
     ligas em cada dia e mede em quantos dias ele acertou.

Assim da pra ver, com honestidade, se o nosso modelo e melhor/mais confiavel que
a API e ate onde da pra confiar nele.

Uso:
    python calibragem.py --treino-dias 150 --teste-dias 40
    python calibragem.py --treino-dias 150 --teste-dias 40 --salvar calib.json
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
from modelo import construir_forcas, prever, acertou, melhor_palpite, MERC_LABEL


console = Console()


def treinar_por_liga(eventos: list[dict]) -> dict:
    por_liga: dict[str, list[dict]] = defaultdict(list)
    for ev in eventos:
        por_liga[str(ev.get("league_id", ""))].append(ev)
    modelos = {}
    for lid, jogos in por_liga.items():
        forcas, mc, mf = construir_forcas(jogos)
        if forcas:
            modelos[lid] = (forcas, mc, mf)
    return modelos


@dataclass
class Faixa:
    lo: int
    acertos: int = 0
    n: int = 0

    def taxa(self) -> float:
        return self.acertos / self.n * 100 if self.n else 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Calibracao e poder do nosso modelo Poisson em varias ligas.")
    p.add_argument("--treino-dias", type=int, default=150)
    p.add_argument("--teste-dias", type=int, default=40)
    p.add_argument("--liga", help="Restringe a uma liga (opcional)")
    p.add_argument("--min-pick-prob", type=float, default=0.0, help="So conta picks do dia com prob >= isso")
    p.add_argument("--salvar")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)

    fim = date.today() - timedelta(days=1)
    ini_teste = fim - timedelta(days=max(1, args.teste_dias) - 1)
    treino_de = (ini_teste - timedelta(days=args.treino_dias)).isoformat()
    treino_ate = (ini_teste - timedelta(days=1)).isoformat()

    console.print(Panel.fit(
        f"Liga: {args.liga or '(todas)'}  |  Treino: {treino_de} a {treino_ate}  |  "
        f"Teste: {ini_teste} a {fim}",
        title="Calibracao do NOSSO modelo (Poisson)",
    ))

    try:
        with console.status("Treinando por liga e avaliando o teste..."):
            treino = client.get_events(treino_de, treino_ate, league_id=args.liga)
            teste = client.get_events(ini_teste.isoformat(), fim.isoformat(), league_id=args.liga)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    modelos = treinar_por_liga(treino)
    if not modelos:
        console.print("[yellow]Treino insuficiente.[/yellow]")
        return

    # 1) calibracao: bins por faixa de prob do melhor palpite de CADA jogo
    faixas = {lo: Faixa(lo) for lo in range(50, 100, 10)}
    # 2) pick do dia (melhor palpite entre todas as ligas)
    por_dia: dict[str, list[tuple[str, float, dict]]] = defaultdict(list)

    for ev in teste:
        if not _is_finished(ev):
            continue
        gh = _to_int_score(ev.get("match_hometeam_score"))
        ga = _to_int_score(ev.get("match_awayteam_score"))
        if gh is None or ga is None:
            continue
        lid = str(ev.get("league_id", ""))
        if lid not in modelos:
            continue
        forcas, mc, mf = modelos[lid]
        pred = prever(forcas, mc, mf, ev.get("match_hometeam_name"), ev.get("match_awayteam_name"))
        if not pred:
            continue
        m, prob = melhor_palpite(pred)
        ok = acertou(m, gh, ga)
        # calibracao
        lo = min(90, int(prob // 10 * 10))
        if lo >= 50:
            faixas[lo].n += 1
            faixas[lo].acertos += 1 if ok else 0
        # pick do dia
        d = ev.get("match_date", "")
        por_dia[d].append((m, prob, {"ok": ok, "jogo": f"{ev.get('match_hometeam_name')} x {ev.get('match_awayteam_name')}"}))

    # --- tabela de calibracao ---
    tcal = Table(title="Calibracao: o que o modelo PROMETE x o que ACONTECE", title_justify="left")
    tcal.add_column("Faixa de prob (modelo)")
    tcal.add_column("N", justify="right")
    tcal.add_column("Acerto real", justify="right")
    for lo in range(50, 100, 10):
        f = faixas[lo]
        if f.n == 0:
            continue
        # bem calibrado se acerto real ~ meio da faixa
        meio = lo + 5
        cor = "green" if f.taxa() >= meio - 5 else "red"
        tcal.add_row(f"{lo}-{lo+10}%", str(f.n), f"[{cor}]{f.taxa():.1f}%[/{cor}]")
    console.print(tcal)

    # --- pick do dia ---
    dias = acertos = 0
    for d in sorted(por_dia):
        cand = [(m, prob, meta) for (m, prob, meta) in por_dia[d] if prob >= args.min_pick_prob]
        if not cand:
            continue
        m, prob, meta = max(cand, key=lambda x: x[1])
        dias += 1
        acertos += 1 if meta["ok"] else 0

    if dias:
        tp = Table(title="Pick do dia do NOSSO modelo (mais confiavel entre todas as ligas)", title_justify="left")
        tp.add_column("Metrica"); tp.add_column("Valor", justify="right")
        tp.add_row("Dias avaliados", str(dias))
        tp.add_row("Dias em que o pick acertou", f"{acertos} ({acertos/dias*100:.1f}%)")
        console.print(tp)

    console.print(
        "[dim]Se o 'acerto real' fica BEM abaixo do meio da faixa, o modelo e superconfiante "
        "(promete demais) — igual a API. Calibracao honesta e o que separa um modelo util de um "
        "gerador de falsas certezas.[/dim]"
    )

    if args.salvar:
        payload = {
            "treino": [treino_de, treino_ate], "teste": [ini_teste.isoformat(), fim.isoformat()],
            "calibracao": {f"{lo}-{lo+10}": {"n": faixas[lo].n, "acerto_real": round(faixas[lo].taxa(), 1)}
                           for lo in range(50, 100, 10) if faixas[lo].n},
            "pick_do_dia": {"dias": dias, "acertos": acertos,
                            "taxa": round(acertos/dias*100, 1) if dias else 0},
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
