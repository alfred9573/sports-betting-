"""Parseo de cada fuente sobre payloads fijados. Sin red.

Los payloads reproducen el formato real de cada API/dataset. Su valor es fijar
el contrato: si la fuente cambia de formato, estos tests son lo que avisa.
"""

import pytest

from betbot.ingest.sources.espn import ESPNScoreboard
from betbot.ingest.sources.fivethirtyeight_nba import FiveThirtyEightNBA
from betbot.ingest.sources.mlb_statsapi import MLBStatsAPI
from betbot.ingest.sources.retrosheet import Retrosheet
from betbot.types import Sport

# ---------- FiveThirtyEight NBA ----------

FTE_ROWS = [
    # fila del local (la que se conserva)
    {"_iscopy": "0", "game_location": "H", "year_id": "2015", "game_id": "201501010BOS",
     "date_game": "2015-01-01", "fran_id": "Celtics", "team_id": "BOS", "pts": "110",
     "opp_fran": "Heat", "opp_id": "MIA", "opp_pts": "100", "is_playoffs": "0", "notes": ""},
    # la MISMA partida vista desde el visitante: debe descartarse
    {"_iscopy": "1", "game_location": "A", "year_id": "2015", "game_id": "201501010BOS",
     "date_game": "2015-01-01", "fran_id": "Heat", "team_id": "MIA", "pts": "100",
     "opp_fran": "Celtics", "opp_id": "BOS", "opp_pts": "110", "is_playoffs": "0", "notes": ""},
]


def test_fte_keeps_one_row_per_game():
    """El CSV trae cada partido dos veces. No filtrar duplica el dataset y
    dobla el peso de cada resultado en el Elo."""
    games = FiveThirtyEightNBA().parse(FTE_ROWS)
    assert len(games) == 1


def test_fte_orients_home_and_away_correctly():
    g = FiveThirtyEightNBA().parse(FTE_ROWS)[0]
    assert g.home_team == "Boston Celtics"
    assert g.away_team == "Miami Heat"
    assert (g.home_score, g.away_score) == (110, 100)
    assert g.home_won


def test_fte_season_filter():
    assert FiveThirtyEightNBA().parse(FTE_ROWS, seasons={2014}) == []
    assert len(FiveThirtyEightNBA().parse(FTE_ROWS, seasons={2015})) == 1


def test_fte_marks_playoffs():
    rows = [dict(FTE_ROWS[0], is_playoffs="1")]
    assert FiveThirtyEightNBA().parse(rows)[0].playoff


# ---------- Retrosheet ----------

def _gl_row(date="20230330", num="0", vis="MIL", home="CHN", vr="0", hr="4"):
    row = [""] * 161
    row[0], row[1], row[3], row[6] = date, num, vis, home
    row[9], row[10] = vr, hr
    row[102], row[104] = "Corbin Burnes", "Marcus Stroman"
    return ",".join(row)


def test_retrosheet_parses_basic_game():
    g = Retrosheet().parse(_gl_row(), 2023)[0]
    assert g.home_team == "Chicago Cubs"
    assert g.away_team == "Milwaukee Brewers"
    assert (g.home_score, g.away_score) == (4, 0)
    assert g.game_date.isoformat() == "2023-03-30"


def test_retrosheet_extracts_starting_pitchers():
    """Los offsets de abridor son 102/104. Estuvieron mal (103/106) y devolvian
    un bateador y un id crudo: exactamente el tipo de fallo que no rompe nada
    pero envenena el ajuste por abridor."""
    g = Retrosheet().parse(_gl_row(), 2023)[0]
    assert g.extra["home_sp"] == "Marcus Stroman"
    assert g.extra["away_sp"] == "Corbin Burnes"


def test_retrosheet_doubleheader_gets_distinct_ids():
    raw = _gl_row(num="1", hr="4") + "\n" + _gl_row(num="2", hr="7")
    games = Retrosheet().parse(raw, 2023)
    assert len(games) == 2
    assert games[0].source_id != games[1].source_id


def test_retrosheet_chicago_codes_are_distinct():
    cubs = Retrosheet().parse(_gl_row(home="CHN"), 2023)[0]
    sox = Retrosheet().parse(_gl_row(home="CHA"), 2023)[0]
    assert cubs.home_team == "Chicago Cubs"
    assert sox.home_team == "Chicago White Sox"


def test_retrosheet_athletics_new_code():
    g = Retrosheet().parse(_gl_row(home="ATH", vis="SEA"), 2025)[0]
    assert g.home_team == "Oakland Athletics"


def test_retrosheet_skips_short_rows():
    assert Retrosheet().parse("a,b,c", 2023) == []


def test_retrosheet_skips_unparseable_score():
    src = Retrosheet()
    row = _gl_row(hr="x")
    assert src.parse(row, 2023) == []
    assert src.skipped


# ---------- MLB StatsAPI ----------

STATSAPI = {
    "dates": [
        {
            "games": [
                {
                    "gamePk": 745001,
                    "officialDate": "2024-07-04",
                    "season": "2024",
                    "gameType": "R",
                    "status": {"detailedState": "Final"},
                    "teams": {
                        "home": {"team": {"name": "New York Yankees"}, "score": 6},
                        "away": {"team": {"name": "Boston Red Sox"}, "score": 3},
                    },
                },
                {   # en curso: no debe entrar
                    "gamePk": 745002,
                    "officialDate": "2024-07-04",
                    "status": {"detailedState": "In Progress"},
                    "teams": {
                        "home": {"team": {"name": "Chicago Cubs"}, "score": 1},
                        "away": {"team": {"name": "Chicago White Sox"}, "score": 0},
                    },
                },
            ]
        }
    ]
}


def test_statsapi_parses_final_game():
    games = MLBStatsAPI().parse(STATSAPI, 2024)
    assert len(games) == 1
    g = games[0]
    assert g.home_team == "New York Yankees"
    assert (g.home_score, g.away_score) == (6, 3)
    assert g.source_id == "745001"


def test_statsapi_excludes_unfinished_games():
    """Un partido en curso trae marcador PARCIAL. Meterlo al entrenamiento
    ensena resultados que no ocurrieron."""
    games = MLBStatsAPI().parse(STATSAPI, 2024)
    assert all(g.source_id != "745002" for g in games)


def test_statsapi_marks_playoff():
    payload = {"dates": [{"games": [dict(
        STATSAPI["dates"][0]["games"][0], gameType="D", gamePk=999)]}]}
    assert MLBStatsAPI().parse(payload, 2024)[0].playoff


def test_statsapi_handles_missing_fields():
    payload = {"dates": [{"games": [{"gamePk": 1, "status": {"detailedState": "Final"}}]}]}
    src = MLBStatsAPI()
    assert src.parse(payload, 2024) == []
    assert src.skipped


# ---------- ESPN ----------

ESPN_PAYLOAD = {
    "events": [
        {
            "id": "401585123",
            "date": "2025-01-10T00:30Z",
            "season": {"year": 2025},
            "competitions": [
                {
                    "neutralSite": False,
                    "status": {"type": {"completed": True}},
                    "competitors": [
                        {"homeAway": "home", "score": "112",
                         "team": {"displayName": "Boston Celtics"}},
                        {"homeAway": "away", "score": "104",
                         "team": {"displayName": "Miami Heat"}},
                    ],
                }
            ],
        },
        {
            "id": "401585124",
            "date": "2025-01-10T03:00Z",
            "competitions": [
                {
                    "status": {"type": {"completed": False}},
                    "competitors": [
                        {"homeAway": "home", "score": "50",
                         "team": {"displayName": "Los Angeles Lakers"}},
                        {"homeAway": "away", "score": "48",
                         "team": {"displayName": "Phoenix Suns"}},
                    ],
                }
            ],
        },
    ]
}


def test_espn_parses_completed_game():
    games = ESPNScoreboard(Sport.NBA).parse(ESPN_PAYLOAD)
    assert len(games) == 1
    g = games[0]
    assert g.home_team == "Boston Celtics"
    assert (g.home_score, g.away_score) == (112, 104)


def test_espn_excludes_in_progress():
    games = ESPNScoreboard(Sport.NBA).parse(ESPN_PAYLOAD)
    assert all(g.source_id != "401585124" for g in games)


def test_espn_rejects_unsupported_sport():
    with pytest.raises(ValueError, match="no soportado"):
        ESPNScoreboard("no-es-un-deporte")


def test_espn_handles_empty_payload():
    assert ESPNScoreboard(Sport.NBA).parse({}) == []


# ---------- hoopR NBA (temporadas recientes) ----------

def _hoopr_row(season="2026", stype="2", home="Boston Celtics", away="Miami Heat",
               hs="112", as_="104", date="2026-01-15T00:30Z", gid="401700001",
               completed="TRUE", neutral="false"):
    return {
        "id": gid, "season": season, "season_type": stype, "date": date,
        "status_type_completed": completed, "neutral_site": neutral,
        "home_display_name": home, "away_display_name": away,
        "home_score": hs, "away_score": as_,
    }


def test_hoopr_parses_completed_game():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    g = HoopRNBA().parse([_hoopr_row()])[0]
    assert g.home_team == "Boston Celtics"
    assert (g.home_score, g.away_score) == (112, 104)
    assert g.game_date.isoformat() == "2026-01-15"
    assert g.season == 2026


def test_hoopr_skips_unfinished_games():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    assert HoopRNBA().parse([_hoopr_row(completed="FALSE")]) == []


def test_hoopr_excludes_preseason():
    """season_type 1 es pretemporada: alineaciones irreales y esfuerzo nulo."""
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    assert HoopRNBA().parse([_hoopr_row(stype="1")]) == []


def test_hoopr_includes_playoffs_and_playin():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    assert HoopRNBA().parse([_hoopr_row(stype="3")])[0].playoff
    assert HoopRNBA().parse([_hoopr_row(stype="5")])[0].playoff


def test_hoopr_can_exclude_playoffs():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    assert HoopRNBA(include_playoffs=False).parse([_hoopr_row(stype="3")]) == []


def test_hoopr_excludes_allstar_games():
    """Los partidos del All-Star vienen con season_type=2, IGUAL que la
    temporada regular, asi que ese campo no los filtra. Lo que los excluye es
    que sus equipos no existen en el registro canonico. Son marcadores absurdos
    (211-186) que desplazarian los ratings de todos los participantes."""
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    src = HoopRNBA()
    rows = [
        _hoopr_row(home="Team Chuck", away="Team Shaq", hs="211", as_="186"),
        _hoopr_row(home="Western Conf All-Stars", away="Eastern Conf All-Stars",
                   gid="2"),
        _hoopr_row(home="World", away="USA", gid="3"),
    ]
    assert src.parse(rows) == []
    assert src.exhibition_skipped == 3
    # y NO deben ensuciar el aviso de cobertura real del registro
    assert src.skipped == []


def test_hoopr_real_unknown_team_is_reported_as_problem():
    """Un equipo de verdad que falte SI debe salir en `skipped`, para que el
    aviso de la ingesta señale problemas reales de cobertura."""
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    src = HoopRNBA()
    assert src.parse([_hoopr_row(home="Equipo Nuevo Inventado")]) == []
    assert src.skipped


def test_hoopr_marks_neutral_site():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    assert HoopRNBA().parse([_hoopr_row(neutral="true")])[0].neutral_site


def test_hoopr_season_filter():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    rows = [_hoopr_row(season="2024", gid="a"), _hoopr_row(season="2026", gid="b")]
    got = HoopRNBA().parse(rows, seasons={2026})
    assert len(got) == 1 and got[0].season == 2026


def test_hoopr_handles_float_scores():
    """El CSV puede traer los marcadores como '112.0'."""
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    g = HoopRNBA().parse([_hoopr_row(hs="112.0", as_="104.0")])[0]
    assert (g.home_score, g.away_score) == (112, 104)


def test_hoopr_bad_score_is_recorded():
    from betbot.ingest.sources.hoopr_nba import HoopRNBA

    src = HoopRNBA()
    assert src.parse([_hoopr_row(hs="")]) == []
    assert src.skipped


# ---------- openfootball (temporada en curso) ----------

# Centinela propio: hay que distinguir "no se paso marcador" de "score=None",
# que es como openfootball marca un partido aun no jugado.
_SIN_ESPECIFICAR = object()


def _of_match(date="2026-08-21", home="Arsenal FC", away="Coventry City FC",
              score=_SIN_ESPECIFICAR):
    if score is _SIN_ESPECIFICAR:
        score = {"ht": [2, 0], "ft": [3, 0]}
    return {"round": "Matchday 1", "date": date, "time": "20:00",
            "team1": home, "team2": away, "score": score}


def _of_parse(*matches, season=2026):
    from betbot.ingest.sources.openfootball_json import OpenFootballJSON

    src = OpenFootballJSON(Sport.SOCCER_EPL)
    return src, src.parse({"matches": list(matches)}, season)


def test_openfootball_team1_is_home():
    """No esta etiquetado en el JSON: es una convencion. Invertirla no rompe
    nada visiblemente, solo hace que el modelo aprenda la ventaja de local al
    reves."""
    _, games = _of_parse(_of_match())
    assert games[0].home_team == "Arsenal"
    assert games[0].away_team == "Coventry City"
    assert (games[0].home_score, games[0].away_score) == (3, 0)


def test_openfootball_handles_dict_score():
    _, games = _of_parse(_of_match(score={"ht": [1, 1], "ft": [2, 1]}))
    assert (games[0].home_score, games[0].away_score) == (2, 1)


def test_openfootball_handles_list_score():
    """Forma corta, sin datos de descanso. Conviven las dos en el mismo fichero:
    en 2025-26 hay 353 del primer tipo y 27 del segundo. Asumir solo una hace
    que el parseo reviente a mitad de temporada."""
    _, games = _of_parse(_of_match(score=[0, 0]))
    assert (games[0].home_score, games[0].away_score) == (0, 0)
    assert games[0].is_draw


def test_openfootball_skips_unplayed_matches():
    """En una temporada en curso la mayoria de partidos aun no se jugaron."""
    src, games = _of_parse(_of_match(score=None), _of_match(score={"ht": [0, 0]}))
    assert games == []
    assert src.unplayed == 2
    assert src.skipped == []   # no jugado != dato roto


def test_openfootball_rejects_unsupported_league():
    from betbot.ingest.sources.openfootball_json import OpenFootballJSON

    with pytest.raises(ValueError, match="no cubierta"):
        OpenFootballJSON(Sport.SOCCER_LIGA_MX)


def test_openfootball_season_label():
    from betbot.ingest.sources.openfootball_json import OpenFootballJSON

    assert OpenFootballJSON.season_label(2026) == "2026-27"


def test_openfootball_source_name_includes_league():
    """Dos ligas distintas no deben compartir `source`, o la deduplicacion las
    mezclaria."""
    from betbot.ingest.sources.openfootball_json import OpenFootballJSON

    assert OpenFootballJSON(Sport.SOCCER_EPL).name != \
           OpenFootballJSON(Sport.SOCCER_LA_LIGA).name
