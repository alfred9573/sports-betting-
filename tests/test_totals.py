"""Tests del modelo de puntuacion total (mercados over/under)."""

import pytest

from betbot.models.totals import TotalsModel, normal_cdf


def entrena(m, n=20, local=28, visita=21, equipos=("A", "B")):
    for _ in range(n):
        m.update(equipos[0], equipos[1], local, visita)
    return m


def test_normal_cdf_basics():
    assert normal_cdf(0) == pytest.approx(0.5)
    assert normal_cdf(1.96) == pytest.approx(0.975, abs=0.001)
    assert normal_cdf(10, 0, 0) == 0.5  # sigma cero no debe reventar


def test_refuses_to_predict_without_history():
    assert TotalsModel().total_esperado("A", "B") is None
    assert TotalsModel().prob_over("A", "B", 45.5) is None


def test_expected_total_reflects_scoring():
    m = entrena(TotalsModel(), 20, local=28, visita=21)
    assert m.total_esperado("A", "B") == pytest.approx(49.0, abs=1.0)


def test_prob_over_moves_with_the_line():
    m = entrena(TotalsModel())
    baja = m.prob_over("A", "B", 40.5)
    alta = m.prob_over("A", "B", 55.5)
    assert baja > 0.5 > alta


def test_prob_over_is_symmetric_around_expectation():
    m = entrena(TotalsModel())
    esperado = m.total_esperado("A", "B")
    assert m.prob_over("A", "B", esperado) == pytest.approx(0.5, abs=1e-9)


def test_sigma_is_estimated_from_past_errors_only():
    """Fijar la sigma a un valor bonito seria meter conocimiento del futuro por
    la puerta de atras: en este mercado decide cuanta probabilidad cae a cada
    lado de la linea, asi que manda tanto como la media."""
    m = TotalsModel()
    assert m.sigma == 13.5          # valor de arranque hasta tener datos
    import random

    rng = random.Random(2)
    for _ in range(200):
        m.update("A", "B", rng.randint(10, 40), rng.randint(10, 40))
    assert m.sigma != 13.5
    assert 1.0 < m.sigma < 40.0


def test_defense_matters_as_much_as_offense():
    """Un equipo que anota mucho contra otro que encaja poco no debe producir un
    total inflado."""
    ofensivo = TotalsModel()
    for _ in range(20):
        ofensivo.update("Anotador", "Rival", 35, 20)
        ofensivo.update("Muro", "Rival2", 20, 10)
    total = ofensivo.total_esperado("Anotador", "Muro")
    assert total is not None
    assert total < 35 + 20   # la defensa del rival tira del total hacia abajo


def test_league_average_is_learned():
    m = entrena(TotalsModel(), 30, local=30, visita=30)
    assert m.media_liga == pytest.approx(30.0, abs=0.5)


def test_new_season_regresses_toward_league_average():
    m = entrena(TotalsModel(), 30, local=40, visita=10)
    antes = m.total_esperado("A", "B")
    m.nueva_temporada(0.5)
    despues = m.total_esperado("A", "B")
    assert antes is not None and despues is not None
    # el total total apenas cambia, pero el reparto se acerca a la media
    assert m._equipo("A").ataque(m.media_liga) < 40


def test_min_games_gate():
    m = TotalsModel(min_games=6)
    for _ in range(3):
        m.update("A", "B", 24, 21)
    assert m.total_esperado("A", "B") is None
    for _ in range(5):
        m.update("A", "B", 24, 21)
    assert m.total_esperado("A", "B") is not None
