"""Futbol ingles historico desde engsoccerdata (CSV en GitHub).

Todos los partidos de las 4 divisiones inglesas desde 1888. Estatico,
reproducible, sin cuota. Igual que el dataset de 538: sirve para validar
metodologia, no para operar temporadas en curso.

`tier=1` es la Premier League (y la vieja First Division).
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

URL = "https://raw.githubusercontent.com/jalapic/engsoccerdata/master/data-raw/england.csv"


@dataclass
class EngSoccerData:
    name: str = "engsoccerdata"
    sport: Sport = Sport.SOCCER_EPL
    url: str = URL
    tier: int = 1
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.SOCCER_EPL, strict=False)
    )
    skipped: list[str] = field(default_factory=list, repr=False)

    def _rows(self) -> list[dict]:
        raw = self.fetcher.get_text(self.url, suffix=".csv")
        return list(csv.DictReader(io.StringIO(raw)))

    def fetch_season(self, season: int) -> list[GameResult]:
        return self.parse(self._rows(), seasons={season})

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        return self.parse(self._rows(), seasons=set(range(first, last + 1)))

    def parse(self, rows: list[dict], seasons: set[int] | None = None) -> list[GameResult]:
        out: list[GameResult] = []
        for row in rows:
            try:
                if int(row["tier"]) != self.tier:
                    continue
                season = int(row["Season"])
            except (KeyError, ValueError):
                continue
            if seasons is not None and season not in seasons:
                continue

            try:
                home = self.registry.resolve(row["home"])
                away = self.registry.resolve(row["visitor"])
            except UnknownTeamError:
                home = away = None
            if not home or not away or home == away:
                self.skipped.append(f"{row.get('home')} vs {row.get('visitor')}")
                continue

            try:
                out.append(
                    GameResult(
                        sport=self.sport,
                        game_date=datetime.strptime(row["Date"], "%Y-%m-%d").date(),
                        season=season,
                        home_team=home,
                        away_team=away,
                        home_score=int(row["hgoal"]),
                        away_score=int(row["vgoal"]),
                        source=self.name,
                        # el dataset no trae id propio: se construye uno estable
                        source_id=f"{row['Date']}_{home}_{away}".replace(" ", ""),
                    )
                )
            except (KeyError, ValueError) as e:
                log.debug("fila descartada: %s", e)
        return out
