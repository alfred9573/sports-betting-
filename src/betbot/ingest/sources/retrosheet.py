"""MLB historico desde los game logs de Retrosheet (espejo de Chadwick Bureau).

La mejor fuente de beisbol que existe y es gratis: 1871-2025, 2430 partidos por
temporada moderna, con marcador final, doubleheaders correctamente distinguidos
y abridores identificados. Se sirve como TXT plano desde GitHub, asi que no hay
cuota ni rate limit.

Formato: CSV sin cabecera, 161 campos por fila. Los que importan aqui:
    0   fecha YYYYMMDD
    1   numero de partido en el dia (0 = unico, 1/2 = doubleheader)
    3   equipo visitante (codigo Retrosheet)
    6   equipo local
    9   carreras del visitante
    10  carreras del local
    101 / 102  id y nombre del abridor visitante
    103 / 104  id y nombre del abridor local

El campo 1 es la razon de que la clave de deduplicacion no pueda ser
(fecha, equipos): en un doubleheader ese par se repite con dos resultados
distintos, y usarlo como clave perderia la mitad de los partidos.

Nota: los codigos de Retrosheet son de SEDE, no de franquicia (CHN = Cubs,
CHA = White Sox, ANA = Angels). El registro de equipos ya los mapea.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime

from betbot.ingest.http import CachedFetcher
from betbot.ingest.teams import TeamRegistry, UnknownTeamError
from betbot.ingest.types import GameResult
from betbot.types import Sport

log = logging.getLogger(__name__)

BASE_URL = (
    "https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons"
)

F_DATE, F_GAMENUM, F_VISITOR, F_HOME = 0, 1, 3, 6
F_VIS_RUNS, F_HOME_RUNS = 9, 10
F_VIS_SP_NAME, F_HOME_SP_NAME = 102, 104
MIN_FIELDS = 105


@dataclass
class Retrosheet:
    name: str = "retrosheet"
    sport: Sport = Sport.MLB
    base_url: str = BASE_URL
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.MLB, strict=False)
    )
    skipped: list[str] = field(default_factory=list, repr=False)

    def season_url(self, season: int) -> str:
        return f"{self.base_url}/{season}/GL{season}.TXT"

    def fetch_season(self, season: int) -> list[GameResult]:
        raw = self.fetcher.get_text(self.season_url(season), encoding="latin-1", suffix=".txt")
        return self.parse(raw, season)

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        out: list[GameResult] = []
        for season in range(first, last + 1):
            try:
                out.extend(self.fetch_season(season))
            except Exception as e:  # noqa: BLE001 - una temporada ausente no aborta el rango
                log.warning("temporada %d no disponible: %s", season, e)
        return out

    def parse(self, raw: str, season: int) -> list[GameResult]:
        out: list[GameResult] = []
        for row in csv.reader(io.StringIO(raw)):
            g = self._parse_row(row, season)
            if g:
                out.append(g)
        return out

    def _parse_row(self, row: list[str], season: int) -> GameResult | None:
        if len(row) < MIN_FIELDS:
            return None

        try:
            game_date = datetime.strptime(row[F_DATE], "%Y%m%d").date()
            home_score = int(row[F_HOME_RUNS])
            away_score = int(row[F_VIS_RUNS])
        except (ValueError, IndexError):
            self.skipped.append(f"fila ilegible: {row[:2]}")
            return None

        try:
            home = self.registry.resolve(row[F_HOME])
            away = self.registry.resolve(row[F_VISITOR])
        except UnknownTeamError:
            home = away = None
        if not home or not away or home == away:
            self.skipped.append(f"{row[F_VISITOR]} vs {row[F_HOME]}")
            return None

        game_num = row[F_GAMENUM] or "0"
        return GameResult(
            sport=Sport.MLB,
            game_date=game_date,
            season=season,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            # fecha + equipos + numero de partido: unico incluso en doubleheader
            source_id=f"{row[F_DATE]}{row[F_HOME]}{game_num}",
            extra={
                "home_sp": row[F_HOME_SP_NAME].strip(),
                "away_sp": row[F_VIS_SP_NAME].strip(),
            },
        )
