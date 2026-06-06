"""Scanner de ARBITRAGEM (surebet) nas odds da apifootball.com.

Arbitragem nao usa previsao: e apostar em TODOS os resultados de um jogo, em
casas diferentes, quando a soma das probabilidades implicitas das MELHORES odds
fica abaixo de 100%. Nesse caso, qualquer que seja o placar, voce lucra.

Para um mercado de 2 ou 3 vias:
    inv = 1/melhor_odd_A + 1/melhor_odd_B (+ 1/melhor_odd_C)
    se inv < 1  ->  ha arbitragem; lucro% = (1/inv - 1) * 100
    stake_i = banca * (1/odd_i) / inv   (distribui a aposta entre os resultados)

Mercados verificados: 1X2 (odd_1/odd_x/odd_2), Over/Under 2.5 (o+2.5/u+2.5),
Ambas Marcam (bts_yes/bts_no). Pega a MELHOR odd de cada lado entre bookmakers.

Uso:
    python arbitragem.py --data 2026-06-06
    python arbitragem.py --dias 1 --banca 100 --min-lucro 0.5
    python arbitragem.py --data 2026-06-06 --salvar arbs.json

ATENCAO: as odds da API sao um retrato (podem estar atrasadas e de horarios
diferentes entre bookmakers). Uma "surebet" achada aqui pode ja nao existir na
hora de apostar. Veja as ressalvas impressas no final.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from api_client import ApiFootballClient, ApiFootballError
from config import Settings

console = Console()

# mercado -> (rotulo, [(rotulo_lado, chave_odd), ...])
MERCADOS = {
    "1X2": ("1X2", [("Casa", "odd_1"), ("Empate", "odd_x"), ("Fora", "odd_2")]),
    "OU25": ("Over/Under 2.5", [("Over 2.5", "o+2.5"), ("Under 2.5", "u+2.5")]),
    "BTTS": ("Ambas Marcam", [("BTTS Sim", "bts_yes"), ("BTTS Nao", "bts_no")]),
}


def _to_float(v):
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


@dataclass
class Surebet:
    match_id: str
    jogo: str
    liga: str
    data: str
    mercado: str
    inv: float
    pernas: list[dict]  # [{lado, odd, bookmaker}]

    @property
    def lucro_pct(self) -> float:
        return (1.0 / self.inv - 1.0) * 100.0 if self.inv > 0 else 0.0


def melhor_odd_por_lado(registros: list[dict], chave: str) -> tuple[float, str]:
    melhor, bk = 0.0, ""
    for r in registros:
        odd = _to_float(r.get(chave))
        if odd is not None and odd > melhor:
            melhor = odd
            bk = str(r.get("odd_bookmakers") or r.get("bookmaker") or r.get("bk_name") or "?")
    return melhor, bk


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scanner de arbitragem (surebets) nas odds da apifootball.com")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--ate", help="Data final YYYY-MM-DD (padrao: = data)")
    p.add_argument("--dias", type=int, default=1, help="Dias a partir da data inicial (padrao: 1)")
    p.add_argument("--banca", type=float, default=100.0, help="Banca total por surebet (padrao: 100)")
    p.add_argument("--min-lucro", type=float, default=0.0, help="Lucro%% minimo p/ listar (padrao: 0)")
    p.add_argument("--salvar", help="Salva as oportunidades em .json")
    return p.parse_args()


def resolve_dates(args) -> tuple[str, str]:
    start = date.fromisoformat(args.data) if args.data else date.today()
    end = date.fromisoformat(args.ate) if args.ate else start + timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def main() -> None:
    args = parse_args()
    settings = Settings.load()
    client = ApiFootballClient(settings)
    d_from, d_to = resolve_dates(args)

    console.print(
        Panel.fit(
            f"Periodo: [bold]{d_from}[/bold] a [bold]{d_to}[/bold]  |  Banca/surebet: {args.banca:.0f}",
            title="Scanner de arbitragem (surebets) - apifootball.com",
        )
    )

    try:
        with console.status("Baixando odds e jogos..."):
            odds = client.get_odds(date_from=d_from, date_to=d_to)
            eventos = client.get_events(d_from, d_to)
    except ApiFootballError as exc:
        console.print(f"[red]{exc}[/red]")
        return

    # mapa match_id -> nomes/liga (as odds nao trazem nome dos times)
    nomes: dict[str, dict] = {}
    for ev in eventos:
        mid = str(ev.get("match_id", "")).strip()
        if mid:
            nomes[mid] = {
                "jogo": f"{ev.get('match_hometeam_name', '?')} x {ev.get('match_awayteam_name', '?')}",
                "liga": ev.get("league_name", ""),
                "data": ev.get("match_date", ""),
            }

    # agrupa registros (linhas de bookmaker) por match_id
    por_jogo: dict[str, list[dict]] = {}
    info: dict[str, dict] = {}
    registros = odds if isinstance(odds, list) else []
    for r in registros:
        if not isinstance(r, dict):
            continue
        mid = str(r.get("match_id", "")).strip()
        if not mid:
            continue
        por_jogo.setdefault(mid, []).append(r)
        info.setdefault(mid, r)

    n_jogos = len(por_jogo)
    n_bookmakers = max((len(v) for v in por_jogo.values()), default=0)

    surebets: list[Surebet] = []
    for mid, regs in por_jogo.items():
        for cod, (rot, lados) in MERCADOS.items():
            pernas = []
            inv = 0.0
            ok = True
            for rot_lado, chave in lados:
                odd, bk = melhor_odd_por_lado(regs, chave)
                if odd <= 0:
                    ok = False
                    break
                inv += 1.0 / odd
                pernas.append({"lado": rot_lado, "odd": odd, "bookmaker": bk})
            if ok and inv < 1.0:
                meta = nomes.get(mid, {})
                surebets.append(
                    Surebet(
                        match_id=mid,
                        jogo=meta.get("jogo", f"match {mid}"),
                        liga=meta.get("liga", ""),
                        data=meta.get("data", ""),
                        mercado=rot,
                        inv=inv,
                        pernas=pernas,
                    )
                )

    surebets = [s for s in surebets if s.lucro_pct >= args.min_lucro]
    surebets.sort(key=lambda s: s.lucro_pct, reverse=True)

    console.print(
        f"[dim]Jogos com odds: {n_jogos}  |  max bookmakers por jogo: {n_bookmakers}  |  "
        f"surebets encontradas: {len(surebets)}[/dim]\n"
    )

    if not surebets:
        console.print(
            "[yellow]Nenhuma arbitragem encontrada nessas odds.[/yellow] "
            "[dim]Normal: a apifootball costuma trazer poucas casas por jogo (as vezes 1 so), "
            "e arbitragem exige varias casas com odds divergentes ao mesmo tempo.[/dim]"
        )
    else:
        tab = Table(title="Oportunidades de arbitragem (lucro garantido se executavel)", title_justify="left")
        tab.add_column("Jogo", overflow="fold")
        tab.add_column("Mercado")
        tab.add_column("Lucro", justify="right")
        tab.add_column("Distribuicao da banca (lado @ odd | casa | stake)")
        for s in surebets[:30]:
            linhas = []
            for p in s.pernas:
                stake = args.banca * (1.0 / p["odd"]) / s.inv
                linhas.append(f"{p['lado']} @ {p['odd']:.2f} | {p['bookmaker']} | R${stake:.2f}")
            alerta = " [yellow](margem alta: provavel odd desatualizada)[/yellow]" if s.lucro_pct > 8 else ""
            tab.add_row(
                f"{s.jogo}\n[dim]{s.liga} {s.data}[/dim]",
                s.mercado,
                f"[green]+{s.lucro_pct:.2f}%[/green]{alerta}",
                "\n".join(linhas),
            )
        console.print(tab)

    console.print(
        "\n[dim]RESSALVAS IMPORTANTES:\n"
        "- As odds da API sao um retrato e podem estar atrasadas / de horarios diferentes entre casas; "
        "uma surebet aqui pode nao existir na hora de apostar.\n"
        "- Arbitragem exige contas em varias casas, capital parado e execucao rapida.\n"
        "- Casas limitam/banem quem faz arbitragem; margens reais sao pequenas (1-5%).\n"
        "- Confirme as odds AO VIVO nas casas antes de apostar qualquer centavo.[/dim]"
    )

    if args.salvar:
        payload = {
            "periodo": {"de": d_from, "ate": d_to},
            "jogos_com_odds": n_jogos,
            "max_bookmakers_por_jogo": n_bookmakers,
            "surebets": [
                {
                    "jogo": s.jogo, "liga": s.liga, "data": s.data, "mercado": s.mercado,
                    "lucro_pct": round(s.lucro_pct, 3),
                    "pernas": [
                        {**p, "stake": round(args.banca * (1.0 / p["odd"]) / s.inv, 2)}
                        for p in s.pernas
                    ],
                }
                for s in surebets
            ],
        }
        with open(args.salvar, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        console.print(f"[green]Salvo em {args.salvar}[/green]")


if __name__ == "__main__":
    main()
