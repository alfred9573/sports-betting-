"""NFL historico desde nflverse/nfldata (CSV en GitHub).

Todos los partidos desde 1999 con marcador, sede neutral y tipo de partido
(regular/playoff). Estatico, sin cuota, reproducible.

Dos trampas del dataset:
  - Incluye partidos FUTUROS ya programados, con marcador vacio. Meterlos al
    entrenamiento como 0-0 seria desastroso; se filtran por marcador ausente.
  - Usa codigos de sede, no de franquicia: STL y LA son los mismos Rams, SD y
    LAC los mismos Chargers, OAK y LV los mismos Raiders. El registro de equipos
    los unifica; sin eso cada mudanza parte una franquicia en dos.
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

URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"


@dataclass
class NFLverse:
    name: str = "nflverse"
    sport: Sport = Sport.NFL
    url: str = URL
    include_playoffs: bool = True
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.NFL, strict=False)
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
            g = self._parse_row(row, seasons)
            if g:
                out.append(g)
        return out

    def _parse_row(self, row: dict, seasons: set[int] | None) -> GameResult | None:
        try:
            season = int(row["season"])
        except (KeyError, ValueError):
            return None
        if seasons is not None and season not in seasons:
            return None

        game_type = row.get("game_type", "REG")
        if game_type != "REG" and not self.include_playoffs:
            return None

        # Partido futuro ya programado: sin marcador. Nunca al entrenamiento.
        if not row.get("home_score") or not row.get("away_score"):
            return None

        try:
            home_score = int(row["home_score"])
            away_score = int(row["away_score"])
            game_date = datetime.strptime(row["gameday"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            self.skipped.append(f"fila ilegible: {row.get('game_id')}")
            return None

        try:
            home = self.registry.resolve(row["home_team"])
            away = self.registry.resolve(row["away_team"])
        except UnknownTeamError:
            home = away = None
        if not home or not away or home == away:
            self.skipped.append(f"{row.get('away_team')} vs {row.get('home_team')}")
            return None

        return GameResult(
            sport=Sport.NFL,
            game_date=game_date,
            season=season,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            source_id=str(row["game_id"]),
            neutral_site=row.get("location", "Home") == "Neutral",
            playoff=game_type != "REG",
        )
