"""Conversion de odds y eliminacion del vig (margen de la casa).

Por que importa: usar 1/odds directamente como "probabilidad del mercado"
sobreestima cada lado. En un moneyline NBA tipico las dos patas suman ~1.045,
asi que comparar el modelo contra 1/odds regala ~2.2 puntos de "edge" falso por
lado. Con umbrales de EV del 3% eso es la diferencia entre un bot rentable y uno
que apuesta ruido.

Tres metodos, de menos a mas fiel:
  - multiplicative: reparte el margen proporcional a la prob. Simple, sesga a
    favor de los favoritos (subestima el favorite-longshot bias).
  - power:          resuelve p_i = q_i**k. Mejor comportamiento en lineas largas.
  - shin:           modelo de insider trading de Shin (1993). El estandar de
    facto para moneyline de 2-3 vias.
"""

from __future__ import annotations

from collections.abc import Sequence

_TOL = 1e-10
_MAX_ITER = 100


def american_to_decimal(american: int | float) -> float:
    """+150 -> 2.50, -200 -> 1.50."""
    if american == 0:
        raise ValueError("odds americanas no pueden ser 0")
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> float:
    if decimal <= 1.0:
        raise ValueError(f"odds decimales invalidas: {decimal}")
    if decimal >= 2.0:
        return (decimal - 1.0) * 100.0
    return -100.0 / (decimal - 1.0)


def implied_prob(decimal_odds: float) -> float:
    """Probabilidad implicita CRUDA (incluye vig). No usar para comparar vs modelo."""
    if decimal_odds <= 1.0:
        raise ValueError(f"odds decimales invalidas: {decimal_odds}")
    return 1.0 / decimal_odds


def overround(decimal_odds: Sequence[float]) -> float:
    """Suma de probabilidades implicitas. 1.045 = 4.5% de margen."""
    return sum(implied_prob(o) for o in decimal_odds)


def _multiplicative(raw: list[float]) -> list[float]:
    total = sum(raw)
    return [q / total for q in raw]


def _power(raw: list[float]) -> list[float]:
    """Busca k tal que sum(q_i ** k) == 1, por biseccion."""
    lo, hi = 0.05, 10.0
    for _ in range(_MAX_ITER):
        k = (lo + hi) / 2.0
        s = sum(q**k for q in raw)
        if abs(s - 1.0) < _TOL:
            break
        # sum(q**k) decrece al crecer k (q_i < 1)
        if s > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    fair = [q**k for q in raw]
    total = sum(fair)
    return [p / total for p in fair]


def _shin(raw: list[float]) -> list[float]:
    """Modelo de Shin: estima z (fraccion de dinero informado) por biseccion.

    p_i = (sqrt(z**2 + 4*(1-z)*q_i**2 / S) - z) / (2*(1-z)), con S = sum(q).
    """
    s = sum(raw)
    if s <= 1.0:  # sin vig (o incluso valor positivo agregado): nada que quitar
        return _multiplicative(raw)

    def probs_for(z: float) -> list[float]:
        if z >= 1.0 - _TOL:
            return _multiplicative(raw)
        return [
            ((z**2 + 4.0 * (1.0 - z) * q * q / s) ** 0.5 - z) / (2.0 * (1.0 - z))
            for q in raw
        ]

    lo, hi = 0.0, 0.99
    for _ in range(_MAX_ITER):
        z = (lo + hi) / 2.0
        total = sum(probs_for(z))
        if abs(total - 1.0) < _TOL:
            break
        if total > 1.0:
            lo = z
        else:
            hi = z
    z = (lo + hi) / 2.0
    fair = probs_for(z)
    total = sum(fair)
    return [p / total for p in fair]


_METHODS = {
    "multiplicative": _multiplicative,
    "power": _power,
    "shin": _shin,
}


def devig(decimal_odds: Sequence[float], method: str = "shin") -> list[float]:
    """Devuelve probabilidades justas (suman 1) a partir de las odds de un mercado.

    Hay que pasar TODAS las patas del mercado (2 en moneyline NBA/MLB, 3 en
    futbol 1X2). Pasar una sola pata no permite estimar el margen.
    """
    if len(decimal_odds) < 2:
        raise ValueError("devig necesita todas las patas del mercado (>=2)")
    if method not in _METHODS:
        raise ValueError(f"metodo desconocido: {method}. Usa {sorted(_METHODS)}")
    raw = [implied_prob(o) for o in decimal_odds]
    return _METHODS[method](raw)
