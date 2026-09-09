"""Backtest walk-forward: la unica forma honesta de validar estos modelos.

LA REGLA: para predecir el partido N, el modelo solo puede haber visto los
partidos 1..N-1. Ni uno mas.

Suena obvio y es el error mas comun del backtesting deportivo. Entrenar un Elo
con la temporada completa y luego "predecir" partidos de esa misma temporada
produce metricas espectaculares e imposibles de reproducir en vivo: el rating de
cada equipo ya incorpora el resultado que estas prediciendo. Se ve un log-loss
de 0.60 donde la realidad es 0.66, y esa diferencia es exactamente la que separa
un modelo con ventaja de uno sin ella.

Aqui el modelo se actualiza partido a partido, en orden cronologico, prediciendo
siempre antes de ver el resultado. Es lento y es correcto.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from betbot.backtest.metrics import brier_score, calibration_table, log_loss

log = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    n_predictions: int
    n_skipped: int
    brier: float
    logloss: float
    accuracy: float
    baseline_brier: float
    """Brier de predecir siempre la tasa base de victoria local. Si el modelo no
    le gana a ESTO, no aporta nada: la ventaja de local ya la conoce el mercado."""
    baseline_logloss: float
    home_win_rate: float
    calibration: list[dict] = field(default_factory=list)
    probs: list[float] = field(default_factory=list, repr=False)
    outcomes: list[int] = field(default_factory=list, repr=False)

    @property
    def beats_baseline(self) -> bool:
        return self.logloss < self.baseline_logloss

    def __str__(self) -> str:
        verdict = "SI" if self.beats_baseline else "NO"
        lines = [
            f"Walk-forward: {self.n_predictions} predicciones "
            f"({self.n_skipped} omitidas por falta de historial)",
            f"  Brier    {self.brier:.4f}   (baseline tasa base: {self.baseline_brier:.4f})",
            f"  Log-loss {self.logloss:.4f}   (baseline tasa base: {self.baseline_logloss:.4f})",
            f"  Acierto  {self.accuracy:.2%}   | victorias local: {self.home_win_rate:.2%}",
            f"  Le gana al baseline: {verdict}",
        ]
        if self.calibration:
            lines.append("\n  Calibracion (predicho vs observado):")
            lines.append("  bin           n   predicho  observado    gap")
            for r in self.calibration:
                lines.append(
                    f"  {r['bin']:<10} {r['n']:>5}   {r['pred_avg']:>7.1%}   "
                    f"{r['obs_rate']:>7.1%}  {r['gap']:>+6.1%}"
                )
        return "\n".join(lines)


def walk_forward(
    games: list[dict],
    make_model: Callable[[], Any],
    predict_home_prob: Callable[[Any, dict], float | None],
    update: Callable[[Any, dict], None],
    min_history: int = 0,
) -> WalkForwardResult:
    """Corre la validacion walk-forward sobre partidos en orden cronologico.

    `games` DEBE venir ordenado por fecha ascendente (GameStore.training_rows lo
    garantiza). Si no lo esta, el resultado es basura silenciosa.

    - `make_model()`      crea el modelo vacio
    - `predict_home_prob` prob. de victoria local, o None si no hay datos
    - `update`            incorpora el resultado al modelo TRAS predecir
    """
    model = make_model()
    probs: list[float] = []
    outcomes: list[int] = []
    skipped = 0

    for i, g in enumerate(games):
        # 1) PREDECIR con el estado actual (solo partidos 0..i-1)
        if i >= min_history:
            p = predict_home_prob(model, g)
            if p is None:
                skipped += 1
            else:
                if not 0.0 < p < 1.0:
                    raise ValueError(f"probabilidad invalida del modelo: {p}")
                probs.append(p)
                outcomes.append(1 if g["home_score"] > g["away_score"] else 0)
        else:
            skipped += 1

        # 2) SOLO AHORA incorporar el resultado
        update(model, g)

    if not probs:
        raise ValueError(
            "ninguna prediccion generada: el modelo nunca acumulo historial "
            "suficiente. Revisa min_games del modelo o amplia el rango de datos."
        )

    # Baseline honesto: la tasa base de victoria local del propio periodo.
    base_rate = sum(outcomes) / len(outcomes)
    baseline = [base_rate] * len(outcomes)

    correct = sum(1 for p, o in zip(probs, outcomes, strict=True) if (p > 0.5) == bool(o))

    return WalkForwardResult(
        n_predictions=len(probs),
        n_skipped=skipped,
        brier=brier_score(probs, outcomes),
        logloss=log_loss(probs, outcomes),
        accuracy=correct / len(probs),
        baseline_brier=brier_score(baseline, outcomes),
        baseline_logloss=log_loss(baseline, outcomes),
        home_win_rate=base_rate,
        calibration=calibration_table(probs, outcomes),
        probs=probs,
        outcomes=outcomes,
    )


def walk_forward_elo(games: list[dict], model_factory: Callable[[], Any]) -> WalkForwardResult:
    """Atajo para los modelos basados en Elo (NBA/MLB), que comparten interfaz."""

    def predict(model, g):
        home, away = g["home"], g["away"]
        if not (model.ratings.is_reliable(home) and model.ratings.is_reliable(away)):
            return None
        p = model.ratings.win_prob(home, away, neutral=g.get("neutral", False))
        return 0.5 + (p - 0.5) * getattr(model, "shrink", 1.0)

    def update(model, g):
        if g.get("new_season"):
            model.ratings.new_season()
        model.ratings.update(
            g["home"], g["away"], g["home_score"], g["away_score"],
            neutral=g.get("neutral", False),
        )

    return walk_forward(games, model_factory, predict, update)
