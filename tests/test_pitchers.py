"""Tests de los ratings de abridor.

Nota: este modulo esta medido y aporta ~+0.0004 de log-loss, o sea nada. Los
tests verifican que hace lo que dice, no que sirva — para eso esta la medicion
documentada en el docstring del modulo.
"""

import pytest

from betbot.models.pitchers import PitcherRatings, elo_to_prob_shift


def test_unknown_pitcher_gets_no_adjustment():
    assert PitcherRatings().rating("Desconocido") == 0.0


def test_pitcher_below_min_starts_gets_no_adjustment():
    """Un abridor con 3 aperturas no tiene rating fiable. Devolver 0 es
    preferible a devolver un numero basado en tres partidos."""
    p = PitcherRatings(min_starts=15)
    for _ in range(3):
        p.update("Nuevo", "Otro", home_won=True, expected_home=0.5)
    assert p.rating("Nuevo") == 0.0
    assert not p.is_reliable("Nuevo")


def test_becomes_reliable_after_min_starts():
    p = PitcherRatings(min_starts=5)
    for _ in range(5):
        p.update("A", "B", home_won=True, expected_home=0.5)
    assert p.is_reliable("A")
    assert p.rating("A") > 0


def test_winning_beyond_expectation_raises_rating():
    p = PitcherRatings(min_starts=1, k=10)
    p.update("Bueno", "Rival", home_won=True, expected_home=0.3)
    assert p.rating("Bueno") > 0
    assert p.rating("Rival") < 0


def test_winning_as_expected_barely_moves_rating():
    """Si el modelo de equipo ya esperaba la victoria, el abridor no debe
    llevarse el credito: solo recibe lo que el equipo no explicaba."""
    esperado = PitcherRatings(min_starts=1, k=10)
    esperado.update("A", "B", home_won=True, expected_home=0.95)
    sorpresa = PitcherRatings(min_starts=1, k=10)
    sorpresa.update("A", "B", home_won=True, expected_home=0.20)
    assert sorpresa.rating("A") > esperado.rating("A")


def test_ratings_are_zero_sum():
    p = PitcherRatings(min_starts=1, k=10)
    p.update("A", "B", home_won=True, expected_home=0.4)
    assert p.ratings["A"] + p.ratings["B"] == pytest.approx(0.0)


def test_adjustment_is_capped():
    """Sin techo, una racha corta y afortunada genera un ajuste absurdo que se
    traduce en EV fantasma."""
    p = PitcherRatings(min_starts=1, k=50, max_adjustment=60)
    for _ in range(40):
        p.update("Racha", "Rival", home_won=True, expected_home=0.1)
    assert p.rating("Racha") == 60.0
    assert p.rating("Rival") == -60.0


def test_matchup_adjustment_is_the_difference():
    p = PitcherRatings(min_starts=1, k=10)
    p.update("As", "Malo", home_won=True, expected_home=0.3)
    esperado = p.rating("As") - p.rating("Malo")
    assert p.matchup_adjustment("As", "Malo") == pytest.approx(esperado)


def test_missing_pitcher_name_is_ignored():
    """Un partido sin abridor identificado no debe corromper ningun rating."""
    p = PitcherRatings(min_starts=1)
    p.update("", "Rival", home_won=True, expected_home=0.5)
    assert p.ratings == {}


def test_regress_shrinks_toward_zero():
    p = PitcherRatings(min_starts=1, k=10)
    for _ in range(10):
        p.update("A", "B", home_won=True, expected_home=0.3)
    antes = p.ratings["A"]
    p.regress(0.5)
    assert p.ratings["A"] == pytest.approx(antes * 0.5)
    assert p.starts["A"] == 0


def test_top_only_lists_reliable_pitchers():
    p = PitcherRatings(min_starts=5, k=10)
    for _ in range(6):
        p.update("Fiable", "Rival", home_won=True, expected_home=0.3)
    p.update("Novato", "Otro", home_won=True, expected_home=0.3)
    nombres = [n for n, _, _ in p.top()]
    assert "Fiable" in nombres
    assert "Novato" not in nombres


def test_n_reliable_counts_correctly():
    p = PitcherRatings(min_starts=2, k=5)
    for _ in range(2):
        p.update("A", "B", home_won=True, expected_home=0.5)
    assert p.n_reliable == 2


def test_elo_to_prob_shift_reference_values():
    """30 puntos de Elo son ~4,3 puntos de probabilidad. Sirve para interpretar
    si un ajuste es grande o cosmetico."""
    assert elo_to_prob_shift(0) == pytest.approx(0.0)
    assert elo_to_prob_shift(30) == pytest.approx(0.043, abs=0.002)
    assert elo_to_prob_shift(-30) == pytest.approx(-0.043, abs=0.002)
