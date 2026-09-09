"""Metricas de validacion. Aqui se decide si el modelo sirve, antes de arriesgar.

Orden de importancia, y no es el intuitivo:

1. CALIBRACION (Brier, log-loss, tabla de calibracion). Si el modelo dice 60% y
   gana el 52% de las veces, todo el EV calculado es ficcion. Se mide sin apostar
   un peso y con pocos cientos de partidos.
2. CLV. Si tus precios no le ganan al cierre, tu ROI positivo fue suerte.
3. ROI. Ultimo, porque es el mas ruidoso: con un edge real del 3% y odds ~2.00,
   la desviacion tipica por apuesta es ~1.0 unidades. Para que el ROI sea
   distinguible de cero a 2 sigma hacen falta ~4400 apuestas. Un backtest de 300
   apuestas con +8% ROI no es evidencia de nada.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Error cuadratico medio de la probabilidad. Menor es mejor.

    Referencia: predecir siempre la tasa base da ~0.25 en un mercado 50/50.
    Un modelo NBA decente ronda 0.21-0.23.
    """
    if len(probs) != len(outcomes):
        raise ValueError("probs y outcomes deben tener el mismo largo")
    if not probs:
        raise ValueError("serie vacia")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-15) -> float:
    """Penaliza mucho mas la sobreconfianza que el Brier. Menor es mejor.

    Baseline: log(2) = 0.693 al predecir siempre 50%. Un Elo NBA razonable
    ronda 0.63-0.66.
    """
    if len(probs) != len(outcomes):
        raise ValueError("probs y outcomes deben tener el mismo largo")
    if not probs:
        raise ValueError("serie vacia")
    total = 0.0
    for p, o in zip(probs, outcomes, strict=True):
        p = min(1 - eps, max(eps, p))
        total += -(o * math.log(p) + (1 - o) * math.log(1 - p))
    return total / len(probs)


def calibration_table(
    probs: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> list[dict]:
    """Tabla predicho vs. observado por decil.

    Es la herramienta de diagnostico mas util que existe aqui: revela si el
    modelo esta sobreconfiado en los extremos (el fallo tipico de Elo sin
    encogimiento) mucho mejor que cualquier metrica agregada.
    """
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, o in zip(probs, outcomes, strict=True):
        idx = min(bins - 1, int(p * bins))
        buckets[idx].append((p, o))

    table = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        table.append(
            {
                "bin": f"{i / bins:.0%}-{(i + 1) / bins:.0%}",
                "n": len(b),
                "pred_avg": sum(p for p, _ in b) / len(b),
                "obs_rate": sum(o for _, o in b) / len(b),
                "gap": sum(p for p, _ in b) / len(b) - sum(o for _, o in b) / len(b),
            }
        )
    return table


def closing_line_value(bet_odds: float, closing_odds: float) -> float:
    """CLV en probabilidad implicita: cuanto mejor fue tu precio que el cierre.

    Positivo = conseguiste mejor precio que el mercado final. Es el mejor
    predictor temprano de rentabilidad a largo plazo que existe: un CLV medio
    positivo y estable implica que estas ganandole a la linea, aunque el ROI
    de la muestra todavia sea negativo por varianza.
    """
    if bet_odds <= 1 or closing_odds <= 1:
        raise ValueError("odds decimales invalidas")
    return (1.0 / closing_odds) - (1.0 / bet_odds)


@dataclass
class ROISummary:
    n_bets: int
    staked: float
    pnl: float
    roi: float
    win_rate: float
    avg_odds: float
    stderr: float
    """Error tipico del ROI. La regla practica: si |roi| < 2*stderr, el
    resultado es indistinguible de cero."""

    @property
    def significant(self) -> bool:
        return abs(self.roi) > 2 * self.stderr

    def __str__(self) -> str:
        verdict = "significativo" if self.significant else "NO significativo (ruido)"
        return (
            f"{self.n_bets} apuestas | staked {self.staked:.1f} u | PnL {self.pnl:+.1f} u\n"
            f"ROI {self.roi:+.2%} (+/- {self.stderr:.2%}) -> {verdict}\n"
            f"Aciertos {self.win_rate:.1%} | cuota media {self.avg_odds:.2f}"
        )


def roi_summary(bets: Sequence[dict]) -> ROISummary:
    """`bets`: dicts con stake_units, decimal_odds y result ('win'/'loss'/'push')."""
    settled = [b for b in bets if b.get("result") in ("win", "loss", "push")]
    if not settled:
        return ROISummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    staked = sum(b["stake_units"] for b in settled)
    returns = []
    wins = 0
    for b in settled:
        stake = b["stake_units"]
        if b["result"] == "win":
            r = stake * (b["decimal_odds"] - 1.0)
            wins += 1
        elif b["result"] == "push":
            r = 0.0
        else:
            r = -stake
        returns.append(r)

    pnl = sum(returns)
    roi = pnl / staked if staked else 0.0

    # Error tipico del ROI a partir de la dispersion de retornos por unidad apostada.
    n = len(settled)
    unit_returns = [
        r / b["stake_units"]
        for r, b in zip(returns, settled, strict=True)
        if b["stake_units"]
    ]
    if len(unit_returns) > 1:
        mean = sum(unit_returns) / len(unit_returns)
        var = sum((x - mean) ** 2 for x in unit_returns) / (len(unit_returns) - 1)
        stderr = math.sqrt(var / len(unit_returns))
    else:
        stderr = 0.0

    return ROISummary(
        n_bets=n,
        staked=staked,
        pnl=pnl,
        roi=roi,
        win_rate=wins / n,
        avg_odds=sum(b["decimal_odds"] for b in settled) / n,
        stderr=stderr,
    )


def sharpe_of_bets(bets: Sequence[dict]) -> float:
    """Retorno medio por unidad arriesgada / desviacion tipica.

    Concepto trasladado del trading: en apuestas los valores tipicos son mucho
    menores (0.05-0.15 por apuesta) porque cada apuesta es casi todo varianza.
    """
    settled = [b for b in bets if b.get("result") in ("win", "loss")]
    if len(settled) < 2:
        return 0.0
    rs = [
        (b["decimal_odds"] - 1.0) if b["result"] == "win" else -1.0
        for b in settled
    ]
    mean = sum(rs) / len(rs)
    var = sum((x - mean) ** 2 for x in rs) / (len(rs) - 1)
    sd = math.sqrt(var)
    return mean / sd if sd else 0.0
