"""Persistencia SQLite de senales y cierres de linea.

El objetivo real de esto no es llevar contabilidad: es poder medir CLV (closing
line value). CLV — si tu precio le gano al precio de cierre — es el unico
indicador que da senal util con pocas apuestas. El ROI necesita del orden de
1000-2000 apuestas para distinguirse del ruido a un edge del 3%; el CLV lo
detecta en 100-200. Sin esta tabla, los primeros meses del bot no son evaluables.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from betbot.types import Signal

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL,
    sport           TEXT NOT NULL,
    commence_time   TEXT NOT NULL,
    matchup         TEXT NOT NULL,
    market          TEXT NOT NULL,
    selection       TEXT NOT NULL,
    point           REAL,
    bookmaker       TEXT NOT NULL,
    decimal_odds    REAL NOT NULL,
    model_prob      REAL NOT NULL,
    fair_prob       REAL NOT NULL,
    ev              REAL NOT NULL,
    edge            REAL NOT NULL,
    kelly_stake     REAL NOT NULL,
    stake_units     REAL NOT NULL,
    model_name      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    closing_odds    REAL,
    closing_fair_prob REAL,
    closed_at       TEXT,
    result          TEXT,
    pnl             REAL,
    UNIQUE (event_id, market, selection, bookmaker, created_at)
);
CREATE INDEX IF NOT EXISTS idx_signals_event ON signals (event_id);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals (created_at);
CREATE INDEX IF NOT EXISTS idx_signals_commence ON signals (commence_time);
"""

# Columnas anadidas despues de la v1. SQLite no tiene "ADD COLUMN IF NOT EXISTS",
# asi que se comprueba el pragma: una BD creada con la version vieja se migra en
# sitio en vez de obligar a borrar el historial de senales, que es justo lo que
# no se puede perder.
MIGRATIONS = [
    ("closing_fair_prob", "ALTER TABLE signals ADD COLUMN closing_fair_prob REAL"),
    ("closed_at", "ALTER TABLE signals ADD COLUMN closed_at TEXT"),
]


class SignalStore:
    def __init__(self, path: str | Path = "data/betbot.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(conn) -> None:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(signals)")}
        for column, ddl in MIGRATIONS:
            if column not in existing:
                conn.execute(ddl)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save(self, signal: Signal) -> bool:
        """Guarda una senal. Devuelve False si ya existia (deduplicacion)."""
        with self._conn() as c:
            try:
                c.execute(
                    """INSERT INTO signals (
                        event_id, sport, commence_time, matchup, market, selection,
                        point, bookmaker, decimal_odds, model_prob, fair_prob, ev,
                        edge, kelly_stake, stake_units, model_name, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        signal.event_id, signal.sport.value,
                        signal.commence_time.isoformat(), signal.matchup,
                        signal.market.value, signal.selection, signal.point,
                        signal.bookmaker, signal.decimal_odds, signal.model_prob,
                        signal.fair_prob, signal.ev, signal.edge,
                        signal.kelly_stake, signal.stake_units, signal.model_name,
                        signal.created_at.isoformat(),
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def save_many(self, signals: list[Signal]) -> int:
        return sum(1 for s in signals if self.save(s))

    def already_alerted(self, event_id: str, market: str, selection: str) -> bool:
        """Evita spamear la misma senal en cada corrida del scheduler."""
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM signals WHERE event_id=? AND market=? AND selection=? LIMIT 1",
                (event_id, market, selection),
            ).fetchone()
            return row is not None

    def record_closing_odds(
        self,
        event_id: str,
        selection: str,
        closing: float,
        fair_prob: float | None = None,
    ) -> int:
        """Registra el precio de cierre de una seleccion.

        No pisa un cierre ya registrado: el primero que se captura es el bueno.
        Si el job se corre dos veces, la segunda no degrada el dato con un
        precio tomado despues del salto inicial.
        """
        with self._conn() as c:
            cur = c.execute(
                "UPDATE signals SET closing_odds=?, closing_fair_prob=?, "
                "closed_at=datetime('now') "
                "WHERE event_id=? AND selection=? AND closing_odds IS NULL",
                (closing, fair_prob, event_id, selection),
            )
            return cur.rowcount

    def pending_close(self, within_minutes: int = 30) -> list[dict]:
        """Senales cuyo partido empieza pronto y aun no tienen cierre registrado.

        Es la cola de trabajo del job de captura: hay que tomar el precio LO MAS
        CERCA POSIBLE del inicio, porque el cierre es la linea mas eficiente que
        publica el mercado y es contra esa que se mide si tu precio era bueno.
        """
        now = datetime.now(UTC)
        horizon = now + timedelta(minutes=within_minutes)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM signals WHERE closing_odds IS NULL "
                "AND commence_time > ? AND commence_time <= ? "
                "ORDER BY commence_time",
                (now.isoformat(), horizon.isoformat()),
            ).fetchall()
            return [dict(r) for r in rows]

    def missed_close(self) -> list[dict]:
        """Senales cuyo partido YA empezo sin que se capturara el cierre.

        Cada fila aqui es una apuesta que nunca podra evaluarse por CLV. Si esta
        lista crece, el job de captura no esta corriendo con la frecuencia
        suficiente y te estas quedando ciego sin enterarte.
        """
        now = datetime.now(UTC).isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM signals WHERE closing_odds IS NULL AND commence_time <= ? "
                "ORDER BY commence_time DESC",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]

    def settle(self, event_id: str, winning_selection: str) -> int:
        """Liquida todas las senales de un evento segun el ganador."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, selection, decimal_odds, stake_units FROM signals "
                "WHERE event_id=? AND result IS NULL",
                (event_id,),
            ).fetchall()
            for r in rows:
                won = r["selection"] == winning_selection
                pnl = r["stake_units"] * (r["decimal_odds"] - 1.0) if won else -r["stake_units"]
                c.execute(
                    "UPDATE signals SET result=?, pnl=? WHERE id=?",
                    ("win" if won else "loss", round(pnl, 2), r["id"]),
                )
            return len(rows)

    def open_signals(self) -> list[dict]:
        with self._conn() as c:
            now = datetime.now(UTC).isoformat()
            rows = c.execute(
                "SELECT * FROM signals WHERE result IS NULL AND commence_time > ? "
                "ORDER BY commence_time",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]

    def settled_signals(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM signals WHERE result IS NOT NULL ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]
