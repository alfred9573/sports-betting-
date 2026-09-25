"""Persistencia de partidos de jugador de NBA.

Se guardan TAMBIEN las filas de quien no jugo (minutos = 0). El modelo de props
no las usa para proyectar —la apuesta se anula si el jugador no juega—, pero
son la unica forma de estimar algun dia la probabilidad de que juegue, y
tirarlas ahora seria perder un dato que NFL ni siquiera tiene.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from betbot.ingest.sources.nba_player_box import STATS_NBA, NBAPlayerGame

_COLS = ",\n    ".join(f"{c} REAL NOT NULL DEFAULT 0" for c in STATS_NBA)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS nba_player_games (
    athlete_id  TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position    TEXT NOT NULL,
    season      INTEGER NOT NULL,
    season_type INTEGER NOT NULL,
    game_date   TEXT NOT NULL,
    team        TEXT NOT NULL,
    opponent    TEXT NOT NULL,
    minutes     REAL NOT NULL,
    starter     INTEGER NOT NULL,
    {_COLS},
    PRIMARY KEY (athlete_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_nba_orden   ON nba_player_games (game_date);
CREATE INDEX IF NOT EXISTS idx_nba_jugador ON nba_player_games (athlete_id, game_date);
CREATE INDEX IF NOT EXISTS idx_nba_nombre  ON nba_player_games (player_name);
"""


class NBAPlayerStore:
    def __init__(self, path: str | Path = "data/nba_players.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_many(self, filas: list[NBAPlayerGame]) -> int:
        if not filas:
            return 0
        campos = [
            "athlete_id", "game_id", "player_name", "position", "season",
            "season_type", "game_date", "team", "opponent", "minutes", "starter",
            *STATS_NBA,
        ]
        with self._conn() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO nba_player_games ({','.join(campos)}) "
                f"VALUES ({','.join('?' * len(campos))})",
                [
                    (
                        f.athlete_id, f.game_id, f.player_name, f.position, f.season,
                        f.season_type, f.game_date, f.team, f.opponent, f.minutes,
                        int(f.starter), *[f.stats[s] for s in STATS_NBA],
                    )
                    for f in filas
                ],
            )
        return len(filas)

    def filas_para_modelo(self, season_type: int = 2) -> list[dict]:
        """Partidos JUGADOS (minutos > 0), en orden cronologico estricto.

        Mismo formato que consume `walk_forward_props`: `player_id`, `position`,
        `season` y una columna por estadistica.
        """
        with self._conn() as c:
            return [
                dict(r) for r in c.execute(
                    "SELECT athlete_id AS player_id, position, season, game_date, "
                    f"minutes, {','.join(STATS_NBA)} FROM nba_player_games "
                    "WHERE season_type = ? AND minutes > 0 "
                    "ORDER BY game_date, game_id, athlete_id",
                    (season_type,),
                )
            ]

    def resumen(self) -> dict:
        with self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS filas, COUNT(DISTINCT athlete_id) AS jugadores, "
                "SUM(CASE WHEN minutes > 0 THEN 1 ELSE 0 END) AS jugados, "
                "MIN(season) AS desde, MAX(season) AS hasta, MAX(game_date) AS ultimo "
                "FROM nba_player_games"
            ).fetchone()
        return dict(r)
