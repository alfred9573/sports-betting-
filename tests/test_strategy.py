"""Tests de la simulacion de estrategia contra odds reales.

Los controles de este fichero son lo que permite creerse el resultado: si el
apostador aleatorio no pierde el margen y el oraculo no gana, el simulador esta
roto y cualquier conclusion sobre el modelo no vale nada.
"""

import pytest

from betbot.backtest.strategy import simulate
from betbot.ev.engine import EVConfig


def partidos(n=400, seed=3, odds_local=2.0, odds_visita=2.0):
    """Serie con odds justas (sin margen) y resultado 50/50."""
    import random

    rng = random.Random(seed)
    out = []
    for i in range(n):
        local_gana = rng.random() < 0.5
        out.append({
            "home": f"H{i % 8}", "away": f"A{i % 8}",
            "home_score": 2 if local_gana else 1,
            "away_score": 1 if local_gana else 2,
            "home_odds": odds_local, "away_odds": odds_visita,
            "date": f"2020-01-{i % 28 + 1:02d}",
        })
    return out


CFG = EVConfig(bankroll=1000, min_ev=-99, min_edge=-99, kelly_fraction=1.0,
               max_stake_pct=0.01, min_odds=1.0, max_odds=99)


def test_oracle_wins_big():
    """Control imprescindible: quien conoce el resultado debe ganar mucho. Si no,
    el simulador esta invertido o roto."""
    juegos = partidos(300)
    res = simulate(
        juegos, lambda: None,
        lambda m, g: 0.999 if g["home_score"] > g["away_score"] else 0.001,
        lambda m, g: None, CFG,
    )
    assert res.roi > 0.5
    assert res.tasa_acierto > 0.95


def test_random_bettor_loses_roughly_the_margin():
    """Con odds justas (sin margen) el apostador aleatorio debe rondar el 0%."""
    import random

    rng = random.Random(9)
    res = simulate(partidos(1200), lambda: None,
                   lambda m, g: rng.random(), lambda m, g: None, CFG)
    assert res.n_apostados > 500
    assert abs(res.roi) < 0.10


def test_margin_is_actually_lost():
    """Con odds de 1.90/1.90 (5,3% de margen) el apostador aleatorio pierde."""
    import random

    rng = random.Random(4)
    juegos = partidos(1500, odds_local=1.90, odds_visita=1.90)
    res = simulate(juegos, lambda: None,
                   lambda m, g: rng.random(), lambda m, g: None, CFG)
    assert res.roi < 0


def test_no_bets_when_model_agrees_with_market():
    """Un modelo que coincide con el mercado no debe encontrar valor con los
    filtros por defecto."""
    juegos = partidos(300, odds_local=2.0, odds_visita=2.0)
    res = simulate(juegos, lambda: None, lambda m, g: 0.5, lambda m, g: None,
                   EVConfig(bankroll=1000))
    assert res.n_apostados == 0
    assert "Ninguna apuesta" in str(res)


def test_filters_reject_a_thin_edge():
    """Con una ventaja pequena (51% frente a un mercado justo al 50%) los filtros
    por defecto deben rechazar todo: no llega al 3% de EV ni a 2pp de edge.

    Nota sobre el caso contrario, que es la patologia real del sistema: si el
    modelo dice 65% donde el mercado dice 50%, el EV sale +30% y pasan TODOS los
    partidos. Los filtros solo protegen de ventajas pequenas; ante un modelo que
    discrepa masivamente del mercado no filtran nada, porque no distinguen
    "ventaja enorme" de "modelo equivocado"."""
    juegos = partidos(600)
    sin_filtros = simulate(juegos, lambda: None, lambda m, g: 0.51,
                           lambda m, g: None, CFG)
    con_filtros = simulate(juegos, lambda: None, lambda m, g: 0.51,
                           lambda m, g: None, EVConfig(bankroll=1000))
    assert sin_filtros.n_apostados > 0
    assert con_filtros.n_apostados == 0


def test_prediction_happens_before_update():
    """Misma regla que el walk-forward: nada de ver el resultado antes."""
    orden = []
    simulate(partidos(3), lambda: None,
             lambda m, g: (orden.append("predice"), 0.5)[1],
             lambda m, g: orden.append("actualiza"), CFG)
    assert orden == ["predice", "actualiza"] * 3


def test_kelly_uses_live_bankroll():
    """El stake debe calcularse sobre el bankroll VIVO: es como se apuesta de
    verdad y hace que las rachas compongan en los dos sentidos."""
    juegos = partidos(60)
    res = simulate(juegos, lambda: None,
                   lambda m, g: 0.999 if g["home_score"] > g["away_score"] else 0.001,
                   lambda m, g: None, CFG)
    stakes = [a.stake for a in res.apuestas]
    assert stakes[-1] > stakes[0]     # el bankroll crecio, el stake tambien


def test_drawdown_is_tracked():
    import random

    rng = random.Random(11)
    res = simulate(partidos(800, odds_local=1.90, odds_visita=1.90), lambda: None,
                   lambda m, g: rng.random(), lambda m, g: None, CFG)
    assert 0.0 < res.max_drawdown <= 1.0


def test_games_without_odds_are_skipped():
    juegos = partidos(50)
    for g in juegos:
        g.pop("home_odds")
    res = simulate(juegos, lambda: None, lambda m, g: 0.9, lambda m, g: None, CFG)
    assert res.n_evaluados == 0
    assert res.n_apostados == 0


def test_significance_requires_more_than_noise():
    """Un ROI dentro de dos errores tipicos no debe declararse significativo."""
    import random

    rng = random.Random(5)
    res = simulate(partidos(300), lambda: None,
                   lambda m, g: rng.random(), lambda m, g: None, CFG)
    if abs(res.roi) < 2 * res.stderr:
        assert not res.significativo


def test_result_reports_market_comparison_fields():
    res = simulate(partidos(200), lambda: None, lambda m, g: 0.7,
                   lambda m, g: None, CFG)
    assert res.apuestas
    a = res.apuestas[0]
    assert 0 < a.p_mercado < 1
    assert a.p_modelo == pytest.approx(0.7) or a.p_modelo == pytest.approx(0.3)
