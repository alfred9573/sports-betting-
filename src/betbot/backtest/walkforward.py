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
from betbot.backtest.multiclass import (
    OUTCOMES_1X2,
    base_rate_probs,
    multiclass_accuracy,
    multiclass_brier,
    multiclass_log_loss,
    per_class_calibration,
    ranked_probability_score,
)

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


@dataclass
class MulticlassResult:
    """Resultado de un walk-forward sobre un mercado de 3 vias (1X2)."""

    n_predictions: int
    n_skipped: int
    rps: float
    baseline_rps: float
    logloss: float
    baseline_logloss: float
    brier: float
    accuracy: float
    calibration: list[dict] = field(default_factory=list)
    probs: list[list[float]] = field(default_factory=list, repr=False)
    outcomes: list[int] = field(default_factory=list, repr=False)

    @property
    def beats_baseline(self) -> bool:
        """El RPS manda: es la metrica estandar para 1X2 y respeta el orden."""
        return self.rps < self.baseline_rps

    def __str__(self) -> str:
        verdict = "SI" if self.beats_baseline else "NO"
        lines = [
            f"Walk-forward 1X2: {self.n_predictions} predicciones "
            f"({self.n_skipped} omitidas por falta de historial)",
            f"  RPS      {self.rps:.4f}   (baseline frecuencia base: {self.baseline_rps:.4f})",
            f"  Log-loss {self.logloss:.4f}   (baseline: {self.baseline_logloss:.4f})",
            f"  Brier    {self.brier:.4f}   | acierto {self.accuracy:.2%}",
            f"  Le gana al baseline: {verdict}",
        ]
        if self.calibration:
            lines.append("\n  Calibracion por clase:")
            lines.append("  clase      predicho  observado    gap")
            for r in self.calibration:
                lines.append(
                    f"  {r['class']:<8} {r['pred_avg']:>8.1%}  {r['obs_rate']:>8.1%}  "
                    f"{r['gap']:>+6.1%}"
                )
        return "\n".join(lines)


def walk_forward_multiclass(
    games: list[dict],
    make_model: Callable[[], Any],
    predict_probs: Callable[[Any, dict], list[float] | None],
    update: Callable[[Any, dict], None],
    labels: tuple[str, ...] = OUTCOMES_1X2,
) -> MulticlassResult:
    """Walk-forward para mercados de 3 vias. Misma regla: predecir antes de ver.

    `predict_probs` devuelve [p_local, p_empate, p_visitante] o None.
    """
    model = make_model()
    probs: list[list[float]] = []
    outcomes: list[int] = []
    skipped = 0

    for g in games:
        p = predict_probs(model, g)
        if p is None:
            skipped += 1
        else:
            total = sum(p)
            if not 0.98 <= total <= 1.02:
                raise ValueError(f"probabilidades del modelo suman {total:.4f}, no 1")
            hs, as_ = g["home_score"], g["away_score"]
            outcomes.append(0 if hs > as_ else (1 if hs == as_ else 2))
            probs.append([x / total for x in p])
        update(model, g)

    if not probs:
        raise ValueError(
            "ninguna prediccion generada: el modelo nunca acumulo historial "
            "suficiente. Revisa min_matches o amplia el rango de datos."
        )

    baseline = base_rate_probs(outcomes, n_classes=len(labels))
    return MulticlassResult(
        n_predictions=len(probs),
        n_skipped=skipped,
        rps=ranked_probability_score(probs, outcomes),
        baseline_rps=ranked_probability_score(baseline, outcomes),
        logloss=multiclass_log_loss(probs, outcomes),
        baseline_logloss=multiclass_log_loss(baseline, outcomes),
        brier=multiclass_brier(probs, outcomes),
        accuracy=multiclass_accuracy(probs, outcomes),
        calibration=per_class_calibration(probs, outcomes, labels),
        probs=probs,
        outcomes=outcomes,
    )


def walk_forward_soccer(
    games: list[dict],
    model_factory: Callable[[], Any] | None = None,
    refit_every: int = 10,
    window: int = 760,
) -> MulticlassResult:
    """Walk-forward para el modelo Poisson de futbol.

    A diferencia del Elo, que se actualiza en O(1) por partido, el Poisson estima
    fuerzas por medias ponderadas sobre toda la historia. Reajustarlo en cada
    partido seria O(n^2) — inviable con 10.000 partidos en Python puro.

    Se reajusta cada `refit_every` partidos usando una ventana de los ultimos
    `window` (≈2 temporadas de una liga de 20 equipos). Eso NO es un atajo: es
    como se opera de verdad, reentrenando periodicamente sobre historia reciente.
    Lo que no se negocia es que la ventana solo contenga partidos ANTERIORES al
    que se predice.
    """
    if model_factory is None:
        from betbot.models.soccer import PoissonSoccerModel

        model_factory = PoissonSoccerModel

    history: list[dict] = []
    model = model_factory()
    fitted = False
    since_refit = 0

    def predict(_m, g):
        nonlocal model, fitted, since_refit
        if len(history) >= window // 4 and (not fitted or since_refit >= refit_every):
            model = model_factory()
            model.fit(_with_recency(history[-window:]))
            fitted = True
            since_refit = 0
        if not fitted:
            return None
        lams = model.expected_goals(g["home"], g["away"])
        if lams is None:
            return None
        matrix = model.score_matrix(*lams)
        n = len(matrix)
        p_home = sum(matrix[x][y] for x in range(n) for y in range(x))
        p_draw = sum(matrix[i][i] for i in range(n))
        return [p_home, p_draw, max(0.0, 1.0 - p_home - p_draw)]

    def update(_m, g):
        nonlocal since_refit
        history.append(g)
        since_refit += 1

    return walk_forward_multiclass(games, lambda: None, predict, update)


def _with_recency(matches: list[dict]) -> list[dict]:
    """Anade `days_ago` relativo al ultimo partido, para el decaimiento temporal."""
    if not matches:
        return matches
    from datetime import date

    def parse(d):
        return date.fromisoformat(d) if isinstance(d, str) else d

    last = parse(matches[-1]["date"])
    out = []
    for m in matches:
        out.append({**m, "days_ago": (last - parse(m["date"])).days})
    return out
