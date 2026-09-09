from datetime import UTC, datetime, timedelta

import pytest

from betbot.models.elo import EloConfig, EloRatings
from betbot.models.mlb import MLBModel, pythagorean_expectation
from betbot.models.nba import NBAModel
from betbot.models.soccer import PoissonSoccerModel
from betbot.types import Event, Sport

NOW = datetime.now(UTC)


def event(home="Home", away="Away", sport=Sport.NBA, eid="e1"):
    return Event(eid, sport, NOW + timedelta(hours=4), home, away)


# ---------- Elo ----------

def test_elo_starts_even_with_home_advantage():
    e = EloRatings(EloConfig(home_advantage=60))
    p = e.win_prob("A", "B")
    assert 0.5 < p < 0.65  # solo la ventaja de local


def test_elo_neutral_site_is_coin_flip():
    e = EloRatings()
    assert e.win_prob("A", "B", neutral=True) == pytest.approx(0.5)


def test_elo_is_zero_sum():
    e = EloRatings(EloConfig(mov_multiplier=False))
    before = e.rating("A") + e.rating("B")
    e.update("A", "B", 110, 100)
    assert e.rating("A") + e.rating("B") == pytest.approx(before)


def test_elo_rewards_winner():
    e = EloRatings()
    e.update("A", "B", 120, 90)
    assert e.rating("A") > 1500 > e.rating("B")


def test_elo_bigger_margin_moves_rating_more():
    close, blowout = EloRatings(), EloRatings()
    close.update("A", "B", 101, 100)
    blowout.update("A", "B", 130, 90)
    assert blowout.rating("A") > close.rating("A")


def test_elo_upset_moves_more_than_expected_win():
    """Ganar como favoritisimo debe mover poco; la sorpresa debe mover mucho."""
    e = EloRatings(EloConfig(mov_multiplier=False))
    e.ratings = {"Fuerte": 1700, "Debil": 1300}
    expected = EloRatings(EloConfig(mov_multiplier=False))
    expected.ratings = dict(e.ratings)
    expected.update("Fuerte", "Debil", 110, 100)
    upset = EloRatings(EloConfig(mov_multiplier=False))
    upset.ratings = dict(e.ratings)
    upset.update("Debil", "Fuerte", 110, 100)
    assert abs(upset.rating("Debil") - 1300) > abs(expected.rating("Fuerte") - 1700)


def test_new_season_regresses_toward_mean():
    e = EloRatings(EloConfig(regression_to_mean=0.25))
    e.ratings = {"A": 1700}
    e.games_played = {"A": 82}
    e.new_season()
    assert e.rating("A") == pytest.approx(1650)
    assert e.games_played["A"] == 0


def test_min_games_gate():
    e = EloRatings(EloConfig(min_games=10))
    assert not e.is_reliable("A")
    for _ in range(10):
        e.update("A", "B", 110, 100)
    assert e.is_reliable("A")


# ---------- NBA ----------

def test_nba_refuses_to_predict_without_history():
    assert NBAModel().predict(event("Boston Celtics", "Miami Heat")) is None


def test_nba_predicts_after_fit():
    m = NBAModel().fit(
        [{"home": "A", "away": "B", "home_score": 110, "away_score": 100} for _ in range(20)]
    )
    p = m.predict(event("A", "B"))
    assert p is not None
    assert sum(p.probs.values()) == pytest.approx(1.0)
    assert p.probs["A"] > 0.5


def test_nba_shrinkage_pulls_toward_even():
    games = [{"home": "A", "away": "B", "home_score": 130, "away_score": 90} for _ in range(30)]
    m = NBAModel(shrink=0.90).fit(games)
    raw = NBAModel(shrink=1.0).fit(games)
    p_shrunk = m.predict(event("A", "B")).probs["A"]
    p_raw = raw.predict(event("A", "B")).probs["A"]
    assert 0.5 < p_shrunk < p_raw


# ---------- MLB ----------

def test_pythagorean_even_runs_is_half():
    assert pythagorean_expectation(700, 700) == pytest.approx(0.5)


def test_pythagorean_rewards_run_differential():
    assert pythagorean_expectation(800, 650) > 0.5
    assert pythagorean_expectation(600, 750) < 0.5


def test_pythagorean_handles_zero():
    assert pythagorean_expectation(0, 0) == 0.5


def test_mlb_needs_history():
    assert MLBModel().predict(event("NYY", "BOS", Sport.MLB)) is None


def test_mlb_predicts_and_normalizes():
    m = MLBModel().fit(
        [{"home": "NYY", "away": "BOS", "home_score": 6, "away_score": 3} for _ in range(40)]
    )
    p = m.predict(event("NYY", "BOS", Sport.MLB))
    assert p is not None
    assert sum(p.probs.values()) == pytest.approx(1.0)
    assert p.probs["NYY"] > 0.5


def test_mlb_probabilities_stay_in_baseball_range():
    """En MLB casi ningun partido pasa del 65% para un lado. Un modelo que
    escupe 80% esta roto, y generaria senales de valor falsas todo el dia."""
    m = MLBModel().fit(
        [{"home": "NYY", "away": "BOS", "home_score": 10, "away_score": 1} for _ in range(60)]
    )
    p = m.predict(event("NYY", "BOS", Sport.MLB))
    assert p.probs["NYY"] < 0.75


def test_mlb_starting_pitcher_shifts_probability():
    games = [{"home": "NYY", "away": "BOS", "home_score": 5, "away_score": 4} for _ in range(40)]
    base = MLBModel().fit(games)
    ace = MLBModel().fit(list(games))
    ace.pitcher_elo = {"Cole": 35.0}
    ace.starting_pitchers = {"e1": ("Cole", "")}
    assert ace.predict(event("NYY", "BOS", Sport.MLB)).probs["NYY"] > \
           base.predict(event("NYY", "BOS", Sport.MLB)).probs["NYY"]


# ---------- Futbol ----------

def test_soccer_needs_history():
    assert PoissonSoccerModel().predict(event("Barcelona", "Getafe", Sport.SOCCER_LA_LIGA)) is None


def test_soccer_1x2_sums_to_one():
    m = PoissonSoccerModel().fit(
        [{"home": "A", "away": "B", "home_goals": 2, "away_goals": 1} for _ in range(20)]
    )
    p = m.predict(event("A", "B", Sport.SOCCER_EPL))
    assert p is not None
    assert sum(p.probs.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(p.probs) == {"A", "B", "Draw"}


def test_dixon_coles_increases_draw_probability():
    """Sin esta correccion el Poisson subestima el empate ~3pp, justo en el rango
    de precios donde el bot creeria ver valor."""
    m = PoissonSoccerModel(rho=-0.13)
    with_dc = sum(m.score_matrix(1.5, 1.1)[i][i] for i in range(11))
    m.rho = 0.0
    without_dc = sum(m.score_matrix(1.5, 1.1)[i][i] for i in range(11))
    assert with_dc > without_dc
    assert (with_dc - without_dc) > 0.02


def test_score_matrix_is_a_distribution():
    m = PoissonSoccerModel()
    matrix = m.score_matrix(1.4, 1.2)
    assert sum(sum(r) for r in matrix) == pytest.approx(1.0, abs=1e-9)
    assert all(c >= 0 for r in matrix for c in r)


def test_totals_market_sums_to_one():
    m = PoissonSoccerModel().fit(
        [{"home": "A", "away": "B", "home_goals": 2, "away_goals": 1} for _ in range(20)]
    )
    probs = m.total_goals_probs(event("A", "B", Sport.SOCCER_EPL), 2.5)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_stronger_attack_gets_more_expected_goals():
    m = PoissonSoccerModel().fit(
        [
            {"home": "Fuerte", "away": "Debil", "home_goals": 4, "away_goals": 0},
            {"home": "Debil", "away": "Fuerte", "home_goals": 0, "away_goals": 3},
        ] * 10
    )
    lh, la = m.expected_goals("Fuerte", "Debil")
    assert lh > la
