import math

import pytest

from betbot.odds.devig import (
    american_to_decimal,
    decimal_to_american,
    devig,
    implied_prob,
    overround,
)


def test_american_decimal_roundtrip():
    for a in (-350, -200, -110, 105, 150, 400):
        assert decimal_to_american(american_to_decimal(a)) == pytest.approx(a, rel=1e-9)


def test_implied_prob_rejects_invalid():
    with pytest.raises(ValueError):
        implied_prob(1.0)


def test_overround_detects_vig():
    # -110 / -110, el clasico de spread: 4.76% de margen
    o = [american_to_decimal(-110)] * 2
    assert overround(o) == pytest.approx(1.0476, abs=1e-4)


@pytest.mark.parametrize("method", ["multiplicative", "power", "shin"])
def test_devig_normalizes_two_way(method):
    p = devig([1.91, 1.91], method)
    assert sum(p) == pytest.approx(1.0, abs=1e-9)
    # mercado simetrico -> ambos lados al 50%
    assert p[0] == pytest.approx(0.5, abs=1e-6)


@pytest.mark.parametrize("method", ["multiplicative", "power", "shin"])
def test_devig_normalizes_three_way(method):
    p = devig([2.10, 3.40, 3.60], method)
    assert sum(p) == pytest.approx(1.0, abs=1e-9)
    assert all(0 < x < 1 for x in p)


def test_devig_reduces_every_probability():
    """Quitar vig SIEMPRE baja la probabilidad de cada lado. Este es el bug que
    convierte un bot rentable en uno que apuesta ruido."""
    odds = [1.67, 2.30]
    raw = [implied_prob(o) for o in odds]
    for method in ("multiplicative", "power", "shin"):
        fair = devig(odds, method)
        assert all(f < r for f, r in zip(fair, raw, strict=True))


def test_shin_between_multiplicative_and_power():
    odds = [1.30, 4.50]  # favorito claro: donde mas divergen los metodos
    mult = devig(odds, "multiplicative")[0]
    powr = devig(odds, "power")[0]
    shin = devig(odds, "shin")[0]
    assert min(mult, powr) <= shin <= max(mult, powr)


def test_devig_requires_full_market():
    with pytest.raises(ValueError):
        devig([1.91])


def test_devig_rejects_unknown_method():
    with pytest.raises(ValueError):
        devig([1.91, 1.91], "nope")


def test_devig_handles_no_vig_market():
    """Un mercado sin margen (exchange con comision aparte) no debe romper Shin."""
    p = devig([2.0, 2.0], "shin")
    assert sum(p) == pytest.approx(1.0, abs=1e-9)


def test_longshot_devig_stays_sane():
    p = devig([1.05, 15.0], "shin")
    assert 0.9 < p[0] < 1.0
    assert sum(p) == pytest.approx(1.0, abs=1e-9)
    assert not any(math.isnan(x) for x in p)
