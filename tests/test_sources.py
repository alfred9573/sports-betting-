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
