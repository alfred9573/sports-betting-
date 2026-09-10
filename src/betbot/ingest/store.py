"""Almacen SQLite de resultados historicos.

Requisitos que guian el diseno:
  - IDEMPOTENTE: re-ingerir una temporada no duplica ni corrompe nada. La clave
    es (source, source_id), no el par de equipos: dos partidos del mismo
    doubleheader tienen los mismos equipos y la misma fecha.
  - REANUDABLE: un backfill que se corta a mitad se retoma sin repetir trabajo.
  - ORDENADO: los modelos se entrenan en orden cronologico estricto. Un backtest
    que entrene con partidos posteriores al que predice esta contaminado y dara
    numeros preciosos que no existen. `iter_games()` garantiza el orden.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from betbot.ingest.types import GameResult
from betbot.types import Sport

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sport         TEXT NOT NULL,
    game_date     TEXT NOT NULL,
    season        INTEGER NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_score    INTEGER NOT NULL,
    away_score    INTEGER NOT NULL,
    neutral_site  INTEGER NOT NULL DEFAULT 0,
    playoff       INTEGER NOT NULL DEFAULT 0,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    ingested_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_games_sport_date ON games (sport, game_date);
CREATE INDEX IF NOT EXISTS idx_games_season ON games (sport, season);

CREATE TABLE IF NOT EXISTS ingest_log (
    sport       TEXT NOT NULL,
    source      TEXT NOT NULL,
    season      INTEGER NOT NULL,
    n_games     INTEGER NOT NULL,
    completed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (sport, source, season)
);
"""


class GameStore:
    def __init__(self, path: str | Path = "data/games.db") -> None:
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

    def upsert_many(self, games: list[GameResult]) -> int:
        """Inserta partidos nuevos. Devuelve cuantos eran realmente nuevos."""
        if not games:
            return 0
        rows = [
            (
                g.sport.value, g.game_date.isoformat(), g.season,
                g.home_team, g.away_team, g.home_score, g.away_score,
                int(g.neutral_site), int(g.playoff), g.source, g.source_id,
            )
            for g in games
        ]
        with self._conn() as c:
            before = c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            c.executemany(
                """INSERT OR IGNORE INTO games (
                    sport, game_date, season, home_team, away_team,
                    home_score, away_score, neutral_site, playoff, source, source_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            after = c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            return after - before

    def mark_season_done(self, sport: Sport, source: str, season: int, n_games: int) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ingest_log (sport, source, season, n_games) "
                "VALUES (?,?,?,?)",
                (sport.value, source, season, n_games),
            )

    def season_is_done(self, sport: Sport, source: str, season: int) -> bool:
        """Permite reanudar un backfill largo sin repetir temporadas."""
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM ingest_log WHERE sport=? AND source=? AND season=?",
                (sport.value, source, season),
            ).fetchone()
            return row is not None

    def iter_games(
        self,
        sport: Sport,
        start: date | None = None,
        end: date | None = None,
        seasons: list[int] | None = None,
    ) -> list[dict]:
        """Partidos en ORDEN CRONOLOGICO ESTRICTO.

        El orden secundario por id mantiene el resultado determinista dentro de
        una misma fecha, para que un backtest sea reproducible.
        """
        sql = "SELECT * FROM games WHERE sport=?"
        params: list = [sport.value]
        if start:
            sql += " AND game_date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND game_date <= ?"
            params.append(end.isoformat())
        if seasons:
            sql += f" AND season IN ({','.join('?' * len(seasons))})"
            params.extend(seasons)
        sql += " ORDER BY game_date ASC, id ASC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def training_rows(self, sport: Sport, **kwargs) -> list[dict]:
        """Partidos en el formato que consumen los `fit()` de los modelos,
        con la bandera `new_season` ya calculada en los cambios de temporada."""
        games = self.iter_games(sport, **kwargs)
        out = []
        prev_season = None
        for g in games:
            out.append(
                {
                    "home": g["home_team"],
                    "away": g["away_team"],
                    "home_score": g["home_score"],
                    "away_score": g["away_score"],
                    "home_goals": g["home_score"],   # alias para el modelo de futbol
                    "away_goals": g["away_score"],
                    "neutral": bool(g["neutral_site"]),
                    "date": g["game_date"],
                    "season": g["season"],
                    "new_season": prev_season is not None and g["season"] != prev_season,
                }
            )
            prev_season = g["season"]
        return out

    def cross_source_duplicates(self, sport: Sport) -> list[dict]:
        """Partidos que parecen el mismo pero vienen de fuentes distintas.

        La clave de deduplicacion es (source, source_id), asi que dos fuentes
        que cubran temporadas solapadas meten los mismos partidos dos veces sin
        que salte nada. El efecto es silencioso y grave: cada resultado cuenta
        el doble en el entrenamiento, y el Elo exagera todas las diferencias.

        No se borra nada automaticamente porque en beisbol un doubleheader
        comparte fecha y equipos de forma legitima. Se informa para que decidas.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT game_date, home_team, away_team, COUNT(DISTINCT source) n_fuentes, "
                "       GROUP_CONCAT(DISTINCT source) fuentes "
                "FROM games WHERE sport=? "
                "GROUP BY game_date, home_team, away_team "
                "HAVING n_fuentes > 1 "
                "ORDER BY game_date DESC",
                (sport.value,),
            ).fetchall()
            return [dict(r) for r in rows]

    def summary(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT sport, season, COUNT(*) n, MIN(game_date) d0, MAX(game_date) d1 "
                "FROM games GROUP BY sport, season ORDER BY sport, season"
            ).fetchall()
            return [dict(r) for r in rows]

    def count(self, sport: Sport | None = None) -> int:
        with self._conn() as c:
            if sport:
                return c.execute(
                    "SELECT COUNT(*) FROM games WHERE sport=?", (sport.value,)
                ).fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM games").fetchone()[0]
