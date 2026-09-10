"""Tests de la fuente Liga MX y de los presets por liga."""

import pytest

from betbot.ingest.sources.ligamx_csv import LigaMX
from betbot.ingest.teams import TeamRegistry
from betbot.models.soccer import LEAGUE_PRESETS, PoissonSoccerModel
from betbot.types import Sport

HEADER = "Stage,Round,Date,Time,Timezone,Team 1,FT,HT,Team 2,UTC"


def csv_row(stage="Apertura", rnd="1", date="Fri Jul 5 2024", home="Gallos Blancos",
            ft="1-2", away="Club Tijuana", utc="2024-07-06T01:00Z"):
    return f"{stage},{rnd},{date},19:00,CST/-0600,{home},{ft},0-1,{away},{utc}"


def parse(*rows, season=2024, **kw):
    return LigaMX(**kw).parse("\n".join([HEADER, *rows]), season)


# ---------- registro de equipos ----------

@pytest.fixture
def mx():
    return TeamRegistry(Sport.SOCCER_LIGA_MX)


@pytest.mark.parametrize("alias,expected", [
    ("Gallos Blancos", "Queretaro"),
    ("Querétaro", "Queretaro"),
    ("CF América", "Club America"),
    ("Deportivo Guadalajara", "Guadalajara Chivas"),
    ("Chivas", "Guadalajara Chivas"),
    ("UANL Tigres", "Tigres UANL"),
    ("Club León", "Leon"),
    ("Atlético San Luis", "Atletico San Luis"),
    ("FC Juárez", "FC Juarez"),
    ("Xolos", "Club Tijuana"),
])
def test_ligamx_aliases(mx, alias, expected):
    assert mx.resolve(alias) == expected


def test_accents_do_not_split_a_team(mx):
    """'Querétaro' y 'Queretaro' deben ser el mismo equipo: si una fuente pone
    acentos y otra no, se entrenarian dos equipos con media historia cada uno."""
    assert mx.resolve("Querétaro") == mx.resolve("Queretaro")
    assert mx.resolve("CF América") == mx.resolve("CF America")


def test_morelia_maps_to_mazatlan(mx):
    """Decision documentada: la franquicia se traslado en 2020. Nunca
    coexistieron, asi que unificarlas no crea colision."""
    assert mx.resolve("Monarcas Morelia") == "Mazatlan FC"
    assert mx.resolve("Mazatlán FC") == "Mazatlan FC"


def test_defunct_teams_keep_identity(mx):
    assert mx.resolve("CD Veracruz") == "Veracruz"
    assert mx.resolve("Lobos BUAP") == "Lobos BUAP"


# ---------- parseo ----------

def test_parses_basic_match():
    g = parse(csv_row())[0]
    assert g.home_team == "Queretaro"
    assert g.away_team == "Club Tijuana"
    assert (g.home_score, g.away_score) == (1, 2)
    assert g.game_date.isoformat() == "2024-07-06"


def test_cancelled_covid_matches_are_dropped():
    """El Clausura 2020 cancelado trae '(*)' en vez de marcador. Parsearlo como
    0-0 meteria 144 empates falsos en un dataset de 2.000 partidos."""
    assert parse(csv_row(ft="(*)")) == []


def test_empty_score_is_dropped():
    assert parse(csv_row(ft="")) == []
    assert parse(csv_row(ft="   ")) == []


def test_liguilla_is_marked_as_playoff():
    g = parse(csv_row(stage="Apertura - Liguilla"))[0]
    assert g.playoff
    assert not parse(csv_row(stage="Apertura"))[0].playoff


def test_can_exclude_liguilla():
    assert parse(csv_row(stage="Clausura - Liguilla"), include_playoffs=False) == []


def test_stage_is_preserved():
    """Apertura y Clausura son torneos distintos; conviene poder separarlos."""
    assert parse(csv_row(stage="Clausura"))[0].extra["stage"] == "Clausura"


def test_falls_back_to_date_when_utc_missing():
    """63 filas del dataset no traen UTC."""
    g = parse(csv_row(utc=""))[0]
    assert g.game_date.isoformat() == "2024-07-05"


def test_score_with_annotation_is_parsed():
    g = parse(csv_row(ft="1-1 pen."))[0]
    assert (g.home_score, g.away_score) == (1, 1)


def test_unknown_team_is_skipped_and_recorded():
    src = LigaMX()
    out = src.parse("\n".join([HEADER, csv_row(home="Equipo Inventado")]), 2024)
    assert out == []
    assert src.skipped


def test_season_label_format():
    assert LigaMX().season_label(2018) == "2018-19"
    assert LigaMX().season_label(2024) == "2024-25"


def test_unavailable_season_returns_empty():
    assert LigaMX().fetch_season(1990) == []


def test_source_id_distinguishes_matches():
    a = parse(csv_row(rnd="1"))[0]
    b = parse(csv_row(rnd="2"))[0]
    assert a.source_id != b.source_id


# ---------- presets por liga ----------

def test_ligamx_preset_differs_from_epl():
    """Cada liga tiene sus parametros propios.

    Nota historica: este test comprobaba que Mexico tenia MENOS ventaja de local
    que Inglaterra, y era cierto frente a la calibracion inglesa de 1995-2016
    (1.44). Al recalibrar la Premier con datos de 2016-2026 las dos convergen en
    1.28: el futbol ingles moderno perdio ventaja de local hasta igualar a la
    Liga MX. Lo que sigue distinguiendolas es la tendencia al empate."""
    epl = PoissonSoccerModel.for_league(Sport.SOCCER_EPL)
    mx = PoissonSoccerModel.for_league(Sport.SOCCER_LIGA_MX)
    assert (mx.home_advantage, mx.rho) != (epl.home_advantage, epl.rho)
    assert mx.rho != epl.rho


def test_preset_values_are_the_measured_ones():
    mx = PoissonSoccerModel.for_league(Sport.SOCCER_LIGA_MX)
    assert (mx.home_advantage, mx.rho, mx.decay) == LEAGUE_PRESETS[Sport.SOCCER_LIGA_MX]


def test_uncalibrated_league_falls_back_with_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        m = PoissonSoccerModel.for_league(Sport.SOCCER_LA_LIGA)
    assert "sin preset calibrado" in caplog.text
    assert m.home_advantage == LEAGUE_PRESETS[Sport.SOCCER_EPL][0]


def test_for_league_accepts_overrides():
    m = PoissonSoccerModel.for_league(Sport.SOCCER_LIGA_MX, min_matches=3)
    assert m.min_matches == 3
