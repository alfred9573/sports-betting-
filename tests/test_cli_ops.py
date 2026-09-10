"""Tests de las piezas operativas del CLI: modelo entrenado y frescura."""

from datetime import UTC, date, timedelta

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


# ---------- seleccion de fuente ----------

def test_source_flag_is_honored():
    """El bug que esto previene: --source se ignoraba, asi que --source espn no
    hacia nada y NBA siempre usaba el dataset que termina en 2015."""
    from betbot.cli import _make_source

    assert _make_source(Sport.NBA).name == "fivethirtyeight_nba"
    assert _make_source(Sport.NBA, "espn").name == "espn"
    assert _make_source(Sport.MLB, "espn").name == "espn"


def test_espn_source_rejects_unsupported_sport():
    from betbot.cli import _make_source

    class Fake:
        value = "curling"

    assert _make_source(Fake(), "espn") is None


# ---------- ventanas de temporada de ESPN ----------

@pytest.mark.parametrize("sport,expected_start", [
    (Sport.NBA, (10, 1)),
    (Sport.NFL, (9, 1)),
    (Sport.MLB, (3, 1)),
    (Sport.SOCCER_LIGA_MX, (7, 1)),
])
def test_season_window_start(sport, expected_start):
    from betbot.ingest.sources.espn import ESPNScoreboard

    start, _ = ESPNScoreboard(sport).season_window(2020)
    assert (start.month, start.day) == expected_start
    assert start.year == 2020


def test_season_crossing_year_ends_next_year():
    """La 2024 de NBA acaba en junio de 2025, no de 2024."""
    from betbot.ingest.sources.espn import ESPNScoreboard

    _, end = ESPNScoreboard(Sport.NBA).season_window(2020)
    assert end.year == 2021 and end.month == 6


def test_mlb_season_stays_in_same_year():
    from betbot.ingest.sources.espn import ESPNScoreboard

    start, end = ESPNScoreboard(Sport.MLB).season_window(2020)
    assert start.year == end.year == 2020


def test_season_window_never_asks_for_future_dates():
    """Pedir fechas futuras gasta llamadas de API y no devuelve nada."""
    from betbot.ingest.sources.espn import ESPNScoreboard

    _, end = ESPNScoreboard(Sport.NBA).season_window(date.today().year)
    assert end <= date.today()


def test_future_season_returns_empty():
    from betbot.ingest.sources.espn import ESPNScoreboard

    assert ESPNScoreboard(Sport.NBA).fetch_season(date.today().year + 5) == []


# ---------- deteccion de temporada ----------

def _in_season_at(sport, year, month, day):
    """Reimplementa la ventana para poder probar fechas concretas sin parchear
    el reloj del sistema."""
    from betbot.ingest.sources.espn import _SEASON_WINDOWS

    (sm, sd), (em, ed), crosses = _SEASON_WINDOWS[sport]
    today = date(year, month, day)
    start = date(year, sm, sd)
    if crosses:
        return today >= start or today <= date(year, em, ed)
    return start <= today <= date(year, em, ed)


def test_nba_january_is_in_season():
    assert _in_season_at(Sport.NBA, 2026, 1, 15)


def test_nba_august_is_off_season():
    """El caso que motivo esto: en verano el ultimo partido tiene ~87 dias y no
    falta ningun dato. Avisar de 'desactualizado' durante meses hace que el
    aviso se ignore justo cuando importa."""
    assert not _in_season_at(Sport.NBA, 2026, 8, 15)


def test_mlb_july_is_in_season():
    assert _in_season_at(Sport.MLB, 2026, 7, 4)


def test_mlb_january_is_off_season():
    assert not _in_season_at(Sport.MLB, 2026, 1, 15)


def test_unknown_sport_defaults_to_in_season():
    """Ante la duda, avisar: es preferible un aviso de mas que datos viejos
    usados en silencio."""
    from betbot.cli import in_season

    class Fake:
        value = "curling"
        name = "CURLING"

    assert in_season(Fake())


# ---------- regresion de entretemporada ----------

def test_no_regression_within_the_same_season():
    """En pretemporada (junio-septiembre en NBA) no ha arrancado nada nuevo:
    los ratings de junio siguen siendo los buenos."""
    from betbot.cli import seasons_started_since

    assert seasons_started_since(Sport.NBA, "2026-06-14") == 0


def test_counts_season_starts_not_season_numbers():
    """Se cuenta por ARRANQUES de temporada porque cada fuente usa su propia
    convencion: hoopR y 538 etiquetan la NBA por el ano de fin, nflverse la NFL
    por el de inicio, y en MLB coincide con el ano natural."""
    from betbot.cli import seasons_started_since

    assert seasons_started_since(Sport.NBA, "2025-06-14") >= 1
    assert seasons_started_since(Sport.NBA, "2024-06-14") >= 2


def test_malformed_date_does_not_crash():
    from betbot.cli import seasons_started_since

    assert seasons_started_since(Sport.NBA, "no-es-fecha") == 0
    assert seasons_started_since(Sport.NBA, "") == 0


def test_regression_is_applied_when_loading_a_stale_model(db):
    """EL BUG QUE ESTO PREVIENE: fit() solo regresa a la media cuando ve el
    cambio de temporada DENTRO de los datos. Si la ultima temporada ingerida ya
    termino, los ratings se quedaban como en la final — sin regresar pese a que
    las plantillas cambiaron. Medido sobre datos reales: 68 puntos de Elo en el
    lider, casi 10 puntos porcentuales de probabilidad."""
    store = GameStore(db)
    base = date(2024, 11, 1)
    # Un equipo domina, para que su rating se despegue de 1500
    store.upsert_many([
        game(base + timedelta(days=i), home="Boston Celtics", away="Miami Heat",
             hs=120, as_=100, season=2025)
        for i in range(30)
    ])

    model, err = load_trained_model(Sport.NBA, db)
    assert err is None
    con_regresion = model.ratings.rating("Boston Celtics")

    from betbot.models.nba import NBAModel

    sin_regresion = NBAModel().fit(store.training_rows(Sport.NBA))
    assert con_regresion < sin_regresion.ratings.rating("Boston Celtics")
    # y debe seguir por encima de la media: regresa, no borra
    assert con_regresion > 1500


def test_regression_is_capped(db):
    """Con datos muy viejos no se regresa indefinidamente: a partir de cierto
    punto el modelo no sirve y de eso ya avisa `doctor` marcandolo OBSOLETO."""
    from betbot.cli import seasons_started_since

    store = GameStore(db)
    store.upsert_many([
        game(date(2010, 11, 1) + timedelta(days=i), hs=120, as_=100, season=2011)
        for i in range(30)
    ])
    assert seasons_started_since(Sport.NBA, "2011-06-14") > 3
    model, _ = load_trained_model(Sport.NBA, db)
    # tres regresiones del 25% dejan el rating por encima de 1500, no en 1500
    assert model.ratings.rating("Boston Celtics") > 1500


# ---------- barrera de cobertura de temporada ----------

def test_blocks_games_from_an_untrained_season():
    """LA BARRERA MAS IMPORTANTE. Un Elo entrenado hasta junio no sabe nada del
    verano: draft, traspasos, fichajes, lesiones. El mercado si, y ya lo ha
    puesto en el precio. El modelo interpreta esa diferencia como VALOR.

    Caso real observado: 16 senales con EV de hasta +79,8%, todas con el modelo
    mas confiado que el mercado. No se parece a un fallo — se parece exactamente
    a lo que uno querria ver si el bot funcionara."""
    from betbot.cli import coverage_check

    ok, motivo = coverage_check(Sport.NBA, "2026-06-14", date(2026, 10, 25))
    assert not ok
    assert "POSTERIOR" in motivo


def test_allows_games_within_the_trained_season():
    from betbot.cli import coverage_check

    ok, _ = coverage_check(Sport.NBA, "2026-03-01", date(2026, 4, 10))
    assert ok


def test_blocks_when_there_is_no_training_data():
    from betbot.cli import coverage_check

    ok, motivo = coverage_check(Sport.NBA, "", date(2026, 4, 10))
    assert not ok
    assert "datos de entrenamiento" in motivo


def test_accepts_datetime_and_string_dates():
    from datetime import datetime

    from betbot.cli import coverage_check

    dt = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)
    assert not coverage_check(Sport.NBA, "2026-06-14", dt)[0]
    assert not coverage_check(Sport.NBA, "2026-06-14", "2026-10-25T23:00:00Z")[0]


def test_malformed_event_date_does_not_block():
    """Ante una fecha ilegible se deja pasar: la barrera esta para el caso
    conocido, no para bloquear por ruido de parseo."""
    from betbot.cli import coverage_check

    assert coverage_check(Sport.NBA, "2026-06-14", "fecha-rara")[0]


def test_mlb_boundary_is_march_not_january():
    """Cada deporte tiene su arranque: en MLB, enero y marzo del mismo ano estan
    a distinto lado de la frontera."""
    from betbot.cli import coverage_check

    assert coverage_check(Sport.MLB, "2025-09-28", date(2026, 1, 15))[0]
    assert not coverage_check(Sport.MLB, "2025-09-28", date(2026, 4, 15))[0]


# ---------- reserva de cuota ----------

def test_quota_reserve_default():
    """La reserva protege la captura de cierres frente al escaneo. Una senal
    perdida cuesta una oportunidad; un cierre perdido cuesta poder EVALUAR la
    apuesta, y sin CLV no hay forma de saber si el bot tiene ventaja antes de
    que pasen varias temporadas."""
    from betbot.config import Settings

    assert Settings().quota_reserve == 60


def test_quota_reserve_from_env(tmp_path, monkeypatch):
    from betbot.config import Settings

    monkeypatch.setenv("QUOTA_RESERVE", "120")
    assert Settings.from_env(tmp_path / "no-existe.env").quota_reserve == 120


def test_scan_aborts_below_reserve(tmp_path, monkeypatch, capsys):
    """Con la cuota por debajo de la reserva, `scan` se abstiene sin error."""
    import argparse

    from betbot import cli

    monkeypatch.setenv("ODDS_API_KEY", "fake")
    monkeypatch.setenv("QUOTA_RESERVE", "60")

    class FakeProvider:
        credits_remaining = 25

        def __init__(self, *a, **kw):
            pass

        def fetch_events(self, sport, markets):
            return []

    import betbot.odds.the_odds_api as api

    monkeypatch.setattr(api, "TheOddsAPI", FakeProvider)

    args = argparse.Namespace(
        sport="nba", games_db=str(tmp_path / "g.db"), force=False, strategy="model",
    )
    code = cli.cmd_scan(args)
    salida = capsys.readouterr().out
    assert code == 0
    assert "reserva" in salida
    assert "close" in salida
