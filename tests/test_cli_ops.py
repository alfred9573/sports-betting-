"""Tests de las piezas operativas del CLI: modelo entrenado y frescura."""

from datetime import date, timedelta

import pytest

from betbot.cli import load_trained_model, model_freshness
from betbot.ingest.store import GameStore
from betbot.ingest.types import GameResult
from betbot.types import Sport


def game(day, home="Boston Celtics", away="Miami Heat", sport=Sport.NBA,
         hs=110, as_=100, season=2015):
    return GameResult(
        sport=sport, game_date=day, season=season, home_team=home, away_team=away,
        home_score=hs, away_score=as_, source="test",
        source_id=f"{day.isoformat()}{home[:3]}{hs}",
    )


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "games.db")


def test_load_without_data_returns_actionable_error(db):
    """El error debe decir QUE comando corregir, no solo que falta algo."""
    model, err = load_trained_model(Sport.NBA, db)
    assert model is None
    assert "ingest --sport nba" in err


def test_load_trains_the_model(db):
    """El bug que esto previene: `scan` creaba el modelo sin entrenarlo, asi que
    predict() devolvia None siempre y el bot nunca emitia una senal."""
    store = GameStore(db)
    base = date(2015, 1, 1)
    store.upsert_many([game(base + timedelta(days=i)) for i in range(20)])

    model, err = load_trained_model(Sport.NBA, db)
    assert err is None
    assert model.ratings.is_reliable("Boston Celtics")
    assert model.ratings.rating("Boston Celtics") > 1500


def test_load_soccer_uses_league_preset(db):
    store = GameStore(db)
    base = date(2020, 1, 1)
    store.upsert_many([
        game(base + timedelta(days=i * 3), home="Club America", away="Cruz Azul",
             sport=Sport.SOCCER_LIGA_MX, hs=2, as_=1, season=2020)
        for i in range(20)
    ])
    model, err = load_trained_model(Sport.SOCCER_LIGA_MX, db)
    assert err is None
    assert model.home_advantage == pytest.approx(1.28)  # preset de Liga MX


def test_freshness_reports_none_without_data(db):
    days, last = model_freshness(Sport.NBA, db)
    assert days is None and last is None


def test_freshness_detects_stale_data(db):
    """El fallo mas silencioso del sistema: escanear partidos de hoy con ratings
    de hace anos. No da error, solo señales sin fundamento."""
    store = GameStore(db)
    store.upsert_many([game(date(2015, 6, 16))])
    days, last = model_freshness(Sport.NBA, db)
    assert last == "2015-06-16"
    assert days > 3000


def test_freshness_detects_current_data(db):
    store = GameStore(db)
    today = date.today()
    store.upsert_many([game(today - timedelta(days=2), season=today.year)])
    days, _ = model_freshness(Sport.NBA, db)
    assert days == 2


def test_freshness_uses_latest_game(db):
    store = GameStore(db)
    store.upsert_many([game(date(2015, 1, 1)), game(date(2015, 6, 1))])
    _, last = model_freshness(Sport.NBA, db)
    assert last == "2015-06-01"
