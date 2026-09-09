from datetime import UTC, datetime, timedelta

import pytest

from betbot.storage import SignalStore
from betbot.types import Market, Signal, Sport

NOW = datetime.now(UTC)


def make_signal(selection="Home", odds=2.0, created=None, event_id="e1"):
    return Signal(
        event_id=event_id, sport=Sport.NBA, commence_time=NOW + timedelta(hours=5),
        matchup="Away @ Home", market=Market.MONEYLINE, selection=selection,
        point=None, bookmaker="pinnacle", decimal_odds=odds, model_prob=0.60,
        fair_prob=0.50, ev=0.20, edge=0.10, kelly_stake=0.02, stake_units=20.0,
        model_name="test", created_at=created or NOW,
    )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_save_and_read_back(store):
    assert store.save(make_signal()) is True
    assert len(store.open_signals()) == 1


def test_duplicate_signal_is_rejected(store):
    s = make_signal()
    assert store.save(s) is True
    assert store.save(s) is False


def test_already_alerted_prevents_spam(store):
    store.save(make_signal())
    assert store.already_alerted("e1", "h2h", "Home")
    assert not store.already_alerted("e1", "h2h", "Away")


def test_settle_computes_pnl_for_winner_and_loser(store):
    store.save(make_signal("Home", 2.0))
    store.save(make_signal("Away", 2.5, created=NOW + timedelta(seconds=1)))
    assert store.settle("e1", "Home") == 2
    settled = {s["selection"]: s for s in store.settled_signals()}
    assert settled["Home"]["result"] == "win"
    assert settled["Home"]["pnl"] == pytest.approx(20.0)
    assert settled["Away"]["result"] == "loss"
    assert settled["Away"]["pnl"] == pytest.approx(-20.0)


def test_settled_signals_leave_open_list(store):
    store.save(make_signal())
    store.settle("e1", "Home")
    assert store.open_signals() == []


def test_record_closing_odds(store):
    store.save(make_signal())
    assert store.record_closing_odds("e1", "Home", 1.90) == 1
    assert store.settled_signals() == []  # aun sin liquidar
    store.settle("e1", "Home")
    assert store.settled_signals()[0]["closing_odds"] == pytest.approx(1.90)


def test_closing_odds_not_overwritten(store):
    store.save(make_signal())
    store.record_closing_odds("e1", "Home", 1.90)
    assert store.record_closing_odds("e1", "Home", 1.50) == 0


def test_save_many_counts_new_only(store):
    a = make_signal("Home")
    b = make_signal("Away", created=NOW + timedelta(seconds=1))
    assert store.save_many([a, b, a]) == 2


def test_past_events_are_not_open(store):
    store.save(make_signal(created=NOW))
    past = Signal(
        event_id="old", sport=Sport.NBA, commence_time=NOW - timedelta(hours=2),
        matchup="X @ Y", market=Market.MONEYLINE, selection="Y", point=None,
        bookmaker="pinnacle", decimal_odds=2.0, model_prob=0.6, fair_prob=0.5,
        ev=0.2, edge=0.1, kelly_stake=0.02, stake_units=20.0, model_name="test",
    )
    store.save(past)
    assert [s["event_id"] for s in store.open_signals()] == ["e1"]
