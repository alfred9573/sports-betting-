"""Backtest walk-forward de anytime TD, con la linea base que hay que batir.

A diferencia de las props de yardas, aqui el resultado es binario (anoto o no),
asi que se evalua como cualquier probabilidad de un evento: Brier, log-loss y
calibracion por cubos. Pero eso solo no basta: un modelo que siempre dice "RB =
30%, WR = 22%" ya sale razonablemente calibrado sin saber nada del jugador. La
pregunta de verdad es si el modelo le GANA a esa tasa base por posicion. Por eso
cada prediccion guarda tambien la de la base, y el resumen da la mejora
relativa ("skill"): 1 - Brier_modelo / Brier_base.

LA BASE SE MIDE SOBRE EL MISMO UNIVERSO QUE EL MODELO. La primera version usaba
la tasa de TD de TODOS los jugadores de la posicion, suplentes incluidos, que
casi nunca anotan. Pero el modelo solo predice para jugadores con uso real, que
anotan bastante mas. Contra esa base rebajada el modelo parecia mejorar un 5-7%
cuando parte de esa mejora era solo "este jugador si juega". La base correcta
es la tasa de anotacion de los jugadores ELEGIBLES de la posicion: lo que diria
alguien que no sabe nada del jugador salvo que es un titular apostable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from betbot.models.anytime_td import AnytimeTDModel, tds_de, toques_de


@dataclass(frozen=True)
class PrediccionTD:
    season: int
    posicion: str
    p: float
    p_base: float
    anoto: bool


def walk_forward_anytime_td(
    filas: list[dict],
    modelo: AnytimeTDModel | None = None,
    progreso: Callable[[int], None] | None = None,
) -> list[PrediccionTD]:
    """`filas` en orden (season, week). Predice con lo anterior y luego aprende."""
    modelo = modelo or AnytimeTDModel()
    hist_tds: dict[str, list[float]] = {}
    hist_toques: dict[str, list[float]] = {}
    # Tasa base por posicion, SOLO sobre partidos que el modelo llego a predecir.
    base_pos: dict[str, list[float]] = {}   # [anotaron, n]
    salida: list[PrediccionTD] = []
    temporada_actual = None

    for fila in filas:
        if progreso is not None and fila["season"] != temporada_actual:
            temporada_actual = fila["season"]
            progreso(temporada_actual)
        pid = fila["player_id"]
        pos = fila["position"] or "?"
        tds = tds_de(fila)
        toques = toques_de(fila)
        h_td = hist_tds.setdefault(pid, [])
        h_to = hist_toques.setdefault(pid, [])

        p = modelo.predecir(h_td, h_to, pos)
        if p is not None:
            acum = base_pos.setdefault(pos, [0.0, 0.0])
            if acum[1] >= 50:
                # Laplace para que la base nunca sea 0 o 1 exactos.
                base = (acum[0] + 1.0) / (acum[1] + 2.0)
                salida.append(PrediccionTD(fila["season"], pos, p, base, tds > 0))
            acum[0] += 1.0 if tds > 0 else 0.0   # despues de usarla
            acum[1] += 1.0

        modelo.observar(pos, tds, toques)   # siempre DESPUES de predecir
        h_td.append(tds)
        h_to.append(toques)
    return salida


def brier(preds: list[PrediccionTD], base: bool = False) -> float:
    if not preds:
        return 0.0
    return sum(((x.p_base if base else x.p) - x.anoto) ** 2 for x in preds) / len(preds)


def log_loss(preds: list[PrediccionTD], base: bool = False) -> float:
    if not preds:
        return 0.0
    tot = 0.0
    for x in preds:
        p = min(max(x.p_base if base else x.p, 1e-9), 1 - 1e-9)
        tot += -math.log(p if x.anoto else 1.0 - p)
    return tot / len(preds)


def skill(preds: list[PrediccionTD]) -> float:
    """Mejora relativa del Brier sobre la tasa base. 0 = no aporta nada."""
    b = brier(preds, base=True)
    return 1.0 - brier(preds) / b if b > 0 else 0.0


def calibracion(preds: list[PrediccionTD], n_cubos: int = 10) -> list[tuple[float, float, int]]:
    cubos: list[list[PrediccionTD]] = [[] for _ in range(n_cubos)]
    for x in preds:
        cubos[min(n_cubos - 1, int(x.p * n_cubos))].append(x)
    return [
        (sum(x.p for x in c) / len(c), sum(x.anoto for x in c) / len(c), len(c))
        for c in cubos if c
    ]
