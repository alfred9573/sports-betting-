import pytest

from betbot.ingest.teams import TeamRegistry, UnknownTeamError, canonical
from betbot.types import Sport


@pytest.fixture
def nba():
    return TeamRegistry(Sport.NBA)


@pytest.fixture
def mlb():
    return TeamRegistry(Sport.MLB)


@pytest.fixture
def epl():
    return TeamRegistry(Sport.SOCCER_EPL)


def test_canonical_name_maps_to_itself(nba):
    assert nba.resolve("Boston Celtics") == "Boston Celtics"


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("LA Clippers", "Los Angeles Clippers"),
        ("LAC", "Los Angeles Clippers"),
        ("Clippers", "Los Angeles Clippers"),
        ("GSW", "Golden State Warriors"),
        ("Sixers", "Philadelphia 76ers"),
        ("Trailblazers", "Portland Trail Blazers"),
    ],
)
def test_nba_aliases(nba, alias, expected):
    assert nba.resolve(alias) == expected


def test_relocated_franchises_map_to_current_name(nba):
    """Los datasets largos traen nombres historicos. Sin esto, media franquicia
    entra al modelo como un equipo distinto con 0 partidos."""
    assert nba.resolve("Seattle SuperSonics") == "Oklahoma City Thunder"
    assert nba.resolve("New Jersey Nets") == "Brooklyn Nets"
    assert nba.resolve("Vancouver Grizzlies") == "Memphis Grizzlies"
    assert nba.resolve("Washington Bullets") == "Washington Wizards"


def test_case_and_punctuation_insensitive(nba):
    for variant in ("boston celtics", "BOSTON CELTICS", "Boston  Celtics"):
        assert nba.resolve(variant) == "Boston Celtics"


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("NYA", "New York Yankees"),
        ("NYN", "New York Mets"),
        ("CHN", "Chicago Cubs"),
        ("CHA", "Chicago White Sox"),
        ("ATH", "Oakland Athletics"),
        ("Cleveland Indians", "Cleveland Guardians"),
        ("Tampa Bay Devil Rays", "Tampa Bay Rays"),
        ("Florida Marlins", "Miami Marlins"),
        ("Montreal Expos", "Washington Nationals"),
    ],
)
def test_mlb_aliases(mlb, alias, expected):
    assert mlb.resolve(alias) == expected


def test_mlb_chicago_codes_are_not_confused(mlb):
    """CHN y CHA son equipos DISTINTOS de la misma ciudad. Confundirlos mezcla
    dos rosters en un rating."""
    assert mlb.resolve("CHN") != mlb.resolve("CHA")


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("Man City", "Manchester City"),
        ("Man Utd", "Manchester United"),
        ("Nott'm Forest", "Nottingham Forest"),
        ("Spurs", "Tottenham Hotspur"),
        ("Wolves", "Wolverhampton Wanderers"),
        ("Brighton", "Brighton and Hove Albion"),
    ],
)
def test_epl_aliases(epl, alias, expected):
    assert epl.resolve(alias) == expected


def test_club_suffix_is_stripped(epl):
    assert epl.resolve("Liverpool FC") == "Liverpool"
    assert epl.resolve("Arsenal FC") == "Arsenal"


def test_accents_are_normalized():
    r = TeamRegistry(Sport.SOCCER_EPL, strict=False)
    r.add_alias("Atlético Test", "Arsenal")
    assert r.resolve("Atletico Test") == "Arsenal"


def test_strict_mode_raises_on_unknown(nba):
    with pytest.raises(UnknownTeamError, match="no reconocido"):
        nba.resolve("Equipo Inexistente")


def test_lenient_mode_returns_none_and_records():
    r = TeamRegistry(Sport.NBA, strict=False)
    assert r.resolve("Equipo Inexistente") is None
    assert "Equipo Inexistente" in r.unresolved


def test_empty_name_is_rejected(nba):
    with pytest.raises(UnknownTeamError):
        nba.resolve("")


def test_add_alias_requires_known_canonical(nba):
    with pytest.raises(UnknownTeamError):
        nba.add_alias("X", "Equipo Que No Existe")


def test_add_alias_works(nba):
    nba.add_alias("Los Verdes", "Boston Celtics")
    assert nba.resolve("Los Verdes") == "Boston Celtics"


def test_ambiguous_alias_is_rejected():
    """Un alias que apunta a dos equipos es un bug de datos silencioso: debe
    detonar al construir el registro, no en produccion."""
    from betbot.ingest import teams

    r = TeamRegistry(Sport.NBA, strict=False)
    with pytest.raises(ValueError, match="ambiguo"):
        r._register("Boston Celtics", "Miami Heat")
    assert teams  # el modulo se importa sin efectos raros


def test_all_leagues_have_expected_team_count(nba, mlb):
    assert len(nba.canonical_names) == 30
    assert len(mlb.canonical_names) == 30


def test_canonical_shortcut():
    assert canonical(Sport.NBA, "LAL") == "Los Angeles Lakers"
    assert canonical(Sport.NBA, "no existe") is None
