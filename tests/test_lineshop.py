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
    # Las claves llevan la linea: en moneyline es None, en totales el numero.
    assert sum(ref.values()) == pytest.approx(1.0)
    assert ref[("Home", None)] == pytest.approx(0.5, abs=1e-6)


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


# ---------- mercados con linea (totales y handicap) ----------

def evento_totales(*libros, linea_por_libro=None):
    """libros: (nombre, precio_over, precio_under). linea_por_libro permite dar
    una linea distinta a cada casa."""
    lineas = linea_por_libro or {}
    return Event("t1", Sport.NFL, AHORA + timedelta(hours=3), "Chiefs", "Bills",
                 books=tuple(
                     BookMarket(nombre, Market.TOTALS,
                                (Outcome("Over", over, lineas.get(nombre, 45.5)),
                                 Outcome("Under", under, lineas.get(nombre, 45.5))),
                                AHORA)
                     for nombre, over, under in libros
                 ))


def test_detects_value_on_totals():
    """El caso central de la estrategia: mercado 50/50 y una casa pagando +105
    donde la sharp paga -110. Son 2,5% de EV sin predecir nada."""
    ev = evento_totales(("pinnacle", 1.909, 1.909), ("draftkings", 2.05, 1.80))
    senales = LineShopEngine().evaluate(ev, Market.TOTALS)
    assert len(senales) == 1
    s = senales[0]
    assert s.selection == "Over"
    assert s.point == pytest.approx(45.5)
    assert s.ev == pytest.approx(0.025, abs=0.001)


def test_never_compares_across_different_lines():
    """'Over 45.5' y 'Over 46.5' son apuestas DISTINTAS. Compararlas como si
    fueran la misma seria inventarse ventaja justo donde se supone que esta."""
    ev = evento_totales(
        ("pinnacle", 1.909, 1.909), ("fanduel", 2.20, 1.70),
        linea_por_libro={"fanduel": 46.5},
    )
    assert LineShopEngine().evaluate(ev, Market.TOTALS) == []


def test_line_is_preserved_in_the_signal():
    ev = evento_totales(("pinnacle", 1.909, 1.909), ("draftkings", 2.05, 1.80))
    assert LineShopEngine().evaluate(ev, Market.TOTALS)[0].point == pytest.approx(45.5)


def test_detects_value_on_spreads():
    ev = Event("s1", Sport.NFL, AHORA + timedelta(hours=3), "Chiefs", "Bills", books=(
        BookMarket("pinnacle", Market.SPREAD,
                   (Outcome("Chiefs", 1.909, -3.5), Outcome("Bills", 1.909, 3.5)), AHORA),
        BookMarket("bovada", Market.SPREAD,
                   (Outcome("Chiefs", 2.10, -3.5), Outcome("Bills", 1.75, 3.5)), AHORA),
    ))
    s = LineShopEngine().evaluate(ev, Market.SPREAD)[0]
    assert s.selection == "Chiefs"
    assert s.point == pytest.approx(-3.5)
    assert s.ev > 0


def test_scan_covers_the_three_markets_by_default():
    ev = evento_totales(("pinnacle", 1.909, 1.909), ("draftkings", 2.05, 1.80))
    assert LineShopEngine().scan([ev])          # totales incluido por defecto
    assert LineShopEngine().scan([ev], markets=(Market.MONEYLINE,)) == []


def test_min_edge_does_not_cancel_min_ev():
    """edge y ev no son criterios independientes: EV = edge x cuota. Un min_edge
    alto anula al min_ev y rechaza el caso central de la estrategia."""
    ev = evento_totales(("pinnacle", 1.909, 1.909), ("draftkings", 2.05, 1.80))
    assert LineShopEngine(LineShopConfig(min_edge=0.005)).evaluate(ev, Market.TOTALS)
    assert LineShopEngine(LineShopConfig(min_edge=0.015)).evaluate(ev, Market.TOTALS) == []
