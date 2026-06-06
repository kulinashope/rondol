"""
====================================================================
 GERADOR DE BILHETES - apifootball.com  (versao arquivo unico)
====================================================================

Uso pessoal. Busca previsoes + odds da apifootball.com e monta bilhetes
de aposta automaticamente (confianca e/ou value bet).

--------------------------------------------------------------------
 COMO USAR (Windows) - passo a passo:
--------------------------------------------------------------------
 1) Crie uma pasta, por ex.:  C:\\apostas
 2) Salve este arquivo dentro dela como:  bilhetes_completo.py
 3) Cole sua chave da apifootball.com na variavel API_KEY abaixo
    (ou defina a variavel de ambiente APIFOOTBALL_KEY).
 4) Abra o Prompt de Comando e rode:

        cd C:\\apostas
        pip install requests
        python bilhetes_completo.py

 Exemplos:
        python bilhetes_completo.py --estrategia valor
        python bilhetes_completo.py --simples
        python bilhetes_completo.py --dias 2 --selecoes 4
        python bilhetes_completo.py --raw odds      (mostra o JSON cru)

 Aviso: previsoes e value bets sao estimativas, nao garantias.
        Aposte com responsabilidade.
====================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import reduce
from typing import Any, Iterable

try:
    import requests
except ImportError:
    print("ERRO: o pacote 'requests' nao esta instalado.")
    print("Rode no Prompt de Comando:  pip install requests")
    sys.exit(1)


# =================================================================== #
# CONFIGURACAO
# =================================================================== #

# >>> COLE SUA CHAVE AQUI (entre as aspas) <<<
# Se preferir, deixe vazio e use a variavel de ambiente APIFOOTBALL_KEY
# ou um arquivo .env com a linha: APIFOOTBALL_KEY=sua_chave
API_KEY = ""

TIMEZONE = "America/Sao_Paulo"
BASE_URL = "https://apiv3.apifootball.com/"

# Parametros padrao da estrategia
DEFAULT_MIN_PROB = 50.0     # probabilidade minima do modelo (%)
DEFAULT_MIN_VALUE = 0.05    # value bet (EV) minimo (0.05 = 5%)
MIN_ODD = 1.20              # faixa de odd aceitavel
MAX_ODD = 15.0


def resolve_api_key() -> str:
    """Procura a chave: 1) variavel API_KEY  2) env  3) arquivo .env."""
    if API_KEY.strip():
        return API_KEY.strip()
    env = os.getenv("APIFOOTBALL_KEY", "").strip()
    if env:
        return env
    # tenta ler um arquivo .env simples no mesmo diretorio
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("APIFOOTBALL_KEY"):
                    return line.split("=", 1)[1].strip()
    return ""


# =================================================================== #
# CLIENTE DA API
# =================================================================== #
class ApiFootballError(RuntimeError):
    pass


class ApiFootballClient:
    def __init__(self, api_key: str, timezone: str, timeout: int = 30) -> None:
        self._key = api_key
        self._tz = timezone
        self._timeout = timeout
        self._session = requests.Session()

    def _request(self, action: str, **params: Any) -> Any:
        query = {
            "action": action,
            "APIkey": self._key,
            **{k: v for k, v in params.items() if v is not None},
        }
        try:
            resp = self._session.get(BASE_URL, params=query, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ApiFootballError(f"Falha de conexao com a API: {exc}") from exc
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiFootballError(f"Resposta nao e JSON valido: {resp.text[:200]}") from exc
        if isinstance(data, dict) and "error" in data:
            raise ApiFootballError(f"API retornou erro {data.get('error')}: {data.get('message')}")
        return data

    def raw(self, action: str, **params: Any) -> Any:
        return self._request(action, **params)

    def get_predictions(self, d_from: str, d_to: str, league_id: str | None = None) -> list[dict]:
        data = self._request("get_predictions", **{"from": d_from, "to": d_to},
                             league_id=league_id, timezone=self._tz)
        return data if isinstance(data, list) else []

    def get_odds(self, d_from: str | None = None, d_to: str | None = None,
                 match_id: str | None = None) -> Any:
        return self._request("get_odds", match_id=match_id, **{"from": d_from, "to": d_to})


# =================================================================== #
# ANALISE (previsao x odds -> value bet)
# =================================================================== #
MARKETS: dict[str, dict[str, Any]] = {
    # 1X2
    "HOME":    {"label": "Casa (1)",          "prob_keys": ["prob_HW", "prob_1", "prob_home"],            "odd_keys": ["odd_1", "1", "home"]},
    "DRAW":    {"label": "Empate (X)",         "prob_keys": ["prob_D", "prob_X", "prob_draw"],             "odd_keys": ["odd_x", "x", "draw"]},
    "AWAY":    {"label": "Fora (2)",           "prob_keys": ["prob_AW", "prob_2", "prob_away"],            "odd_keys": ["odd_2", "2", "away"]},
    # Dupla chance
    "DC_1X":   {"label": "Dupla chance 1X",    "prob_keys": ["prob_HW_D"],                                 "odd_keys": ["1x", "odd_1x", "dc_1x"]},
    "DC_X2":   {"label": "Dupla chance X2",    "prob_keys": ["prob_AW_D"],                                 "odd_keys": ["x2", "odd_x2", "dc_x2"]},
    "DC_12":   {"label": "Dupla chance 12",    "prob_keys": ["prob_HW_AW"],                                "odd_keys": ["12", "odd_12", "dc_12"]},
    # Over/Under
    "OVER15":  {"label": "Mais de 1.5 gols",   "prob_keys": ["prob_O_1"],                                  "odd_keys": ["o+1.5", "over_15", "o_15", "o1.5"]},
    "UNDER15": {"label": "Menos de 1.5 gols",  "prob_keys": ["prob_U_1"],                                  "odd_keys": ["u+1.5", "under_15", "u_15", "u1.5"]},
    "OVER25":  {"label": "Mais de 2.5 gols",   "prob_keys": ["prob_O", "prob_over", "prob_o25"],           "odd_keys": ["o+2.5", "over_25", "o_25", "odd_over_25", "o2.5"]},
    "UNDER25": {"label": "Menos de 2.5 gols",  "prob_keys": ["prob_U", "prob_under", "prob_u25"],          "odd_keys": ["u+2.5", "under_25", "u_25", "odd_under_25", "u2.5"]},
    "OVER35":  {"label": "Mais de 3.5 gols",   "prob_keys": ["prob_O_3"],                                  "odd_keys": ["o+3.5", "over_35", "o_35", "o3.5"]},
    "UNDER35": {"label": "Menos de 3.5 gols",  "prob_keys": ["prob_U_3"],                                  "odd_keys": ["u+3.5", "under_35", "u_35", "u3.5"]},
    # Ambas marcam
    "BTS_YES": {"label": "Ambas marcam: Sim",  "prob_keys": ["prob_bts", "prob_btts", "prob_bts_yes"],     "odd_keys": ["bts_yes", "bts_y", "gg", "btts_yes"]},
    "BTS_NO":  {"label": "Ambas marcam: Nao",  "prob_keys": ["prob_ots", "prob_bts_no", "prob_ng"],        "odd_keys": ["bts_no", "bts_n", "ng", "btts_no"]},
}


@dataclass
class Selection:
    match_id: str
    match_label: str
    league: str
    kickoff: str
    market_code: str
    market_label: str
    model_prob: float
    odd: float
    bookmaker: str = ""
    implied_prob: float = 0.0
    value: float = 0.0

    def __post_init__(self) -> None:
        if self.odd > 0:
            self.implied_prob = round(100.0 / self.odd, 2)
            self.value = round((self.model_prob / 100.0) * self.odd - 1.0, 4)


def _to_float(value: Any) -> float | None:
    try:
        f = float(str(value).replace(",", "."))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _first_prob(prediction: dict, keys: Iterable[str]) -> float | None:
    for key in keys:
        if key in prediction:
            val = _to_float(prediction[key])
            if val is not None:
                return val
    return None


def _index_odds_by_match(odds_response: Any) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}

    def add(record: dict) -> None:
        if not isinstance(record, dict):
            return
        mid = str(record.get("match_id", "")).strip()
        if mid:
            index.setdefault(mid, []).append(record)

    if isinstance(odds_response, list):
        for rec in odds_response:
            add(rec)
    elif isinstance(odds_response, dict):
        for key, value in odds_response.items():
            if isinstance(value, list):
                for rec in value:
                    if isinstance(rec, dict):
                        rec.setdefault("match_id", key)
                        add(rec)
            elif isinstance(value, dict):
                value.setdefault("match_id", key)
                add(value)
    return index


def _best_odd(records: list[dict], odd_keys: Iterable[str]) -> tuple[float, str]:
    best, best_bk = 0.0, ""
    keyset = list(odd_keys)
    for rec in records:
        lowered = {str(k).lower(): v for k, v in rec.items()}
        for key in keyset:
            raw = rec.get(key, lowered.get(key.lower()))
            odd = _to_float(raw)
            if odd is not None and MIN_ODD <= odd <= MAX_ODD and odd > best:
                best = odd
                best_bk = str(rec.get("bookmaker") or rec.get("bk_name") or rec.get("name") or "")
    return best, best_bk


def fair_odd(prob: float) -> float:
    return round(100.0 / prob, 2) if prob > 0 else 0.0


def build_selections(predictions: list[dict], odds_response: Any,
                     min_prob: float, min_value: float | None,
                     use_fair_odds: bool = False) -> list[Selection]:
    odds_index = {} if use_fair_odds else _index_odds_by_match(odds_response)
    selections: list[Selection] = []
    for pred in predictions:
        match_id = str(pred.get("match_id", "")).strip()
        records = odds_index.get(match_id, [])
        home = pred.get("match_hometeam_name") or pred.get("home") or "Casa"
        away = pred.get("match_awayteam_name") or pred.get("away") or "Fora"
        match_label = f"{home} x {away}"
        league = pred.get("league_name") or pred.get("country_name") or ""
        kickoff = f"{pred.get('match_date', '')} {pred.get('match_time', '')}".strip()

        for code, spec in MARKETS.items():
            prob = _first_prob(pred, spec["prob_keys"])
            if prob is None or prob < min_prob:
                continue
            if use_fair_odds:
                odd, bookmaker = fair_odd(prob), "(odd justa)"
            else:
                odd, bookmaker = _best_odd(records, spec["odd_keys"])
            if odd <= 0:
                continue
            sel = Selection(match_id, match_label, league, kickoff, code,
                            spec["label"], prob, odd, bookmaker)
            if not use_fair_odds and min_value is not None and sel.value < min_value:
                continue
            selections.append(sel)
    return selections


def dedupe_one_per_match(selections: list[Selection]) -> list[Selection]:
    best_by_match: dict[str, Selection] = {}
    for sel in selections:
        cur = best_by_match.get(sel.match_id)
        if cur is None or (sel.value, sel.model_prob) > (cur.value, cur.model_prob):
            best_by_match[sel.match_id] = sel
    return list(best_by_match.values())


# =================================================================== #
# BILHETES
# =================================================================== #
@dataclass
class Bilhete:
    tipo: str
    selections: list[Selection] = field(default_factory=list)

    @property
    def odd_total(self) -> float:
        return round(reduce(lambda a, s: a * s.odd, self.selections, 1.0), 2)

    @property
    def prob_total(self) -> float:
        p = reduce(lambda a, s: a * (s.model_prob / 100.0), self.selections, 1.0)
        return round(p * 100.0, 2)

    @property
    def valor(self) -> float:
        return round((self.prob_total / 100.0) * self.odd_total - 1.0, 4)

    def retorno(self, stake: float) -> float:
        return round(stake * self.odd_total, 2)


def _sort_key(estrategia: str):
    if estrategia == "valor":
        return lambda s: (s.value, s.model_prob)
    if estrategia == "seguro":
        return lambda s: (s.model_prob, s.value)
    return lambda s: (s.model_prob * (1.0 + max(s.value, 0)), s.model_prob)


def montar_bilhetes(selections: list[Selection], estrategia: str = "equilibrado",
                    selecoes_por_bilhete: int = 3, max_bilhetes: int = 5) -> list[Bilhete]:
    unicas = dedupe_one_per_match(selections)
    unicas.sort(key=_sort_key(estrategia), reverse=True)
    selecoes_por_bilhete = max(1, selecoes_por_bilhete)
    bilhetes: list[Bilhete] = []
    for i in range(0, len(unicas), selecoes_por_bilhete):
        grupo = unicas[i:i + selecoes_por_bilhete]
        if selecoes_por_bilhete > 1 and len(grupo) < 2:
            break
        bilhetes.append(Bilhete(tipo=estrategia, selections=grupo))
        if len(bilhetes) >= max_bilhetes:
            break
    return bilhetes


# =================================================================== #
# SAIDA (texto puro, funciona em qualquer terminal)
# =================================================================== #
def imprimir_bilhete(idx: int, b: Bilhete, stake: float) -> None:
    print("=" * 70)
    print(f" BILHETE #{idx}  [{b.tipo.upper()}]")
    print(f"   Odd total: {b.odd_total:.2f}   |   Prob. modelo: {b.prob_total:.1f}%   |   "
          f"Valor(EV): {b.valor:+.1%}")
    print(f"   Stake {stake:.2f} -> Retorno potencial: {b.retorno(stake):.2f}")
    print("-" * 70)
    for s in b.selections:
        print(f"   - {s.match_label}  [{s.league}]  {s.kickoff}")
        print(f"       {s.market_label}  @ {s.odd:.2f}   "
              f"(prob {s.model_prob:.0f}% | EV {s.value:+.1%} | casa {s.bookmaker or '-'})")
    print()


def salvar(path: str, bilhetes: list[Bilhete], stake: float) -> None:
    if path.endswith(".json"):
        payload = [{
            "tipo": b.tipo, "odd_total": b.odd_total, "prob_total_pct": b.prob_total,
            "valor_ev": b.valor, "stake": stake, "retorno": b.retorno(stake),
            "selecoes": [{
                "jogo": s.match_label, "liga": s.league, "quando": s.kickoff,
                "aposta": s.market_label, "odd": s.odd, "prob_modelo_pct": s.model_prob,
                "prob_implicita_pct": s.implied_prob, "valor_ev": s.value, "bookmaker": s.bookmaker,
            } for s in b.selections],
        } for b in bilhetes]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    else:
        lines: list[str] = []
        for i, b in enumerate(bilhetes, 1):
            lines.append(f"== Bilhete #{i} ({b.tipo}) | Odd {b.odd_total:.2f} | "
                         f"Prob {b.prob_total:.1f}% | EV {b.valor:+.1%} | "
                         f"Retorno {b.retorno(stake):.2f} (stake {stake:.2f}) ==")
            for s in b.selections:
                lines.append(f"  - {s.match_label} [{s.league}] {s.kickoff} | "
                             f"{s.market_label} @ {s.odd:.2f} "
                             f"(prob {s.model_prob:.0f}%, EV {s.value:+.1%}, casa {s.bookmaker or '-'})")
            lines.append("")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    print(f"[OK] Salvo em {path}")


# =================================================================== #
# CLI
# =================================================================== #
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gera bilhetes de apostas (apifootball.com)")
    p.add_argument("--data", help="Data inicial YYYY-MM-DD (padrao: hoje)")
    p.add_argument("--ate", help="Data final YYYY-MM-DD (padrao: data inicial)")
    p.add_argument("--dias", type=int, default=1, help="Qtd de dias (padrao: 1)")
    p.add_argument("--liga", help="Filtrar por league_id")
    p.add_argument("--estrategia", choices=["seguro", "valor", "equilibrado"], default="equilibrado")
    p.add_argument("--selecoes", type=int, default=3, help="Selecoes por bilhete (padrao: 3)")
    p.add_argument("--simples", action="store_true", help="Bilhetes simples (1 aposta cada)")
    p.add_argument("--bilhetes", type=int, default=5, help="Maximo de bilhetes (padrao: 5)")
    p.add_argument("--min-prob", type=float, default=DEFAULT_MIN_PROB)
    p.add_argument("--min-valor", type=float, default=DEFAULT_MIN_VALUE)
    p.add_argument("--sem-valor", action="store_true", help="Ignora o filtro de value bet")
    p.add_argument("--sem-odds", action="store_true",
                   help="Nao usa odds: bilhetes so com a previsao (odd justa = 100/prob)")
    p.add_argument("--stake", type=float, default=10.0, help="Valor por bilhete (padrao: 10)")
    p.add_argument("--salvar", help="Salva em arquivo .txt ou .json")
    p.add_argument("--raw", choices=["events", "predictions", "odds"],
                   help="Mostra o JSON cru de um endpoint e sai (depuracao)")
    return p.parse_args()


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    start = date.fromisoformat(args.data) if args.data else date.today()
    end = date.fromisoformat(args.ate) if args.ate else start + timedelta(days=max(1, args.dias) - 1)
    return start.isoformat(), end.isoformat()


def main() -> None:
    args = parse_args()
    key = resolve_api_key()
    if not key:
        print("ERRO: nenhuma chave da API encontrada.")
        print("Cole sua chave na variavel API_KEY no topo do arquivo, ou crie um")
        print("arquivo .env com a linha:  APIFOOTBALL_KEY=sua_chave")
        sys.exit(1)

    client = ApiFootballClient(key, TIMEZONE)
    d_from, d_to = resolve_dates(args)

    print()
    print("#" * 70)
    print(f"# GERADOR DE BILHETES - apifootball.com")
    print(f"# Periodo: {d_from} a {d_to}" + (f"  | Liga: {args.liga}" if args.liga else ""))
    print(f"# Estrategia: {args.estrategia}  | Min prob: {args.min_prob:.0f}%  | "
          + ("Modo: SEM ODDS (odd justa)" if args.sem_odds
             else f"Min valor: {'(desligado)' if args.sem_valor else f'{args.min_valor:+.1%}'}"))
    print("#" * 70)
    print()

    try:
        if args.raw == "events":
            data = client.raw("get_events", **{"from": d_from, "to": d_to}, league_id=args.liga)
        elif args.raw == "predictions":
            data = client.raw("get_predictions", **{"from": d_from, "to": d_to}, league_id=args.liga)
        elif args.raw == "odds":
            data = client.raw("get_odds", **{"from": d_from, "to": d_to})
        else:
            data = None

        if args.raw:
            preview = data[:3] if isinstance(data, list) else data
            print(json.dumps(preview, ensure_ascii=False, indent=2))
            if isinstance(data, list):
                print(f"\nTotal de registros: {len(data)} (mostrando ate 3)")
            return

        print("Buscando previsoes...")
        predictions = client.get_predictions(d_from, d_to, league_id=args.liga)
        if not predictions:
            print("Nenhuma previsao retornada para o periodo/liga. "
                  "Verifique se ha jogos na data ou tente outra liga.")
            return

        if args.sem_odds:
            selections = build_selections(predictions, None, args.min_prob, None,
                                          use_fair_odds=True)
        else:
            print("Buscando odds...")
            odds = client.get_odds(d_from=d_from, d_to=d_to)
            min_value = None if args.sem_valor else args.min_valor
            selections = build_selections(predictions, odds, args.min_prob, min_value)

        if not selections:
            print("Nenhuma selecao passou nos filtros. "
                  "Tente baixar --min-prob, usar --sem-valor/--sem-odds ou reduzir --min-valor.")
            return

        bilhetes = montar_bilhetes(
            selections, estrategia=args.estrategia,
            selecoes_por_bilhete=1 if args.simples else args.selecoes,
            max_bilhetes=args.bilhetes,
        )
        if not bilhetes:
            print("Selecoes insuficientes para montar bilhetes.")
            return

        print(f"\n{len(bilhetes)} bilhete(s) gerado(s) a partir de "
              f"{len(selections)} selecao(oes) analisada(s).\n")
        for i, b in enumerate(bilhetes, 1):
            imprimir_bilhete(i, b, args.stake)

        print("Aviso: previsoes e value bets sao estimativas, nao garantias. "
              "Aposte com responsabilidade.")

        if args.salvar:
            salvar(args.salvar, bilhetes, args.stake)

    except ApiFootballError as exc:
        print(f"ERRO: {exc}")


if __name__ == "__main__":
    main()
