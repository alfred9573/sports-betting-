import math

import pytest

from betbot.backtest.metrics import (
    brier_score,
    calibration_table,
    closing_line_value,
    log_loss,
    roi_summary,
    sharpe_of_bets,
)


def test_brier_perfect_prediction_is_zero():
    assert brier_score([1.0, 0.0, 1.0], [1, 0, 1]) == pytest.approx(0.0)


def test_brier_coinflip_baseline():
    assert brier_score([0.5] * 100, [1, 0] * 50) == pytest.approx(0.25)


def test_brier_worst_case_is_one():
    assert brier_score([1.0, 0.0], [0, 1]) == pytest.approx(1.0)


def test_log_loss_coinflip_is_ln2():
    assert log_loss([0.5] * 100, [1, 0] * 50) == pytest.approx(math.log(2), abs=1e-9)


def test_log_loss_punishes_confident_error():
    confident_wrong = log_loss([0.99], [0])
    mild_wrong = log_loss([0.60], [0])
    assert confident_wrong > mild_wrong * 4


def test_log_loss_does_not_explode_on_certainty():
    assert math.isfinite(log_loss([1.0], [0]))


def test_metrics_reject_mismatched_lengths():
    with pytest.raises(ValueError):
        brier_score([0.5], [1, 0])
    with pytest.raises(ValueError):
        log_loss([0.5], [1, 0])


def test_metrics_reject_empty():
    with pytest.raises(ValueError):
        brier_score([], [])


def test_calibration_table_detects_overconfidence():
    # el modelo dice 90% pero solo acierta el 50%: sobreconfianza clara
    probs = [0.9] * 100
    outcomes = [1, 0] * 50
    row = [r for r in calibration_table(probs, outcomes) if r["n"] == 100][0]
    assert row["pred_avg"] == pytest.approx(0.9)
    assert row["obs_rate"] == pytest.approx(0.5)
    assert row["gap"] > 0.35


def test_calibration_table_skips_empty_bins():
    table = calibration_table([0.55] * 10, [1] * 10, bins=10)
    assert len(table) == 1


def test_clv_positive_when_you_beat_the_close():
    assert closing_line_value(2.10, 2.00) > 0


def test_clv_negative_when_line_moves_against_you():
    assert closing_line_value(1.90, 2.00) < 0


def test_clv_zero_when_line_unchanged():
    assert closing_line_value(2.00, 2.00) == pytest.approx(0.0)


def test_clv_rejects_invalid_odds():
    with pytest.raises(ValueError):
        closing_line_value(1.0, 2.0)


def test_roi_summary_empty():
    s = roi_summary([])
    assert s.n_bets == 0 and s.roi == 0.0


def test_roi_summary_all_wins():
    bets = [{"stake_units": 10, "decimal_odds": 2.0, "result": "win"}] * 10
    s = roi_summary(bets)
    assert s.n_bets == 10
    assert s.pnl == pytest.approx(100.0)
    assert s.roi == pytest.approx(1.0)
    assert s.win_rate == 1.0


def test_roi_summary_breakeven_is_not_significant():
    """200 apuestas a cuota 2.00 con 50% de acierto: ROI 0 y, sobre todo, un
    error tipico de ~7%. Es el numero que justifica no celebrar un backtest corto."""
    bets = [
        {"stake_units": 10, "decimal_odds": 2.0, "result": "win" if i % 2 == 0 else "loss"}
        for i in range(200)
    ]
    s = roi_summary(bets)
    assert s.roi == pytest.approx(0.0, abs=1e-9)
    assert s.stderr > 0.05
    assert not s.significant


def test_roi_summary_ignores_unsettled():
    bets = [
        {"stake_units": 10, "decimal_odds": 2.0, "result": "win"},
        {"stake_units": 10, "decimal_odds": 2.0, "result": None},
    ]
    assert roi_summary(bets).n_bets == 1


def test_roi_summary_push_is_neutral():
    bets = [{"stake_units": 10, "decimal_odds": 1.91, "result": "push"}] * 5
    s = roi_summary(bets)
    assert s.pnl == pytest.approx(0.0)


def test_sharpe_needs_two_bets():
    assert sharpe_of_bets([{"stake_units": 1, "decimal_odds": 2.0, "result": "win"}]) == 0.0


def test_sharpe_positive_for_winning_record():
    bets = [
        {"stake_units": 10, "decimal_odds": 2.0, "result": "win" if i % 3 else "loss"}
        for i in range(60)
    ]
    assert sharpe_of_bets(bets) > 0
