"""Estadisticas semanales de jugador de NFL (nflverse), 1999-actual.

POR QUE ESTO IMPORTA. Las props de jugador resultaron estar disponibles en el
plan de odds, pero eso solo da el PRECIO. Para saber si un precio esta mal hace
falta una estimacion propia de la probabilidad, y para eso hace falta el
historico de rendimiento de cada jugador. Esta fuente lo aporta: 27 temporadas
de lineas semanales por jugador, con el mismo esquema de 150 columnas de 1999 a
hoy, actualizado durante la temporada en curso.

Lo que NO resuelve: sigue sin existir historico de LINEAS de props. O sea, se
puede medir si el modelo predice bien al jugador (eso es backtesteable hoy con
estos datos), pero no si el mercado se equivoca. Eso ultimo solo se podra medir
con las lineas que `betbot collect` vaya archivando desde ahora.

Clave de deduplicacion: (player_id, season, week, season_type). Verificado sin
colisiones en 1999 y 2026. Se usa `player_id` (GSIS) y no el nombre porque los
nombres cambian de grafia entre temporadas y romperian la serie de un jugador
justo donde mas importa: su continuidad.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date

from betbot.ingest.http import CachedFetcher, caducidad

log = logging.getLogger(__name__)

URL_BASE = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)

# Columnas que se guardan. El CSV trae 150; casi todas son metricas avanzadas
# (EPA, air yards, WOPR) que no alimentan ninguna prop del catalogo. Guardar
# solo lo necesario mantiene la base manejable y deja explicito que consume el
# modelo.
STATS = (
    "completions", "attempts", "passing_yards", "passing_tds",
    "carries", "rushing_yards", "rushing_tds",
    "receptions", "targets", "receiving_yards", "receiving_tds",
)


@dataclass(frozen=True)
class PlayerWeek:
    """Linea de un jugador en un partido."""

    player_id: str
    player_name: str
    position: str
    season: int
    week: int
    season_type: str      # REG / POST
    game_id: str
    team: str
    opponent: str
    stats: dict[str, float]

    @property
    def clave(self) -> tuple:
        return (self.player_id, self.season, self.week, self.season_type)


def _num(valor: str | None) -> float:
    if valor is None:
        return 0.0
    v = valor.strip()
    if not v or v.upper() == "NA":
        return 0.0
    try:
        return float(v)
    except ValueError:
        return 0.0


@dataclass
class NFLPlayerStats:
    name: str = "nflverse_players"
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    descartados: list[str] = field(default_factory=list, repr=False)

    def fetch_season(self, season: int) -> list[PlayerWeek]:
        # La temporada S termina con el Super Bowl de febrero de S+1.
        raw = self.fetcher.get_text(
            URL_BASE.format(season=season), suffix=".csv",
            max_age=caducidad(date(season + 1, 3, 1)),
        )
        return self.parse(list(csv.DictReader(io.StringIO(raw))), season)

    def fetch_range(self, first: int, last: int) -> list[PlayerWeek]:
        out: list[PlayerWeek] = []
        for s in range(first, last + 1):
            try:
                out.extend(self.fetch_season(s))
            except Exception as e:  # una temporada que falta no aborta el resto
                log.warning("temporada %d no disponible: %s", s, e)
                self.descartados.append(f"{s}: {e}")
        return out

    def parse(self, rows: list[dict], season: int | None = None) -> list[PlayerWeek]:
        out: list[PlayerWeek] = []
        for r in rows:
            pid = (r.get("player_id") or "").strip()
            if not pid:
                # ~1 fila de cada 1000 viene sin id. Sin id no hay serie
                # temporal posible para ese jugador, asi que no sirve de nada.
                self.descartados.append("fila sin player_id")
                continue
            try:
                temporada = int(r["season"])
                semana = int(r["week"])
            except (KeyError, ValueError):
                self.descartados.append(f"{pid}: season/week ilegible")
                continue
            if season is not None and temporada != season:
                continue
            out.append(
                PlayerWeek(
                    player_id=pid,
                    player_name=(r.get("player_display_name") or "").strip(),
                    position=(r.get("position") or "").strip(),
                    season=temporada,
                    week=semana,
                    season_type=(r.get("season_type") or "REG").strip(),
                    game_id=(r.get("game_id") or "").strip(),
                    team=(r.get("team") or "").strip(),
                    opponent=(r.get("opponent_team") or "").strip(),
                    stats={k: _num(r.get(k)) for k in STATS},
                )
            )
        return out
