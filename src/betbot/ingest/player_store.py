"""Persistencia de lineas semanales de jugador.

Separada de `GameStore` a proposito: un partido y la linea de un jugador en ese
partido son granularidades distintas, y mezclarlas obligaria a que cada consulta
de modelo de equipo filtrara filas de jugador. La clave unica
(player_id, season, week, season_type) hace la reingesta idempotente, que es lo
que permite refrescar la temporada en curso cada semana sin duplicar nada.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from betbot.ingest.sources.nfl_player_stats import STATS, PlayerWeek

_COLS_STATS = ",\n    ".join(f"{c} REAL NOT NULL DEFAULT 0" for c in STATS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS player_weeks (
    player_id   TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position    TEXT NOT NULL,
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,
    season_type TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    team        TEXT NOT NULL,
    opponent    TEXT NOT NULL,
    {_COLS_STATS},
    PRIMARY KEY (player_id, season, week, season_type)
);
CREATE INDEX IF NOT EXISTS idx_pw_jugador ON player_weeks (player_id, season, week);
CREATE INDEX IF NOT EXISTS idx_pw_orden   ON player_weeks (season, week);
CREATE INDEX IF NOT EXISTS idx_pw_nombre  ON player_weeks (player_name);
"""


class PlayerStore:
    def __init__(self, path: str | Path = "data/players.db") -> None:
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

    def upsert_many(self, filas: list[PlayerWeek]) -> int:
        """Inserta o reemplaza. Devuelve cuantas filas se escribieron."""
        if not filas:
            return 0
        campos = [
            "player_id", "player_name", "position", "season", "week",
            "season_type", "game_id", "team", "opponent", *STATS,
        ]
        marcas = ",".join("?" * len(campos))
        with self._conn() as c:
            c.executemany(
                f"INSERT OR REPLACE INTO player_weeks ({','.join(campos)}) "
                f"VALUES ({marcas})",
                [
                    (
                        f.player_id, f.player_name, f.position, f.season, f.week,
                        f.season_type, f.game_id, f.team, f.opponent,
                        *[f.stats.get(s, 0.0) for s in STATS],
                    )
                    for f in filas
                ],
            )
        return len(filas)

    def historial(
        self, player_id: str, hasta: tuple[int, int] | None = None,
        season_type: str = "REG",
    ) -> list[sqlite3.Row]:
        """Partidos de un jugador en orden cronologico.

        `hasta` es (season, week) EXCLUSIVO. Existe por una razon concreta: el
        backtest debe poder pedir "lo que se sabia antes de este partido" sin
        posibilidad de colar el propio partido en la entrada del modelo. Dejar
        que el llamante filtre a posteriori es como se cuelan las fugas.
        """
        sql = (
            "SELECT * FROM player_weeks WHERE player_id=? AND season_type=?"
        )
        params: list = [player_id, season_type]
        if hasta is not None:
            sql += " AND (season < ? OR (season = ? AND week < ?))"
            params += [hasta[0], hasta[0], hasta[1]]
        sql += " ORDER BY season, week"
        with self._conn() as c:
            return list(c.execute(sql, params))

    def semana(self, season: int, week: int, season_type: str = "REG") -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(
                "SELECT * FROM player_weeks WHERE season=? AND week=? AND season_type=? "
                "ORDER BY player_name",
                (season, week, season_type),
            ))

    def buscar(self, nombre: str) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(
                "SELECT player_id, player_name, position, MIN(season) AS desde, "
                "MAX(season) AS hasta, COUNT(*) AS partidos "
                "FROM player_weeks WHERE player_name LIKE ? "
                "GROUP BY player_id ORDER BY hasta DESC, partidos DESC",
                (f"%{nombre}%",),
            ))

    def resumen(self) -> dict:
        with self._conn() as c:
            r = c.execute(
                "SELECT COUNT(*) AS filas, COUNT(DISTINCT player_id) AS jugadores, "
                "MIN(season) AS desde, MAX(season) AS hasta FROM player_weeks"
            ).fetchone()
            ultima = c.execute(
                "SELECT season, MAX(week) AS week FROM player_weeks "
                "WHERE season = (SELECT MAX(season) FROM player_weeks) GROUP BY season"
            ).fetchone()
        return {
            "filas": r["filas"], "jugadores": r["jugadores"],
            "desde": r["desde"], "hasta": r["hasta"],
            "ultima_semana": (ultima["week"] if ultima else None),
        }
