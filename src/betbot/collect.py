"""Archivador de lineas: construye el historico que ningun proveedor nos vende.

POR QUE EXISTE ESTE MODULO. El proyecto se quedo sin vias abiertas por una
razon concreta: no hay datos historicos. Las props de jugador no se pueden
evaluar porque nadie publica las lineas pasadas, y el line shopping no se pudo
backtestear porque los mirrors de odds multi-casa tienen las columnas de cuotas
recortadas. Sin historico no hay backtest, y sin backtest no hay forma honesta
de saber si una idea sirve.

La unica salida es dejar de buscar el historico y empezar a fabricarlo. Este
modulo guarda cada precio que ve, de cada casa, en cada mercado, sin apostar
nada. En dos o tres meses habra una base con la que SI se puede medir:
   - si una casa blanda se mueve tarde respecto a la sharp (line shopping),
   - si las props de jugador se precian mal de forma sistematica,
   - cuanto se mueve una linea entre la apertura y el cierre (CLV real).

Esto no genera picks. Es infraestructura para poder responder mas adelante una
pregunta que hoy no se puede responder. Vale la pena decirlo claro: el primer
resultado util tardara meses, y puede perfectamente ser "tampoco hay nada".

DECISION DE DISENO: SE GUARDAN CAMBIOS, NO LATIDOS. Un barrido completo son
~240 eventos x 29 casas x 3 mercados x 2 lados = ~40.000 filas. A cada media
hora eso son 2 millones de filas al dia, y el 99% son el mismo precio repetido.
El archivador compara con el ultimo precio conocido de cada (evento, casa,
mercado, seleccion, linea) y solo escribe si cambio. El resultado es una serie
de movimientos, que es justo lo que hay que analizar, y una base que cabe en
disco.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at   TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    sport         TEXT NOT NULL,
    commence_time TEXT NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    bookmaker     TEXT NOT NULL,
    market        TEXT NOT NULL,
    selection     TEXT NOT NULL,
    point         REAL,
    decimal_odds  REAL NOT NULL,
    book_update   TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_event   ON odds_snapshots (event_id);
CREATE INDEX IF NOT EXISTS idx_snap_captura ON odds_snapshots (captured_at);
CREATE INDEX IF NOT EXISTS idx_snap_sport   ON odds_snapshots (sport, captured_at);
-- La clave de deduplicacion: el ultimo precio de cada linea concreta.
CREATE INDEX IF NOT EXISTS idx_snap_linea
    ON odds_snapshots (event_id, bookmaker, market, selection, point, id);
"""


@dataclass(frozen=True)
class PriceRow:
    """Un precio suelto. Deliberadamente mas laxo que `types.Outcome`.

    `market` es un string libre, no el enum `Market`: asi entran las props de
    jugador (`player_points`, `player_pass_tds`, ...) sin tocar el dominio.
    """

    event_id: str
    sport: str
    commence_time: str
    home_team: str
    away_team: str
    bookmaker: str
    market: str
    selection: str
    point: float | None
    decimal_odds: float
    book_update: str | None = None

    @property
    def clave(self) -> tuple:
        """Identidad de la linea, independiente del precio y del instante.

        El `point` va dentro a proposito: "Over 45.5" y "Over 46.5" son lineas
        distintas, y confundirlas fue un bug real en `lineshop`. Si el punto no
        formara parte de la clave, mover la linea de 45.5 a 46.5 se registraria
        como un cambio de precio de la misma cosa.
        """
        return (self.event_id, self.bookmaker, self.market, self.selection, self.point)


def parse_evento(item: dict, sport: str) -> list[PriceRow]:
    """Aplana el JSON de un evento a filas de precio. Ignora lo que no entienda."""
    try:
        event_id = item["id"]
        commence = item["commence_time"]
        home = item.get("home_team") or ""
        away = item.get("away_team") or ""
    except KeyError:
        return []

    filas: list[PriceRow] = []
    for bk in item.get("bookmakers", []):
        casa = bk.get("key")
        if not casa:
            continue
        for mk in bk.get("markets", []):
            mercado = mk.get("key")
            if not mercado:
                continue
            actualizado = mk.get("last_update")
            for o in mk.get("outcomes", []):
                try:
                    precio = float(o["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if precio <= 1.0:
                    continue  # precio imposible en formato decimal
                # En props el lado es "Over"/"Under" y el jugador viene en
                # `description`. Sin unirlos, todas las props de un partido
                # colapsarian en dos selecciones y el archivo no serviria.
                nombre = str(o.get("name", ""))
                desc = o.get("description")
                seleccion = f"{desc} {nombre}".strip() if desc else nombre
                if not seleccion:
                    continue
                punto = o.get("point")
                filas.append(
                    PriceRow(
                        event_id=event_id,
                        sport=sport,
                        commence_time=commence,
                        home_team=home,
                        away_team=away,
                        bookmaker=casa,
                        market=mercado,
                        selection=seleccion,
                        point=float(punto) if punto is not None else None,
                        decimal_odds=precio,
                        book_update=actualizado,
                    )
                )
    return filas


@dataclass(frozen=True)
class ResultadoArchivo:
    vistas: int
    nuevas: int

    @property
    def sin_cambio(self) -> int:
        return self.vistas - self.nuevas


class OddsArchive:
    def __init__(self, path: str | Path = "data/odds_archive.db") -> None:
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

    def _ultimos_precios(self, conn, event_ids: set[str]) -> dict[tuple, float]:
        """Ultimo precio conocido por linea, para los eventos de esta tanda.

        Se hace en UNA consulta y no una por fila: con 40.000 filas por barrido,
        preguntar linea a linea convertiria un archivado de segundos en minutos.
        """
        if not event_ids:
            return {}
        ids = list(event_ids)
        ultimos: dict[tuple, float] = {}
        # SQLite limita los parametros de una sentencia, asi que se trocea.
        for i in range(0, len(ids), 400):
            lote = ids[i : i + 400]
            marcas = ",".join("?" * len(lote))
            filas = conn.execute(
                f"""
                SELECT s.event_id, s.bookmaker, s.market, s.selection, s.point,
                       s.decimal_odds
                  FROM odds_snapshots s
                  JOIN (
                        SELECT MAX(id) AS id
                          FROM odds_snapshots
                         WHERE event_id IN ({marcas})
                         GROUP BY event_id, bookmaker, market, selection, point
                       ) u ON u.id = s.id
                """,
                lote,
            )
            for r in filas:
                ultimos[
                    (r["event_id"], r["bookmaker"], r["market"], r["selection"], r["point"])
                ] = r["decimal_odds"]
        return ultimos

    def archivar(
        self, filas: list[PriceRow], captured_at: datetime | None = None
    ) -> ResultadoArchivo:
        """Guarda solo los precios que cambiaron respecto al ultimo conocido."""
        if not filas:
            return ResultadoArchivo(vistas=0, nuevas=0)
        ts = (captured_at or datetime.now(UTC)).isoformat()
        with self._conn() as c:
            ultimos = self._ultimos_precios(c, {f.event_id for f in filas})
            nuevas: list[PriceRow] = []
            # `vistos` protege del duplicado DENTRO de la misma tanda: si dos
            # mercados del feed repiten la linea, no queremos dos filas.
            vistos: dict[tuple, float] = {}
            for f in filas:
                k = f.clave
                previo = vistos.get(k, ultimos.get(k))
                if previo is not None and abs(previo - f.decimal_odds) < 1e-9:
                    continue
                vistos[k] = f.decimal_odds
                nuevas.append(f)
            c.executemany(
                """INSERT INTO odds_snapshots (
                       captured_at, event_id, sport, commence_time, home_team,
                       away_team, bookmaker, market, selection, point,
                       decimal_odds, book_update
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        ts, f.event_id, f.sport, f.commence_time, f.home_team,
                        f.away_team, f.bookmaker, f.market, f.selection,
                        f.point, f.decimal_odds, f.book_update,
                    )
                    for f in nuevas
                ],
            )
        return ResultadoArchivo(vistas=len(filas), nuevas=len(nuevas))

    def resumen(self) -> dict:
        """Estado del archivo: es lo que dira si ya hay muestra suficiente."""
        with self._conn() as c:
            fila = c.execute(
                """SELECT COUNT(*) AS filas,
                          COUNT(DISTINCT event_id) AS eventos,
                          COUNT(DISTINCT bookmaker) AS casas,
                          COUNT(DISTINCT market) AS mercados,
                          MIN(captured_at) AS desde,
                          MAX(captured_at) AS hasta
                     FROM odds_snapshots"""
            ).fetchone()
            por_mercado = c.execute(
                """SELECT market, COUNT(*) AS n, COUNT(DISTINCT event_id) AS ev
                     FROM odds_snapshots GROUP BY market ORDER BY n DESC"""
            ).fetchall()
            por_deporte = c.execute(
                """SELECT sport, COUNT(*) AS n, COUNT(DISTINCT event_id) AS ev
                     FROM odds_snapshots GROUP BY sport ORDER BY n DESC"""
            ).fetchall()
        return {
            "filas": fila["filas"],
            "eventos": fila["eventos"],
            "casas": fila["casas"],
            "mercados": fila["mercados"],
            "desde": fila["desde"],
            "hasta": fila["hasta"],
            "por_mercado": [dict(r) for r in por_mercado],
            "por_deporte": [dict(r) for r in por_deporte],
        }


# Mercados de props por deporte. No son los mismos: pedir `player_points` en
# NFL devuelve 422, no una lista vacia. La lista se mantiene corta a proposito
# porque el coste es n_mercados * n_regiones POR PARTIDO.
PROPS_POR_DEPORTE: dict[str, tuple[str, ...]] = {
    "americanfootball_nfl": (
        "player_pass_yds", "player_pass_tds", "player_rush_yds",
        "player_reception_yds", "player_receptions", "player_anytime_td",
    ),
    "basketball_nba": (
        "player_points", "player_rebounds", "player_assists", "player_threes",
    ),
    "baseball_mlb": (
        "batter_hits", "batter_total_bases", "batter_home_runs",
        "pitcher_strikeouts",
    ),
}
# Las ligas de futbol comparten catalogo de props.
for _liga in (
    "soccer_mexico_ligamx", "soccer_epl", "soccer_spain_la_liga",
    "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league",
):
    PROPS_POR_DEPORTE[_liga] = (
        "player_shots_on_target", "player_shots", "player_goal_scorer_anytime",
    )
