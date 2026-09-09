"""Tests de la capa de ingesta: parseo, almacen y ausencia de fuga temporal."""

from datetime import date

import pytest

from betbot.ingest.store import GameStore
from betbot.ingest.types import GameResult
from betbot.types import Sport


def game(day=1, home="Boston Celtics", away="Miami Heat", hs=110, as_=100,
         season=2015, source_id=None, source="test"):
    return GameResult(
        sport=Sport.NBA, game_date=date(2015, 1, day), season=season,
        home_team=home, away_team=away, home_score=hs, away_score=as_,
        source=source, source_id=source_id or f"g{day}{home[:3]}{hs}",
    )


# ---------- GameResult ----------

def test_home_won_and_draw():
    assert game(hs=110, as_=100).home_won
    assert not game(hs=100, as_=110).home_won
    assert game(hs=100, as_=100).is_draw


def test_rejects_negative_score():
    with pytest.raises(ValueError, match="negativo"):
        game(hs=-1)


def test_rejects_team_playing_itself():
    with pytest.raises(ValueError, match="si mismo"):
        game(home="Boston Celtics", away="Boston Celtics")


def test_requires_source_id():
    with pytest.raises(ValueError, match="source_id"):
        GameResult(
            sport=Sport.NBA, game_date=date(2015, 1, 1), season=2015,
            home_team="A", away_team="B", home_score=1, away_score=0,
            source="test", source_id="",
        )


def test_training_dict_shape():
    d = game().to_training_dict()
    assert d["home"] == "Boston Celtics"
    assert d["home_score"] == 110


# ---------- GameStore ----------

@pytest.fixture
def store(tmp_path):
    return GameStore(tmp_path / "games.db")


def test_upsert_counts_only_new(store):
    assert store.upsert_many([game(1), game(2)]) == 2
    assert store.count(Sport.NBA) == 2


def test_reingesting_is_idempotent(store):
    """Re-correr una ingesta no debe duplicar. Sin esto, cada backfill repetido
    multiplica el peso de los mismos partidos en el entrenamiento."""
    games = [game(1), game(2), game(3)]
    assert store.upsert_many(games) == 3
    assert store.upsert_many(games) == 0
    assert store.count() == 3


def test_doubleheader_is_not_deduplicated(store):
    """Dos partidos, misma fecha, mismos equipos: un doubleheader real. La clave
    es source_id justamente para no perder el segundo."""
    g1 = game(1, hs=5, as_=3, source_id="20150101BOS1")
    g2 = game(1, hs=2, as_=7, source_id="20150101BOS2")
    assert store.upsert_many([g1, g2]) == 2


def test_same_source_id_across_sources_coexists(store):
    a = game(1, source="fuente_a", source_id="123")
    b = game(1, source="fuente_b", source_id="123")
    assert store.upsert_many([a, b]) == 2


def test_upsert_empty_is_noop(store):
    assert store.upsert_many([]) == 0


def test_iter_games_is_chronological(store):
    store.upsert_many([game(5), game(1), game(3)])
    dates = [g["game_date"] for g in store.iter_games(Sport.NBA)]
    assert dates == sorted(dates)


def test_iter_games_filters_by_date(store):
    store.upsert_many([game(1), game(10), game(20)])
    got = store.iter_games(Sport.NBA, start=date(2015, 1, 5), end=date(2015, 1, 15))
    assert len(got) == 1


def test_iter_games_filters_by_season(store):
    store.upsert_many([game(1, season=2014), game(2, season=2015)])
    assert len(store.iter_games(Sport.NBA, seasons=[2015])) == 1


def test_training_rows_flags_season_change(store):
    store.upsert_many([
        game(1, season=2014, source_id="a"),
        game(2, season=2014, source_id="b"),
        game(3, season=2015, source_id="c"),
    ])
    rows = store.training_rows(Sport.NBA)
    assert [r["new_season"] for r in rows] == [False, False, True]


def test_training_rows_exposes_goal_aliases_for_soccer(store):
    store.upsert_many([game(1, hs=3, as_=1)])
    row = store.training_rows(Sport.NBA)[0]
    assert row["home_goals"] == 3 and row["away_goals"] == 1


def test_resume_tracking(store):
    assert not store.season_is_done(Sport.NBA, "test", 2015)
    store.mark_season_done(Sport.NBA, "test", 2015, 1230)
    assert store.season_is_done(Sport.NBA, "test", 2015)


def test_summary_groups_by_season(store):
    store.upsert_many([game(1, season=2014), game(2, season=2015), game(3, season=2015)])
    rows = {r["season"]: r["n"] for r in store.summary()}
    assert rows == {2014: 1, 2015: 2}
