"""Configuracoes globais carregadas do ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Base oficial da apifootball.com (NAO confundir com api-football.com)
BASE_URL = "https://apiv3.apifootball.com/"


@dataclass(frozen=True)
class Settings:
    api_key: str
    timezone: str

    @staticmethod
    def load() -> "Settings":
        key = os.getenv("APIFOOTBALL_KEY", "").strip()
        if not key or key == "coloque_sua_chave_aqui":
            raise SystemExit(
                "ERRO: defina APIFOOTBALL_KEY no arquivo .env "
                "(copie o .env.example para .env e cole sua chave)."
            )
        tz = os.getenv("APIFOOTBALL_TIMEZONE", "America/Sao_Paulo").strip()
        return Settings(api_key=key, timezone=tz)


# --- Parametros padrao da estrategia (ajustaveis pela CLI) ---

# Probabilidade minima (em %) do modelo para considerar uma selecao "confiavel"
DEFAULT_MIN_PROB = 50.0

# Valor minimo (EV) para considerar value bet. 0.05 = 5% de valor esperado positivo
DEFAULT_MIN_VALUE = 0.05

# Faixa de odd aceitavel para entrar num bilhete (evita odds absurdas/zeradas)
MIN_ODD = 1.20
MAX_ODD = 15.0
