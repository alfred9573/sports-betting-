"""NBA historico desde el dataset Elo de FiveThirtyEight.

Cubre TODOS los partidos de la NBA/BAA desde 1946 hasta 2015 con marcador final,
sede neutral y marca de playoffs. Es un CSV estatico en GitHub: no hay cuota, no
hay rate limit y es reproducible — el mismo commit da el mismo dataset siempre.

Limite importante: se corta en 2015. Sirve para VALIDAR la metodologia (que es
justo lo que hace falta ahora), no para operar hoy. Para temporadas recientes,
usar ESPNScoreboard.

Nota sobre el formato: el CSV trae DOS filas por partido (una por equipo, con
`_iscopy` marcando la duplicada). Ignorar eso duplica el dataset y arruina
cualquier Elo entrenado con el.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from betbot.ingest.http import CachedFetcher
from betbot.ingest.teams import TeamRegistry, UnknownTeamError
from betbot.ingest.types import GameResult
from betbot.types import Sport

log = logging.getLogger(__name__)

URL = (
    "https://raw.githubusercontent.com/fivethirtyeight/data/master/"
    "nba-elo/nbaallelo.csv"
)


@dataclass
class FiveThirtyEightNBA:
    name: str = "fivethirtyeight_nba"
    sport: Sport = Sport.NBA
    url: str = URL
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.NBA, strict=False)
    )
    skipped: list[str] = field(default_factory=list, repr=False)

    def _rows(self) -> list[dict]:
        raw = self.fetcher.get_text(self.url, suffix=".csv")
        return list(csv.DictReader(io.StringIO(raw)))

    def fetch_season(self, season: int) -> list[GameResult]:
        return self.parse(self._rows(), seasons={season})

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        """Un solo download para todo el rango: el CSV viene completo."""
        return self.parse(self._rows(), seasons=set(range(first, last + 1)))

    def parse(self, rows: list[dict], seasons: set[int] | None = None) -> list[GameResult]:
        out: list[GameResult] = []
        for row in rows:
            # Cada partido aparece dos veces, una por equipo. Nos quedamos con la
            # fila del equipo LOCAL para tener orientacion home/away consistente.
            if row.get("_iscopy") != "0":
                continue
            if row.get("game_location") != "H":
                continue

            try:
                season = int(row["year_id"])
            except (KeyError, ValueError):
                continue
            if seasons is not None and season not in seasons:
                continue

            game = self._parse_row(row, season)
            if game:
                out.append(game)
        return out

    def _parse_row(self, row: dict, season: int) -> GameResult | None:
        try:
            home = self.registry.resolve(row["fran_id"]) or self.registry.resolve(row["team_id"])
            away = self.registry.resolve(row["opp_fran"]) or self.registry.resolve(row["opp_id"])
        except UnknownTeamError:
            home = away = None

        if not home or not away:
            self.skipped.append(f"{row.get('fran_id')} vs {row.get('opp_fran')}")
            return None
        if home == away:
            # Franquicias distintas que colapsan al mismo canonico (mudanzas).
            self.skipped.append(f"colision de franquicia: {row.get('fran_id')}")
            return None

        try:
            return GameResult(
                sport=Sport.NBA,
                game_date=_parse_date(row["date_game"]),
                season=season,
                home_team=home,
                away_team=away,
                home_score=int(row["pts"]),
                away_score=int(row["opp_pts"]),
                source=self.name,
                source_id=str(row["game_id"]),
                neutral_site=row.get("notes", "").lower().startswith("at "),
                playoff=row.get("is_playoffs") == "1",
            )
        except (KeyError, ValueError) as e:
            log.debug("fila descartada: %s", e)
            return None


def _parse_date(value: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no parseable: {value}")
