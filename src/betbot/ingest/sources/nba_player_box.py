"""Estadisticas por jugador y partido de NBA (hoopR / ESPN), 2002-actual.

POR QUE NBA TIENE ALGO QUE NFL NO. El archivo de NFL solo trae a los jugadores
que registraron alguna estadistica, asi que el modelo no ve a los que salieron
al campo y no hicieron nada. Este trae a TODA la plantilla del partido, incluidos
los que no jugaron (`did_not_play`, con el motivo desde ~2010), y los minutos de
cada uno. Eso permite definir "jugo" exactamente como lo hacen las casas para
anular una prop: minutos > 0. Sin esa definicion el modelo ve menos ceros de
los reales y sobreestima los overs de los suplentes.

TRES COSAS DEL DATO QUE EL CODIGO RESUELVE:

  - La temporada se nombra por el ano en que TERMINA: 2026 = 2025-26. La que
    empieza en octubre de 2026 es la 2027.
  - La notacion de posiciones cambio con los anos: en 2002 dominan PG/SG/SF/PF,
    desde ~2020 casi todo es G/F/C. Se normaliza a G/F/C; sin eso un "PG" de
    2005 y un "G" de 2025 serian grupos distintos para el modelo.
  - ~150-200 filas por temporada no estan marcadas como DNP pero tampoco tienen
    minutos. Con el criterio "jugo = minutos > 0" quedan fuera, igual que
    quedaria anulada la apuesta.

Formato parquet: requiere pyarrow. Es la unica fuente del proyecto que lo pide,
asi que se importa aqui y no en el paquete, y su ausencia se explica en vez de
reventar con un ImportError.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import date

from betbot.ingest.http import CachedFetcher, caducidad

log = logging.getLogger(__name__)

URL_BASE = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/"
    "nba/player_box/parquet/player_box_{season}.parquet"
)

FALTA_PYARROW = (
    "Las estadisticas de jugador de NBA vienen en formato parquet y hace falta\n"
    "la libreria pyarrow para leerlas. Instalala una vez:\n\n"
    "    .venv/bin/pip install pyarrow\n"
)

# Temporada regular y playoffs de ESPN. 5 = play-in, 1 = pretemporada.
REGULAR = 2
PLAYOFFS = 3

STATS_NBA = ("points", "rebounds", "assists", "threes")


def normalizar_posicion(pos: str | None) -> str:
    """G/F/C a partir de cualquier notacion historica ("PG", "SF", "G-F", ...)."""
    if not pos:
        return "?"
    p = pos.strip().upper()
    if p in ("NA", ""):
        return "?"
    primera = p.split("-")[0]
    if primera in ("PG", "SG", "G"):
        return "G"
    if primera in ("SF", "PF", "F"):
        return "F"
    if primera == "C":
        return "C"
    return "?"


@dataclass(frozen=True)
class NBAPlayerGame:
    athlete_id: str
    player_name: str
    position: str          # G / F / C / ?
    season: int            # ano en que TERMINA la temporada
    season_type: int
    game_id: str
    game_date: str         # ISO, para ordenar
    team: str
    opponent: str
    minutes: float
    starter: bool
    stats: dict[str, float]

    @property
    def jugo(self) -> bool:
        """Criterio de las casas para no anular una prop: salio a la cancha."""
        return self.minutes > 0


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


@dataclass
class NBAPlayerBox:
    name: str = "hoopr_player_box"
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    descartados: list[str] = field(default_factory=list, repr=False)

    def fetch_season(self, season: int) -> list[NBAPlayerGame]:
        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise RuntimeError(FALTA_PYARROW) from e
        crudo = self.fetcher.get_bytes(
            URL_BASE.format(season=season),
            suffix=".parquet",
            max_age=caducidad(date(season, 7, 1)),   # termina con las Finales
        )
        filas = pq.read_table(io.BytesIO(crudo)).to_pylist()
        return self.parse(filas)

    def parse(self, filas: list[dict]) -> list[NBAPlayerGame]:
        out: list[NBAPlayerGame] = []
        for r in filas:
            aid = r.get("athlete_id")
            gid = r.get("game_id")
            if aid is None or gid is None or r.get("season") is None:
                self.descartados.append("fila sin atleta, partido o temporada")
                continue
            out.append(
                NBAPlayerGame(
                    athlete_id=str(aid),
                    player_name=(r.get("athlete_display_name") or "").strip(),
                    position=normalizar_posicion(r.get("athlete_position_abbreviation")),
                    season=int(r["season"]),
                    season_type=int(r.get("season_type") or 0),
                    game_id=str(gid),
                    game_date=str(r.get("game_date") or ""),
                    team=(r.get("team_abbreviation") or "").strip(),
                    opponent=(r.get("opponent_team_abbreviation") or "").strip(),
                    minutes=_num(r.get("minutes")),
                    starter=bool(r.get("starter")),
                    stats={
                        "points": _num(r.get("points")),
                        "rebounds": _num(r.get("rebounds")),
                        "assists": _num(r.get("assists")),
                        "threes": _num(r.get("three_point_field_goals_made")),
                    },
                )
            )
        return out
