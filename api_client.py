"""Cliente HTTP para a API da apifootball.com.

Todos os endpoints sao acionados via querystring:
    https://apiv3.apifootball.com/?action=<acao>&APIkey=<chave>&...

A API costuma responder:
  - lista de objetos (sucesso)
  - dict {"error": <codigo>, "message": <texto>} (erro logico)
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Iterator

import requests

from config import BASE_URL, Settings


def _date_windows(
    date_from: str, date_to: str, max_days: int = 5
) -> Iterator[tuple[str, str]]:
    """Quebra um intervalo em janelas de no maximo `max_days` dias.

    A apifootball.com limita varios endpoints a 5 dias por requisicao
    (erro 201: "Date interval is to long!"). Iterar em janelas resolve isso.
    """
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start:
        start, end = end, start
    cur = start
    while cur <= end:
        w_end = min(cur + timedelta(days=max_days - 1), end)
        yield cur.isoformat(), w_end.isoformat()
        cur = w_end + timedelta(days=1)


class ApiFootballError(RuntimeError):
    """Erro retornado pela API ou de comunicacao."""


class ApiFootballClient:
    def __init__(self, settings: Settings, timeout: int = 30) -> None:
        self._settings = settings
        self._timeout = timeout
        self._session = requests.Session()

    # ------------------------------------------------------------------ #
    # Baixo nivel
    # ------------------------------------------------------------------ #
    def _request(self, action: str, **params: Any) -> Any:
        query = {
            "action": action,
            "APIkey": self._settings.api_key,
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
            raise ApiFootballError(
                f"Resposta nao e JSON valido: {resp.text[:200]}"
            ) from exc

        # Erro logico da API costuma vir como dict com a chave "error"
        if isinstance(data, dict) and "error" in data:
            raise ApiFootballError(
                f"API retornou erro {data.get('error')}: {data.get('message')}"
            )
        return data

    def raw(self, action: str, **params: Any) -> Any:
        """Retorna a resposta crua de qualquer action (util para depurar)."""
        return self._request(action, **params)

    def _request_opcional(self, action: str, **params: Any) -> Any:
        """Como _request, mas trata 'sem dados' (404 No event/odds found) como vazio.

        Util ao iterar janelas de datas: algumas podem nao ter jogos (off-season)
        e a API responde erro logico 404 — nesse caso devolvemos lista vazia em
        vez de derrubar toda a busca. Outros erros (ex.: chave invalida) sobem.
        """
        try:
            return self._request(action, **params)
        except ApiFootballError as exc:
            msg = str(exc).lower()
            if "no event found" in msg or "no odds" in msg or "not found" in msg or "no data" in msg:
                return []
            raise

    # ------------------------------------------------------------------ #
    # Endpoints de alto nivel
    # ------------------------------------------------------------------ #
    def get_events(
        self,
        date_from: str,
        date_to: str,
        league_id: str | None = None,
        match_live: bool = False,
    ) -> list[dict]:
        """Jogos (fixtures/resultados/livescore) em um intervalo de datas.

        Intervalos maiores que 5 dias sao quebrados automaticamente em janelas.
        """
        out: list[dict] = []
        for d_from, d_to in _date_windows(date_from, date_to):
            data = self._request_opcional(
                "get_events",
                **{"from": d_from, "to": d_to},
                league_id=league_id,
                match_live="1" if match_live else None,
                timezone=self._settings.timezone,
            )
            if isinstance(data, list):
                out.extend(data)
        return out

    def get_predictions(
        self,
        date_from: str,
        date_to: str,
        league_id: str | None = None,
    ) -> list[dict]:
        """Previsoes matematicas (probabilidades 1x2, over/under, btts...).

        Intervalos maiores que 5 dias sao quebrados automaticamente em janelas.
        """
        out: list[dict] = []
        for d_from, d_to in _date_windows(date_from, date_to):
            data = self._request_opcional(
                "get_predictions",
                **{"from": d_from, "to": d_to},
                league_id=league_id,
                timezone=self._settings.timezone,
            )
            if isinstance(data, list):
                out.extend(data)
        return out

    def get_odds(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        match_id: str | None = None,
    ) -> Any:
        """Odds dos bookmakers. Pode filtrar por intervalo ou por match_id.

        Por intervalo, janelas de >5 dias sao quebradas e concatenadas (lista).
        """
        if match_id is not None or date_from is None or date_to is None:
            return self._request(
                "get_odds",
                match_id=match_id,
                **{"from": date_from, "to": date_to},
            )
        out: list[dict] = []
        for d_from, d_to in _date_windows(date_from, date_to):
            data = self._request_opcional("get_odds", **{"from": d_from, "to": d_to})
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict):
                        value.setdefault("match_id", key)
                        out.append(value)
        return out

    def get_standings(self, league_id: str) -> list[dict]:
        data = self._request("get_standings", league_id=league_id)
        return data if isinstance(data, list) else []

    def get_h2h(self, first_team: str, second_team: str) -> Any:
        return self._request(
            "get_H2H", firstTeam=first_team, secondTeam=second_team
        )

    def get_leagues(self, country_id: str | None = None) -> list[dict]:
        data = self._request("get_leagues", country_id=country_id)
        return data if isinstance(data, list) else []
