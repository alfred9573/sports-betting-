from datetime import UTC, datetime, timedelta

import pytest

from betbot.ev.engine import EVConfig, EVEngine, expected_value, kelly_fraction
from betbot.types import BookMarket, Event, Market, ModelProbabilities, Outcome, Sport

NOW = datetime.now(UTC)


def make_event(prices: dict[str, float], books=("pinnacle", "draftkings", "fanduel")):
    return Event(
        event_id="e1",
        sport=Sport.NBA,
        commence_time=NOW + timedelta(hours=3),
        home_team="Home",
        away_team="Away",
        books=tuple(
            BookMarket(
                b, Market.MONEYLINE,
                tuple(Outcome(n, o) for n, o in prices.items()),
                NOW,
            )
            for b in books
        ),
    )


def test_expected_value_fair_coin_is_zero():
    assert expected_value(0.5, 2.0) == pytest.approx(0.0)


def test_expected_value_sign():
    assert expected_value(0.55, 2.0) > 0
    assert expected_value(0.45, 2.0) < 0


def test_kelly_zero_without_edge():
    assert kelly_fraction(0.50, 1.90) == 0.0
    assert kelly_fraction(0.40, 2.0) == 0.0


def test_kelly_known_value():
    # p=0.6, odds=2.0 (b=1): f = (0.6*1 - 0.4)/1 = 0.20
    assert kelly_fraction(0.6, 2.0) == pytest.approx(0.20)
    assert kelly_fraction(0.6, 2.0, 0.25) == pytest.approx(0.05)


def test_engine_emits_signal_on_real_edge():
    event = make_event({"Home": 2.00, "Away": 2.00})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.60, "Away": 0.40}, "test")
    signals = EVEngine().evaluate(event, model)
    assert len(signals) == 1
    s = signals[0]
    assert s.selection == "Home"
    assert s.ev == pytest.approx(0.20)
    assert s.fair_prob == pytest.approx(0.50, abs=1e-6)


def test_engine_silent_when_model_agrees_with_market():
    event = make_event({"Home": 1.91, "Away": 1.91})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.50, "Away": 0.50}, "test")
    assert EVEngine().evaluate(event, model) == []


def test_vig_alone_never_creates_a_signal():
    """La prueba que mas importa: con -110/-110 el mercado justo es 50/50. Un
    modelo que dice 50/50 no debe generar senal. Si comparasemos contra 1/odds
    (52.4%) tampoco, pero al reves — un modelo al 53% generaria senal falsa."""
    event = make_event({"Home": 1.909, "Away": 1.909})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.515, "Away": 0.485}, "test")
    assert EVEngine().evaluate(event, model) == []


def test_engine_picks_best_available_price():
    event = Event(
        event_id="e1", sport=Sport.NBA, commence_time=NOW + timedelta(hours=3),
        home_team="Home", away_team="Away",
        books=(
            BookMarket("pinnacle", Market.MONEYLINE,
                       (Outcome("Home", 2.00), Outcome("Away", 2.00)), NOW),
            BookMarket("draftkings", Market.MONEYLINE,
                       (Outcome("Home", 2.15), Outcome("Away", 1.85)), NOW),
            BookMarket("fanduel", Market.MONEYLINE,
                       (Outcome("Home", 1.95), Outcome("Away", 2.05)), NOW),
        ),
    )
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.60, "Away": 0.40}, "test")
    s = EVEngine().evaluate(event, model)[0]
    assert s.bookmaker == "draftkings"
    assert s.decimal_odds == 2.15


def test_stake_capped_by_max_stake_pct():
    event = make_event({"Home": 3.00, "Away": 1.50})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.60, "Away": 0.40}, "test")
    cfg = EVConfig(max_stake_pct=0.02, bankroll=1000, kelly_fraction=1.0)
    s = EVEngine(cfg).evaluate(event, model)[0]
    assert s.kelly_stake == pytest.approx(0.02)
    assert s.stake_units == pytest.approx(20.0)


def test_longshot_filter_blocks_extreme_odds():
    event = make_event({"Home": 15.0, "Away": 1.05})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.20, "Away": 0.80}, "test")
    assert EVEngine(EVConfig(max_odds=10.0)).evaluate(event, model) == []


def test_thin_market_without_sharp_book_is_skipped():
    event = make_event({"Home": 2.00, "Away": 2.00}, books=("bovada",))
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.60, "Away": 0.40}, "test")
    assert EVEngine(EVConfig(min_books=3)).evaluate(event, model) == []


def test_sharp_book_alone_is_enough():
    event = make_event({"Home": 2.00, "Away": 2.00}, books=("pinnacle",))
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.60, "Away": 0.40}, "test")
    assert len(EVEngine(EVConfig(min_books=3)).evaluate(event, model)) == 1


def test_mismatched_event_id_raises():
    event = make_event({"Home": 2.00, "Away": 2.00})
    model = ModelProbabilities("otro", Market.MONEYLINE, {"Home": 0.6, "Away": 0.4}, "t")
    with pytest.raises(ValueError):
        EVEngine().evaluate(event, model)


def test_model_probs_must_sum_to_one():
    with pytest.raises(ValueError):
        ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.6, "Away": 0.6}, "t")


def test_signals_sorted_by_ev_desc():
    event = make_event({"Home": 2.50, "Away": 2.50})
    model = ModelProbabilities("e1", Market.MONEYLINE, {"Home": 0.55, "Away": 0.45}, "test")
    sigs = EVEngine(EVConfig(min_books=1)).evaluate(event, model)
    assert [s.ev for s in sigs] == sorted([s.ev for s in sigs], reverse=True)
