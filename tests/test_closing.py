"""Tests del ciclo de captura de cierre y CLV. Proveedor simulado, sin red."""

from datetime import UTC, datetime, timedelta

import pytest

from betbot.backtest.metrics import clv_summary
from betbot.closing import ClosingCapture
from betbot.storage import SignalStore
from betbot.types import BookMarket, Event, Market, Outcome, Signal, Sport

NOW = datetime.now(UTC)


def signal(event_id="e1", selection="Home", odds=2.10, book="pinnacle", starts_in_min=15):
    return Signal(
        event_id=event_id, sport=Sport.NBA,
        commence_time=NOW + timedelta(minutes=starts_in_min),
        matchup="Away @ Home", market=Market.MONEYLINE, selection=selection,
        point=None, bookmaker=book, decimal_odds=odds, model_prob=0.60,
        fair_prob=0.50, ev=0.26, edge=0.10, kelly_stake=0.02, stake_units=20.0,
        model_name="test", created_at=NOW - timedelta(hours=3),
    )


def event(event_id="e1", home_odds=2.00, away_odds=1.95, book="pinnacle"):
    return Event(
        event_id=event_id, sport=Sport.NBA, commence_time=NOW + timedelta(minutes=15),
        home_team="Home", away_team="Away",
        books=(
            BookMarket(book, Market.MONEYLINE,
                       (Outcome("Home", home_odds), Outcome("Away", away_odds)), NOW),
        ),
    )


class FakeProvider:
    def __init__(self, events, fail=False):
        self.events = events
        self.fail = fail
        self.calls = 0

    def fetch_events(self, sport, markets):
        self.calls += 1
        if self.fail:
            raise RuntimeError("API caida")
        return self.events


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "s.db")


# ---------- cola de trabajo ----------

def test_pending_close_finds_imminent_signals(store):
    store.save(signal(starts_in_min=15))
    assert len(store.pending_close(30)) == 1


def test_pending_close_ignores_distant_signals(store):
    store.save(signal(starts_in_min=300))
    assert store.pending_close(30) == []


def test_pending_close_ignores_already_captured(store):
    store.save(signal())
    store.record_closing_odds("e1", "Home", 2.00)
    assert store.pending_close(30) == []


def test_missed_close_flags_started_games(store):
    store.save(signal(starts_in_min=-60))
    assert len(store.missed_close()) == 1


def test_missed_close_excludes_captured(store):
    store.save(signal(starts_in_min=-60))
    store.record_closing_odds("e1", "Home", 2.00)
    assert store.missed_close() == []


# ---------- captura ----------

def test_capture_records_closing_price(store):
    store.save(signal(odds=2.10))
    report = ClosingCapture(store, FakeProvider([event(home_odds=2.00)])).run()
    assert report.captured == 1
    row = store.open_signals()[0]
    assert row["closing_odds"] == pytest.approx(2.00)
    assert row["closed_at"] is not None


def test_capture_records_devigged_fair_prob(store):
    """Guardar tambien el consenso sin vig al cierre: un libro blando puede
    dejar la linea quieta y hacerte creer que acertaste."""
    store.save(signal())
    ClosingCapture(store, FakeProvider([event()])).run()
    row = store.open_signals()[0]
    assert row["closing_fair_prob"] is not None
    assert 0 < row["closing_fair_prob"] < 1


def test_capture_prefers_same_bookmaker(store):
    store.save(signal(book="draftkings", odds=2.20))
    ev = Event(
        event_id="e1", sport=Sport.NBA, commence_time=NOW + timedelta(minutes=10),
        home_team="Home", away_team="Away",
        books=(
            BookMarket("pinnacle", Market.MONEYLINE,
                       (Outcome("Home", 2.05), Outcome("Away", 1.90)), NOW),
            BookMarket("draftkings", Market.MONEYLINE,
                       (Outcome("Home", 1.95), Outcome("Away", 2.00)), NOW),
        ),
    )
    ClosingCapture(store, FakeProvider([ev])).run()
    # debe tomar 1.95 (draftkings), no 2.05 (el mejor precio del mercado)
    assert store.open_signals()[0]["closing_odds"] == pytest.approx(1.95)


def test_capture_falls_back_when_book_pulled_market(store):
    """Si el libro original retiro el mercado, usar el mejor disponible en vez
    de perder la observacion entera."""
    store.save(signal(book="libro_que_desaparecio"))
    report = ClosingCapture(store, FakeProvider([event(book="pinnacle")])).run()
    assert report.captured == 1


def test_capture_does_not_overwrite_existing_close(store):
    """El primer cierre capturado es el bueno: correr el job dos veces no debe
    degradar el dato con un precio posterior."""
    store.save(signal())
    ClosingCapture(store, FakeProvider([event(home_odds=2.00)])).run()
    ClosingCapture(store, FakeProvider([event(home_odds=1.50)])).run()
    assert store.open_signals()[0]["closing_odds"] == pytest.approx(2.00)


def test_capture_reports_missing_event(store):
    store.save(signal(event_id="e1"))
    report = ClosingCapture(store, FakeProvider([event(event_id="otro")])).run()
    assert report.captured == 0
    assert report.no_event == 1


def test_capture_reports_missing_selection(store):
    store.save(signal(selection="Empate"))
    report = ClosingCapture(store, FakeProvider([event()])).run()
    assert report.no_price == 1


def test_provider_failure_does_not_crash(store):
    store.save(signal())
    report = ClosingCapture(store, FakeProvider([], fail=True)).run()
    assert report.errors
    assert report.captured == 0


def test_one_fetch_per_sport_not_per_signal(store):
    """La cuota de la API se cuenta por llamada: varias senales del mismo
    deporte deben compartir un solo fetch."""
    store.save(signal(selection="Home"))
    store.save(signal(selection="Away", odds=1.95))
    provider = FakeProvider([event()])
    ClosingCapture(store, provider).run()
    assert provider.calls == 1


def test_no_pending_means_no_api_call(store):
    provider = FakeProvider([event()])
    ClosingCapture(store, provider).run()
    assert provider.calls == 0


# ---------- metrica ----------

def test_clv_positive_when_price_beat_close():
    bets = [{"decimal_odds": 2.10, "closing_odds": 2.00}] * 50
    s = clv_summary(bets)
    assert s.mean_clv > 0
    assert s.beat_rate == 1.0
    assert "ventaja real" in s.verdict


def test_clv_negative_when_line_moved_against():
    bets = [{"decimal_odds": 1.90, "closing_odds": 2.00}] * 50
    s = clv_summary(bets)
    assert s.mean_clv < 0
    assert "perdiendo" in s.verdict


def test_clv_ignores_bets_without_close():
    bets = [
        {"decimal_odds": 2.10, "closing_odds": 2.00},
        {"decimal_odds": 2.10, "closing_odds": None},
        {"decimal_odds": 2.10},
    ]
    assert clv_summary(bets).n == 1


def test_clv_empty_is_safe():
    s = clv_summary([])
    assert s.n == 0 and s.mean_clv == 0.0


def test_clv_significance_needs_consistency():
    """Un CLV medio cero con dispersion alta no debe declararse significativo."""
    bets = ([{"decimal_odds": 2.50, "closing_odds": 2.00}] * 25
            + [{"decimal_odds": 1.60, "closing_odds": 2.00}] * 25)
    assert not clv_summary(bets).significant


def test_clv_pct_is_reported():
    s = clv_summary([{"decimal_odds": 2.10, "closing_odds": 2.00}] * 10)
    assert s.mean_clv_pct == pytest.approx(0.05, abs=1e-9)
