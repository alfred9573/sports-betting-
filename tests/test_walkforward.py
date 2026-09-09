"""Tests del backtest walk-forward.

El primero de este fichero es el mas importante de todo el repo: verifica que el
modelo NO ve el resultado del partido que esta prediciendo. Toda la validacion
descansa en esa propiedad, y si se rompe, las metricas siguen saliendo — mejores
incluso — pero dejan de significar nada.
"""

import pytest

from betbot.backtest.walkforward import walk_forward, walk_forward_elo
from betbot.models.nba import NBAModel


def games(n=200):
    """Serie sintetica con ventaja de local del 70%, sin diferencia de equipos."""
    out = []
    for i in range(n):
        home_win = i % 10 < 7
        out.append(
            {
                "home": "A" if i % 2 == 0 else "B",
                "away": "B" if i % 2 == 0 else "A",
                "home_score": 110 if home_win else 100,
                "away_score": 100 if home_win else 110,
                "neutral": False,
            }
        )
    return out


def games_with_team_skill(n=600, seed=42):
    """Serie donde A es GENUINAMENTE mejor que B: gana el 75% juegue donde juegue.

    La localia se sortea de forma INDEPENDIENTE del resultado (por eso el RNG
    semillado y no un patron por indice: con `i % 2` para la localia y `i % 4`
    para el ganador, las derrotas de A caen siempre en sus partidos fuera y la
    localia queda correlacionada con el resultado — el generador acaba
    fabricando ventaja de local en vez de fuerza de equipo).

    Resultado: tasa base de local ~50% (el baseline no puede acertar) y fuerza
    de equipo real que el Elo si puede aprender.
    """
    import random

    rng = random.Random(seed)
    out = []
    for _ in range(n):
        a_home = rng.random() < 0.5
        a_wins = rng.random() < 0.75
        home_wins = a_home == a_wins
        out.append(
            {
                "home": "A" if a_home else "B",
                "away": "B" if a_home else "A",
                "home_score": 110 if home_wins else 100,
                "away_score": 100 if home_wins else 110,
                "neutral": True,  # sin ventaja de local: aisla la senal de equipo
            }
        )
    return out


def test_model_never_sees_the_game_it_predicts():
    """LA prueba. Se registra el estado en cada prediccion y se comprueba que el
    numero de actualizaciones vistas es exactamente el indice del partido."""
    seen: list[tuple[int, int]] = []

    class Counter:
        def __init__(self):
            self.updates = 0

    data = games(50)
    idx = {"i": 0}

    def predict(model, g):
        seen.append((idx["i"], model.updates))
        return 0.6

    def update(model, g):
        model.updates += 1
        idx["i"] += 1

    walk_forward(data, Counter, predict, update)

    # En el partido i, el modelo debe haber incorporado exactamente i resultados.
    assert all(i == updates for i, updates in seen)
    assert len(seen) == 50


def test_prediction_happens_before_update():
    """Variante directa: si el modelo hubiera visto el resultado, acertaria el
    100%. Un acierto perfecto sobre datos ruidosos delata fuga."""
    order: list[str] = []

    def predict(model, g):
        order.append("predict")
        return 0.5

    def update(model, g):
        order.append("update")

    walk_forward(games(3), lambda: None, predict, update)
    assert order == ["predict", "update"] * 3


def test_skips_are_counted_not_silently_dropped():
    def predict(model, g):
        return None  # el modelo nunca tiene datos suficientes

    with pytest.raises(ValueError, match="ninguna prediccion"):
        walk_forward(games(10), lambda: None, predict, lambda m, g: None)


def test_invalid_probability_is_rejected():
    """Una probabilidad de 0 o 1 hace explotar el log-loss. Mejor fallar ruidoso."""
    with pytest.raises(ValueError, match="probabilidad invalida"):
        walk_forward(games(5), lambda: None, lambda m, g: 1.0, lambda m, g: None)


def test_baseline_uses_actual_base_rate():
    res = walk_forward_elo(games(400), NBAModel)
    assert 0.0 < res.home_win_rate < 1.0
    assert res.baseline_brier > 0
    # el baseline predice siempre lo mismo -> su Brier es la varianza del resultado
    expected = res.home_win_rate * (1 - res.home_win_rate)
    assert res.baseline_brier == pytest.approx(expected, abs=0.01)


def test_elo_learns_real_team_strength():
    """Con senal de equipo real, el Elo debe superar a la tasa base."""
    res = walk_forward_elo(games_with_team_skill(600), NBAModel)
    assert res.n_predictions > 0
    assert res.beats_baseline


def test_elo_does_not_beat_baseline_without_team_signal():
    """Contraparte honesta: si toda la senal es ventaja de local, el Elo no
    aporta nada sobre predecir la tasa base. Que el backtest lo refleje es
    senal de que mide lo que dice medir."""
    res = walk_forward_elo(games(600), NBAModel)
    assert not res.beats_baseline


def test_result_reports_skipped_count():
    res = walk_forward_elo(games(400), NBAModel)
    # min_games=10 por equipo: los primeros partidos no se pueden predecir
    assert res.n_skipped > 0
    assert res.n_predictions + res.n_skipped == 400


def test_calibration_table_is_populated():
    res = walk_forward_elo(games(600), NBAModel)
    assert res.calibration
    assert all("gap" in row for row in res.calibration)


def test_str_output_mentions_baseline():
    out = str(walk_forward_elo(games(400), NBAModel))
    assert "baseline" in out.lower()
    assert "Calibracion" in out
