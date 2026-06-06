"""Analise: junta previsoes (modelo) com odds (bookmakers) e calcula value bets.

O parsing e proposital flexivel: os nomes exatos de alguns campos de odds
variam na apifootball.com, entao tentamos varios candidatos por mercado e,
para depurar, ha o comando `--raw` na CLI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from config import MAX_ODD, MIN_ODD


# --------------------------------------------------------------------------- #
# Definicao dos mercados suportados
# --------------------------------------------------------------------------- #
# Para cada selecao: rotulo amigavel, campos candidatos de probabilidade
# (no get_predictions) e campos candidatos de odd (no get_odds).
MARKETS: dict[str, dict[str, Any]] = {
    # --- 1X2 (resultado final) ---
    "HOME": {
        "label": "Casa (1)",
        "prob_keys": ["prob_HW", "prob_1", "prob_home"],
        "odd_keys": ["odd_1", "1", "home"],
    },
    "DRAW": {
        "label": "Empate (X)",
        "prob_keys": ["prob_D", "prob_X", "prob_draw"],
        "odd_keys": ["odd_x", "x", "draw"],
    },
    "AWAY": {
        "label": "Fora (2)",
        "prob_keys": ["prob_AW", "prob_2", "prob_away"],
        "odd_keys": ["odd_2", "2", "away"],
    },
    # --- Dupla chance ---
    "DC_1X": {
        "label": "Dupla chance 1X (casa ou empate)",
        "prob_keys": ["prob_HW_D"],
        "odd_keys": ["1x", "odd_1x", "dc_1x", "home_draw"],
    },
    "DC_X2": {
        "label": "Dupla chance X2 (empate ou fora)",
        "prob_keys": ["prob_AW_D"],
        "odd_keys": ["x2", "odd_x2", "dc_x2", "draw_away"],
    },
    "DC_12": {
        "label": "Dupla chance 12 (casa ou fora)",
        "prob_keys": ["prob_HW_AW"],
        "odd_keys": ["12", "odd_12", "dc_12", "home_away"],
    },
    # --- Over/Under ---
    "OVER15": {
        "label": "Mais de 1.5 gols",
        "prob_keys": ["prob_O_1"],
        "odd_keys": ["o+1.5", "over_15", "o_15", "o1.5"],
    },
    "UNDER15": {
        "label": "Menos de 1.5 gols",
        "prob_keys": ["prob_U_1"],
        "odd_keys": ["u+1.5", "under_15", "u_15", "u1.5"],
    },
    "OVER25": {
        "label": "Mais de 2.5 gols",
        "prob_keys": ["prob_O", "prob_over", "prob_o25", "prob_O_25"],
        "odd_keys": ["o+2.5", "over_25", "o_25", "odd_over_25", "o2.5"],
    },
    "UNDER25": {
        "label": "Menos de 2.5 gols",
        "prob_keys": ["prob_U", "prob_under", "prob_u25", "prob_U_25"],
        "odd_keys": ["u+2.5", "under_25", "u_25", "odd_under_25", "u2.5"],
    },
    "OVER35": {
        "label": "Mais de 3.5 gols",
        "prob_keys": ["prob_O_3"],
        "odd_keys": ["o+3.5", "over_35", "o_35", "o3.5"],
    },
    "UNDER35": {
        "label": "Menos de 3.5 gols",
        "prob_keys": ["prob_U_3"],
        "odd_keys": ["u+3.5", "under_35", "u_35", "u3.5"],
    },
    # --- Ambas marcam ---
    "BTS_YES": {
        "label": "Ambas marcam: Sim",
        "prob_keys": ["prob_bts", "prob_btts", "prob_bts_yes", "prob_gg"],
        "odd_keys": ["bts_yes", "bts_y", "gg", "btts_yes"],
    },
    "BTS_NO": {
        "label": "Ambas marcam: Nao",
        "prob_keys": ["prob_ots", "prob_bts_no", "prob_ng"],
        "odd_keys": ["bts_no", "bts_n", "ng", "btts_no"],
    },
}


@dataclass
class Selection:
    """Uma aposta possivel (um mercado de um jogo)."""

    match_id: str
    match_label: str
    league: str
    kickoff: str
    market_code: str
    market_label: str
    model_prob: float          # probabilidade do modelo (%)
    odd: float                 # melhor odd encontrada
    bookmaker: str = ""
    implied_prob: float = 0.0  # probabilidade implicita da odd (%)
    value: float = 0.0         # EV: (prob/100)*odd - 1

    def __post_init__(self) -> None:
        if self.odd > 0:
            self.implied_prob = round(100.0 / self.odd, 2)
            self.value = round((self.model_prob / 100.0) * self.odd - 1.0, 4)


# --------------------------------------------------------------------------- #
# Helpers de parsing
# --------------------------------------------------------------------------- #
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
    """Normaliza o retorno de get_odds em {match_id: [registros de bookmaker]}.

    A apifootball.com pode devolver uma lista plana OU um dict indexado por
    match_id. Cobrimos os dois casos.
    """
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
    """Maior odd disponivel entre os bookmakers para uma selecao (melhor valor)."""
    best = 0.0
    best_bk = ""
    keyset = list(odd_keys)
    for rec in records:
        # tenta as chaves candidatas, e tambem variacoes em minusculo
        lowered = {str(k).lower(): v for k, v in rec.items()}
        for key in keyset:
            raw = rec.get(key, lowered.get(key.lower()))
            odd = _to_float(raw)
            if odd is not None and MIN_ODD <= odd <= MAX_ODD and odd > best:
                best = odd
                best_bk = str(
                    rec.get("bookmaker")
                    or rec.get("bk_name")
                    or rec.get("name")
                    or ""
                )
    return best, best_bk


def _match_label(pred: dict) -> str:
    home = pred.get("match_hometeam_name") or pred.get("home") or "Casa"
    away = pred.get("match_awayteam_name") or pred.get("away") or "Fora"
    return f"{home} x {away}"


def _kickoff(pred: dict) -> str:
    date = pred.get("match_date", "")
    time = pred.get("match_time", "")
    return f"{date} {time}".strip()


# --------------------------------------------------------------------------- #
# Construcao das selecoes
# --------------------------------------------------------------------------- #
def fair_odd(prob: float) -> float:
    """Odd justa correspondente a uma probabilidade (%) do modelo."""
    return round(100.0 / prob, 2) if prob > 0 else 0.0


def build_selections(
    predictions: list[dict],
    odds_response: Any,
    min_prob: float,
    min_value: float | None,
    use_fair_odds: bool = False,
) -> list[Selection]:
    """Cruza previsoes com odds e devolve selecoes que passam nos filtros.

    - min_prob: probabilidade minima do modelo (%).
    - min_value: se informado, exige value bet (EV) >= esse valor.
                 Se None, ignora o filtro de valor.
    - use_fair_odds: modo "sem odds" -> usa a odd justa (100/prob) e ignora
                     os bookmakers. Util para gerar palpites so com a previsao.
    """
    odds_index = {} if use_fair_odds else _index_odds_by_match(odds_response)
    selections: list[Selection] = []

    for pred in predictions:
        match_id = str(pred.get("match_id", "")).strip()
        records = odds_index.get(match_id, [])
        match_label = _match_label(pred)
        league = pred.get("league_name") or pred.get("country_name") or ""
        kickoff = _kickoff(pred)

        for code, spec in MARKETS.items():
            prob = _first_prob(pred, spec["prob_keys"])
            if prob is None or prob < min_prob:
                continue

            if use_fair_odds:
                odd, bookmaker = fair_odd(prob), "(odd justa)"
            else:
                odd, bookmaker = _best_odd(records, spec["odd_keys"])
            if odd <= 0:
                continue  # sem odd utilizavel para este mercado

            sel = Selection(
                match_id=match_id,
                match_label=match_label,
                league=league,
                kickoff=kickoff,
                market_code=code,
                market_label=spec["label"],
                model_prob=prob,
                odd=odd,
                bookmaker=bookmaker,
            )

            if not use_fair_odds and min_value is not None and sel.value < min_value:
                continue

            selections.append(sel)

    return selections


def dedupe_one_per_match(selections: list[Selection]) -> list[Selection]:
    """Mantem apenas a melhor selecao por jogo (evita apostas conflitantes).

    Criterio: maior 'value'; em empate, maior probabilidade do modelo.
    """
    best_by_match: dict[str, Selection] = {}
    for sel in selections:
        cur = best_by_match.get(sel.match_id)
        if cur is None or (sel.value, sel.model_prob) > (cur.value, cur.model_prob):
            best_by_match[sel.match_id] = sel
    return list(best_by_match.values())
