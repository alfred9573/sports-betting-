"""Metricas para mercados de mas de dos resultados (1X2 en futbol).

Las metricas binarias no sirven aqui: un partido de futbol tiene tres salidas y
el empate no es "medio local". Ademas hay una propiedad que el log-loss ignora y
que en futbol importa mucho: los resultados estan ORDENADOS (victoria local,
empate, victoria visitante). Equivocarse prediciendo empate cuando gano el local
es un error menor que predecir victoria visitante.

Por eso el estandar en prediccion de futbol es el RPS (Ranked Probability
Score), que penaliza segun la distancia en ese orden. Se incluyen tambien
log-loss y Brier multiclase porque cada una dice algo distinto:

  - RPS       ordena bien y es lo comparable con la literatura de futbol
  - log-loss  castiga la sobreconfianza mas que ninguna
  - Brier     robusto, interpretable como error cuadratico medio
"""

from __future__ import annotations

import math
from collections.abc import Sequence

# Orden canonico del mercado 1X2. El ORDEN IMPORTA para el RPS: el empate va en
# medio porque es el resultado intermedio entre las dos victorias.
OUTCOMES_1X2 = ("home", "draw", "away")


def _validate(probs: Sequence[Sequence[float]], outcomes: Sequence[int]) -> int:
    if len(probs) != len(outcomes):
        raise ValueError("probs y outcomes deben tener el mismo largo")
    if not probs:
        raise ValueError("serie vacia")
    n_classes = len(probs[0])
    for i, p in enumerate(probs):
        if len(p) != n_classes:
            raise ValueError(f"fila {i}: se esperaban {n_classes} clases, hay {len(p)}")
        total = sum(p)
        if not 0.98 <= total <= 1.02:
            raise ValueError(f"fila {i}: las probabilidades suman {total:.4f}, no 1")
    for i, o in enumerate(outcomes):
        if not 0 <= o < n_classes:
            raise ValueError(f"fila {i}: clase {o} fuera de rango [0,{n_classes})")
    return n_classes


def ranked_probability_score(
    probs: Sequence[Sequence[float]], outcomes: Sequence[int]
) -> float:
    """RPS medio. Menor es mejor. El estandar para evaluar pronosticos 1X2.

    Penaliza segun la distancia en el orden de resultados: predecir empate
    cuando gano el local duele menos que predecir victoria visitante.

    Referencias utiles: predecir la tasa base de la liga da ~0.22-0.23; un
    modelo Poisson decente ronda 0.19-0.21. Por debajo de 0.19 sostenido en
    muestra grande seria muy bueno.
    """
    _validate(probs, outcomes)
    n_classes = len(probs[0])
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        cum_p = 0.0
        cum_e = 0.0
        acc = 0.0
        for i in range(n_classes - 1):
            cum_p += p[i]
            cum_e += 1.0 if i == o else 0.0
            acc += (cum_p - cum_e) ** 2
        total += acc / (n_classes - 1)
    return total / len(probs)


def multiclass_log_loss(
    probs: Sequence[Sequence[float]], outcomes: Sequence[int], eps: float = 1e-15
) -> float:
    """Log-loss multiclase. Baseline de referencia: ln(3) = 1.0986 con tres
    resultados equiprobables."""
    _validate(probs, outcomes)
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        total += -math.log(min(1 - eps, max(eps, p[o])))
    return total / len(probs)


def multiclass_brier(
    probs: Sequence[Sequence[float]], outcomes: Sequence[int]
) -> float:
    """Brier multiclase (suma sobre clases del error cuadratico)."""
    n_classes = _validate(probs, outcomes)
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        total += sum((p[i] - (1.0 if i == o else 0.0)) ** 2 for i in range(n_classes))
    return total / len(probs)


def multiclass_accuracy(
    probs: Sequence[Sequence[float]], outcomes: Sequence[int]
) -> float:
    _validate(probs, outcomes)
    hits = sum(
        1
        for p, o in zip(probs, outcomes, strict=True)
        if max(range(len(p)), key=lambda i: p[i]) == o
    )
    return hits / len(probs)


def per_class_calibration(
    probs: Sequence[Sequence[float]],
    outcomes: Sequence[int],
    labels: Sequence[str] = OUTCOMES_1X2,
) -> list[dict]:
    """Predicho vs observado POR CLASE.

    En futbol es el diagnostico decisivo: el fallo tipico del Poisson es
    subestimar el empate de forma sistematica. Un agregado como el RPS no lo
    revela, pero esta tabla lo pone en una fila.
    """
    n_classes = _validate(probs, outcomes)
    rows = []
    for i in range(n_classes):
        pred = sum(p[i] for p in probs) / len(probs)
        obs = sum(1 for o in outcomes if o == i) / len(outcomes)
        rows.append(
            {
                "class": labels[i] if i < len(labels) else str(i),
                "pred_avg": pred,
                "obs_rate": obs,
                "gap": pred - obs,
                "n": len(probs),
            }
        )
    return rows


def base_rate_probs(outcomes: Sequence[int], n_classes: int = 3) -> list[list[float]]:
    """Baseline honesto: predecir siempre la frecuencia base de cada resultado.

    Es el rival a batir. Un modelo de futbol que no le gane a "el local gana el
    46%, empata el 26%, pierde el 28%" no aporta absolutamente nada.
    """
    if not outcomes:
        raise ValueError("serie vacia")
    counts = [0] * n_classes
    for o in outcomes:
        counts[o] += 1
    rates = [c / len(outcomes) for c in counts]
    return [rates[:] for _ in outcomes]
