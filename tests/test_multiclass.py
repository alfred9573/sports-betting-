"""Tests de metricas multiclase (1X2)."""

import math

import pytest

from betbot.backtest.multiclass import (
    base_rate_probs,
    multiclass_accuracy,
    multiclass_brier,
    multiclass_log_loss,
    per_class_calibration,
    ranked_probability_score,
)

HOME, DRAW, AWAY = 0, 1, 2


def test_rps_perfect_prediction_is_zero():
    assert ranked_probability_score([[1.0, 0.0, 0.0]], [HOME]) == pytest.approx(0.0)


def test_rps_respects_outcome_ordering():
    """LA propiedad que justifica usar RPS: predecir empate cuando gano el local
    es un error MENOR que predecir victoria visitante. El log-loss trata ambos
    igual; el RPS no."""
    cerca = ranked_probability_score([[0.0, 1.0, 0.0]], [HOME])
    lejos = ranked_probability_score([[0.0, 0.0, 1.0]], [HOME])
    assert cerca < lejos
    assert cerca == pytest.approx(0.5)
    assert lejos == pytest.approx(1.0)


def test_rps_rewards_hedged_prediction_over_confident_error():
    seguro_mal = ranked_probability_score([[0.0, 0.0, 1.0]], [HOME])
    repartido = ranked_probability_score([[1 / 3, 1 / 3, 1 / 3]], [HOME])
    assert repartido < seguro_mal


def test_multiclass_log_loss_uniform_is_ln3():
    assert multiclass_log_loss([[1 / 3] * 3], [HOME]) == pytest.approx(math.log(3), abs=1e-9)


def test_multiclass_log_loss_does_not_explode_on_certainty():
    assert math.isfinite(multiclass_log_loss([[1.0, 0.0, 0.0]], [AWAY]))


def test_multiclass_brier_perfect_is_zero():
    assert multiclass_brier([[0.0, 0.0, 1.0]], [AWAY]) == pytest.approx(0.0)


def test_accuracy_picks_argmax():
    probs = [[0.5, 0.3, 0.2], [0.1, 0.2, 0.7]]
    assert multiclass_accuracy(probs, [HOME, AWAY]) == 1.0
    assert multiclass_accuracy(probs, [AWAY, HOME]) == 0.0


def test_rejects_probabilities_that_do_not_sum_to_one():
    with pytest.raises(ValueError, match="suman"):
        ranked_probability_score([[0.5, 0.5, 0.5]], [HOME])


def test_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="mismo largo"):
        ranked_probability_score([[1.0, 0.0, 0.0]], [HOME, DRAW])


def test_rejects_out_of_range_class():
    with pytest.raises(ValueError, match="fuera de rango"):
        ranked_probability_score([[1.0, 0.0, 0.0]], [9])


def test_rejects_inconsistent_class_count():
    with pytest.raises(ValueError, match="clases"):
        ranked_probability_score([[0.5, 0.5], [0.3, 0.3, 0.4]], [0, 1])


def test_rejects_empty():
    with pytest.raises(ValueError, match="vacia"):
        ranked_probability_score([], [])


def test_base_rate_probs_reproduce_frequencies():
    outcomes = [HOME] * 46 + [DRAW] * 26 + [AWAY] * 28
    base = base_rate_probs(outcomes)
    assert base[0] == pytest.approx([0.46, 0.26, 0.28])


def test_base_rate_is_the_bar_to_beat():
    """Un modelo que solo repite la tasa base no debe parecer bueno."""
    outcomes = [HOME] * 46 + [DRAW] * 26 + [AWAY] * 28
    base = base_rate_probs(outcomes)
    assert ranked_probability_score(base, outcomes) > 0.20


def test_per_class_calibration_detects_draw_underprediction():
    """El fallo clasico del Poisson: subestimar el empate de forma sistematica.
    Un agregado no lo revela; esta tabla lo pone en una fila."""
    probs = [[0.50, 0.15, 0.35]] * 100
    outcomes = [HOME] * 46 + [DRAW] * 26 + [AWAY] * 28
    rows = {r["class"]: r for r in per_class_calibration(probs, outcomes)}
    assert rows["draw"]["gap"] < -0.10
    assert rows["draw"]["pred_avg"] == pytest.approx(0.15)
    assert rows["draw"]["obs_rate"] == pytest.approx(0.26)
