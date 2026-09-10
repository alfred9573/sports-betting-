"""Tests de la estrategia de desacuerdo entre casas."""

from datetime import UTC, datetime, timedelta

import pytest

from betbot.ev.lineshop import LineShopConfig, LineShopEngine
from betbot.types import BookMarket, Event, Market, Outcome, Sport

AHORA = datetime.now(UTC)


def evento(*libros):
    return Event("e1", Sport.NBA, AHORA + timedelta(hours=3), "Home", "Away",
                 books=tuple(
                     BookMarket(nombre, Market.MONEYLINE,
                                (Outcome("Home", loc), Outcome("Away", vis)), AHORA)
                     for nombre, loc, vis in libros
                 ))


def test_detects_soft_book_lagging_sharp():
    """El caso que justifica la estrategia: pinnacle ya movio a 1.60 y
    draftkings sigue pagando 1.75."""
    ev = evento(("pinnacle", 1.60, 2.45), ("draftkings", 1.75, 2.15))
    senales = LineShopEngine().evaluate(ev)
    assert len(senales) == 1
    assert senales[0].bookmaker == "draftkings"
    assert senales[0].selection == "Home"
    assert senales[0].ev > 0


def test_no_signal_when_books_agree():
    ev = evento(("pinnacle", 1.60, 2.45), ("draftkings", 1.59, 2.42))
    assert LineShopEngine().evaluate(ev) == []


def test_requires_a_sharp_book():
    """Sin referencia sharp no hay estrategia: comparar dos blandas entre si no
    dice cual tiene razon."""
    ev = evento(("draftkings", 1.75, 2.15), ("fanduel", 1.80, 2.10))
    assert LineShopEngine().evaluate(ev) == []


def test_never_bets_at_the_sharp_book_itself():
    """Comparar un libro sharp consigo mismo mediria su propio margen y lo
    llamaria ventaja."""
    ev = evento(("pinnacle", 1.60, 2.45))
    assert LineShopEngine().evaluate(ev) == []


def test_picks_the_most_lagging_soft_book():
    ev = evento(("pinnacle", 1.60, 2.45), ("draftkings", 1.70, 2.20),
                ("bovada", 1.85, 2.05))
    senal = LineShopEngine().evaluate(ev)[0]
    assert senal.bookmaker == "bovada"
    assert senal.decimal_odds == 1.85


def test_uses_sharp_price_as_reference_not_a_model():
    """model_prob y fair_prob coinciden: no hay modelo, la referencia ES el
    precio sharp sin vig."""
    ev = evento(("pinnacle", 1.60, 2.45), ("draftkings", 1.75, 2.15))
    s = LineShopEngine().evaluate(ev)[0]
    assert s.model_prob == pytest.approx(s.fair_prob)
    assert s.model_name == "lineshop_v1"


def test_devig_is_applied_to_the_sharp_reference():
    """Sin quitar el vig al libro sharp, su propio margen se contaria como
    ventaja y saldrian senales falsas en cada partido."""
    ev = evento(("pinnacle", 1.90, 1.90), ("draftkings", 1.95, 1.85))
    motor = LineShopEngine()
    ref = motor.sharp_probs(ev, Market.MONEYLINE)
    assert sum(ref.values()) == pytest.approx(1.0)
    assert ref["Home"] == pytest.approx(0.5, abs=1e-6)


def test_longshot_filter_applies():
    ev = evento(("pinnacle", 1.02, 25.0), ("draftkings", 1.05, 40.0))
    assert LineShopEngine(LineShopConfig(max_odds=15.0)).evaluate(ev) == []


def test_stake_respects_cap():
    ev = evento(("pinnacle", 1.30, 4.00), ("draftkings", 1.90, 2.10))
    s = LineShopEngine(LineShopConfig(max_stake_pct=0.02, bankroll=1000,
                                      kelly_fraction=1.0)).evaluate(ev)[0]
    assert s.kelly_stake == pytest.approx(0.02)
    assert s.stake_units == pytest.approx(20.0)


def test_scan_sorts_by_ev():
    a = evento(("pinnacle", 1.60, 2.45), ("draftkings", 1.70, 2.20))
    b = Event("e2", Sport.NBA, AHORA + timedelta(hours=4), "H2", "A2", books=(
        BookMarket("pinnacle", Market.MONEYLINE,
                   (Outcome("H2", 1.60), Outcome("A2", 2.45)), AHORA),
        BookMarket("bovada", Market.MONEYLINE,
                   (Outcome("H2", 1.95), Outcome("A2", 2.00)), AHORA),
    ))
    senales = LineShopEngine().scan([a, b])
    assert [s.ev for s in senales] == sorted([s.ev for s in senales], reverse=True)
