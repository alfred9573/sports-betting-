"""Tests del modelo NFL y del walk-forward de futbol."""

from datetime import UTC, datetime, timedelta

import pytest

from betbot.backtest.walkforward import walk_forward_multiclass, walk_forward_soccer
from betbot.ingest.sources.nfl_nflverse import NFLverse
from betbot.models.nfl import NFLModel
from betbot.models.soccer import PoissonSoccerModel
from betbot.types import Event, Sport

NOW = datetime.now(UTC)


def event(home="Kansas City Chiefs", away="Buffalo Bills"):
    return Event("e1", Sport.NFL, NOW + timedelta(hours=4), home, away)


# ---------- fuente NFL ----------

def _row(season="2023", home="KC", away="BUF", hs="27", as_="24",
         gameday="2023-10-01", gtype="REG", loc="Home", gid=None):
    return {
        "season": season, "game_type": gtype, "gameday": gameday,
        "home_team": home, "away_team": away, "home_score": hs, "away_score": as_,
        "location": loc, "game_id": gid or f"{season}_01_{away}_{home}",
    }


def test_nfl_parses_basic_game():
    g = NFLverse().parse([_row()])[0]
    assert g.home_team == "Kansas City Chiefs"
    assert g.away_team == "Buffalo Bills"
    assert (g.home_score, g.away_score) == (27, 24)


def test_nfl_skips_future_games_without_score():
    """El dataset incluye partidos programados sin marcador. Meterlos como 0-0
    seria desastroso para el entrenamiento."""
    src = NFLverse()
    assert src.parse([_row(hs="", as_="")]) == []


def test_nfl_relocated_franchises_unify():
    """STL y LA son los mismos Rams. Sin unificarlos, la mudanza parte la
    franquicia en dos equipos que arrancan sin historial."""
    old = NFLverse().parse([_row(season="2015", home="STL", away="SEA")])[0]
    new = NFLverse().parse([_row(season="2017", home="LA", away="SEA")])[0]
    assert old.home_team == new.home_team == "Los Angeles Rams"


@pytest.mark.parametrize("code,expected", [
    ("OAK", "Las Vegas Raiders"),
    ("LV", "Las Vegas Raiders"),
    ("SD", "Los Angeles Chargers"),
    ("LAC", "Los Angeles Chargers"),
])
def test_nfl_all_relocations(code, expected):
    assert NFLverse().parse([_row(home=code, away="KC")])[0].home_team == expected


def test_nfl_marks_neutral_site():
    assert NFLverse().parse([_row(loc="Neutral")])[0].neutral_site


def test_nfl_marks_playoff():
    assert NFLverse().parse([_row(gtype="SB")])[0].playoff
    assert not NFLverse().parse([_row(gtype="REG")])[0].playoff


def test_nfl_can_exclude_playoffs():
    src = NFLverse(include_playoffs=False)
    assert src.parse([_row(gtype="SB")]) == []


def test_nfl_season_filter():
    rows = [_row(season="2020"), _row(season="2021", gid="x")]
    assert len(NFLverse().parse(rows, seasons={2021})) == 1


# ---------- modelo NFL ----------

def test_nfl_model_refuses_without_history():
    assert NFLModel().predict(event()) is None


def test_nfl_model_predicts_after_fit():
    m = NFLModel().fit(
        [{"home": "Kansas City Chiefs", "away": "Buffalo Bills",
          "home_score": 27, "away_score": 20} for _ in range(12)]
    )
    p = m.predict(event())
    assert p is not None
    assert sum(p.probs.values()) == pytest.approx(1.0)
    assert p.probs["Kansas City Chiefs"] > 0.5


# ---------- walk-forward de futbol ----------

def _soccer_games(n=400, seed=7):
    """Partidos sinteticos en fechas crecientes, como los entrega GameStore."""
    import random
    from datetime import date
    from datetime import timedelta as td

    rng = random.Random(seed)
    teams = [f"T{i}" for i in range(10)]
    base = date(2015, 1, 1)
    out = []
    for i in range(n):
        h, a = rng.sample(teams, 2)
        hs, as_ = rng.randint(0, 4), rng.randint(0, 3)
        out.append({
            "home": h, "away": a,
            "home_score": hs, "away_score": as_,
            "home_goals": hs, "away_goals": as_,
            "date": (base + td(days=i * 3)).isoformat(),
            "season": 2015,
        })
    return out


def test_soccer_walk_forward_produces_three_class_probs():
    res = walk_forward_soccer(_soccer_games(500))
    assert res.n_predictions > 0
    assert all(len(p) == 3 for p in res.probs)
    assert all(abs(sum(p) - 1.0) < 1e-6 for p in res.probs)


def test_soccer_walk_forward_reports_rps_and_baseline():
    res = walk_forward_soccer(_soccer_games(500))
    assert res.rps > 0
    assert res.baseline_rps > 0
    assert "RPS" in str(res)


def test_multiclass_rejects_bad_model_output():
    def predict(m, g):
        return [0.5, 0.5, 0.5]  # no suman 1

    with pytest.raises(ValueError, match="suman"):
        walk_forward_multiclass(
            [{"home": "A", "away": "B", "home_score": 1, "away_score": 0}],
            lambda: None, predict, lambda m, g: None,
        )


def test_multiclass_raises_when_no_predictions():
    with pytest.raises(ValueError, match="ninguna prediccion"):
        walk_forward_multiclass(
            [{"home": "A", "away": "B", "home_score": 1, "away_score": 0}],
            lambda: None, lambda m, g: None, lambda m, g: None,
        )


def test_soccer_model_uses_calibrated_defaults():
    """Los defaults se midieron sobre 8.360 partidos reales; que no se cambien
    por accidente."""
    m = PoissonSoccerModel()
    assert m.home_advantage == pytest.approx(1.44)
    assert m.rho == pytest.approx(-0.28)
    assert m.decay == pytest.approx(0.0030)
