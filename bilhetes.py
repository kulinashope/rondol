"""Montagem de bilhetes a partir das selecoes analisadas.

Estrategias:
  - seguro      : prioriza maior probabilidade do modelo (confianca)
  - valor       : prioriza maior value bet (EV)
  - equilibrado : combina confianca e valor

Um bilhete combinado (acumulada) multiplica as odds e (assumindo
independencia) multiplica as probabilidades das selecoes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import reduce

from analysis import Selection, dedupe_one_per_match


@dataclass
class Bilhete:
    tipo: str
    selections: list[Selection] = field(default_factory=list)

    @property
    def odd_total(self) -> float:
        return round(reduce(lambda a, s: a * s.odd, self.selections, 1.0), 2)

    @property
    def prob_total(self) -> float:
        """Probabilidade combinada (%) assumindo eventos independentes."""
        p = reduce(lambda a, s: a * (s.model_prob / 100.0), self.selections, 1.0)
        return round(p * 100.0, 2)

    @property
    def valor(self) -> float:
        """EV combinado: (prob/100) * odd_total - 1."""
        return round((self.prob_total / 100.0) * self.odd_total - 1.0, 4)

    def retorno(self, stake: float) -> float:
        return round(stake * self.odd_total, 2)


def _sort_key(estrategia: str):
    if estrategia == "valor":
        return lambda s: (s.value, s.model_prob)
    if estrategia == "seguro":
        return lambda s: (s.model_prob, s.value)
    # equilibrado: pondera confianca pelo valor
    return lambda s: (s.model_prob * (1.0 + max(s.value, 0)), s.model_prob)


def montar_bilhetes(
    selections: list[Selection],
    estrategia: str = "equilibrado",
    selecoes_por_bilhete: int = 3,
    max_bilhetes: int = 5,
) -> list[Bilhete]:
    """Gera bilhetes ordenando as selecoes pela estrategia escolhida.

    - selecoes_por_bilhete = 1  -> bilhetes simples (uma aposta cada)
    - selecoes_por_bilhete > 1  -> acumuladas
    """
    # uma selecao por jogo evita apostas conflitantes no mesmo bilhete
    unicas = dedupe_one_per_match(selections)
    unicas.sort(key=_sort_key(estrategia), reverse=True)

    selecoes_por_bilhete = max(1, selecoes_por_bilhete)
    bilhetes: list[Bilhete] = []

    for i in range(0, len(unicas), selecoes_por_bilhete):
        grupo = unicas[i : i + selecoes_por_bilhete]
        # combinada precisa de pelo menos 2 selecoes; grupo de 1 e descartado
        if selecoes_por_bilhete > 1 and len(grupo) < 2:
            break
        bilhetes.append(Bilhete(tipo=estrategia, selections=grupo))
        if len(bilhetes) >= max_bilhetes:
            break

    return bilhetes


def montar_bilhetes_odd_alvo(
    selections: list[Selection],
    odd_alvo: float = 2.0,
    tolerancia: float = 0.15,
    estrategia: str = "equilibrado",
    max_selecoes: int = 6,
    min_selecoes: int = 2,
    max_bilhetes: int = 5,
) -> list[Bilhete]:
    """Monta bilhetes combinados (misturando jogos) mirando uma odd total.

    Estrategia gulosa: a cada passo escolhe a selecao que aproxima o produto
    das odds da odd-alvo, evitando ultrapassar o teto (odd_alvo * (1+tolerancia)).
    Cada jogo entra no maximo uma vez por bilhete e nenhum jogo se repete entre
    os bilhetes gerados.

    - odd_alvo:     odd total desejada (ex.: 2.0).
    - tolerancia:   faixa aceitavel em torno da alvo (0.15 = +-15%).
    - max_selecoes: teto de selecoes por bilhete (evita combinadas gigantes).
    - min_selecoes: minimo de jogos por bilhete (>=2 garante "misturar jogos").
    - max_bilhetes: quantos bilhetes tentar montar (cada um com jogos distintos).
    """
    unicas = dedupe_one_per_match(selections)
    unicas.sort(key=_sort_key(estrategia), reverse=True)

    low = odd_alvo * (1.0 - tolerancia)
    high = odd_alvo * (1.0 + tolerancia)
    max_selecoes = max(1, max_selecoes)
    min_selecoes = max(1, min(min_selecoes, max_selecoes))

    bilhetes: list[Bilhete] = []
    usados_global: set[str] = set()

    for _ in range(max(1, max_bilhetes)):
        escolhidas: list[Selection] = []
        usados_local: set[str] = set()
        produto = 1.0

        # continua enquanto faltam jogos para o minimo OU ainda nao atingiu o piso
        while len(escolhidas) < max_selecoes and (
            len(escolhidas) < min_selecoes or produto < low
        ):
            melhor: Selection | None = None
            melhor_score: float | None = None
            for s in unicas:
                if s.match_id in usados_global or s.match_id in usados_local:
                    continue
                novo = produto * s.odd
                # penaliza ultrapassar o teto, mas nao proibe (pode ser inevitavel)
                penalidade = 0.0 if novo <= high else (novo - high) * 10.0
                score = abs(odd_alvo - novo) + penalidade
                if melhor_score is None or score < melhor_score:
                    melhor, melhor_score = s, score
            if melhor is None:
                break  # acabaram as selecoes disponiveis
            escolhidas.append(melhor)
            usados_local.add(melhor.match_id)
            produto *= melhor.odd

        # so aceita o bilhete se tem o minimo de jogos e chegou perto da alvo
        if len(escolhidas) >= min_selecoes and produto >= low:
            bilhetes.append(
                Bilhete(tipo=f"odd-alvo {odd_alvo:.2f}", selections=escolhidas)
            )
            usados_global.update(usados_local)
        else:
            break  # nao da pra montar mais bilhetes na faixa

    return bilhetes
