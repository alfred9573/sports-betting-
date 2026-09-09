"""NBA reciente desde hoopR-nba-data (espejo de ESPN en GitHub).

POR QUE ESTA FUENTE Y NO LA API DE ESPN DIRECTAMENTE

Son LOS MISMOS DATOS —hoopR raspa ESPN a diario— pero servidos como un CSV
estatico desde GitHub. Tres ventajas concretas:

  1. ESPN devuelve 403 a peticiones programaticas desde muchas redes, incluso
     con cabeceras de navegador. GitHub no bloquea a nadie.
  2. Una descarga cubre TODAS las temporadas. La API de ESPN sirve una fecha por
     llamada: una temporada de NBA son ~270 peticiones y varios minutos.
  3. Es reproducible: el mismo commit da el mismo dataset siempre.

Cobertura: 2002 hasta la temporada en curso, ~33.000 partidos. Cubre el hueco
que deja el dataset de FiveThirtyEight, que se congelo en 2015.

CONVENCION DE TEMPORADA: `season` es el ano de FIN, igual que en el dataset de
538. La temporada 2026 es la 2025-26 y termina en junio de 2026. Las dos fuentes
de NBA coinciden en esto, asi que se pueden mezclar en la misma base de datos.

TRAMPA DE LOS PARTIDOS DE EXHIBICION: los del All-Star vienen etiquetados con
`season_type=2`, EXACTAMENTE IGUAL que la temporada regular, asi que filtrar por
ese campo no los quita. Lo que los excluye es que sus equipos ("Team Chuck",
"World", "Western Conf All-Stars") no existen en el registro canonico. Son 30
partidos con marcadores absurdos —211-186 y cosas asi— que desplazarian los
ratings ofensivos de todos los participantes.
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

URL = (
    "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/"
    "nba/schedules/nba_schedule_master.csv"
)

# ESPN: 1=pretemporada, 2=regular, 3=playoffs, 4=all-star, 5=play-in.
# La pretemporada se excluye: alineaciones irreales y esfuerzo nulo.
VALID_SEASON_TYPES = {"2", "3", "5"}
POSTSEASON_TYPES = {"3", "5"}
TRUTHY = {"true", "1", "t", "yes"}


@dataclass
class HoopRNBA:
    name: str = "hoopr_nba"
    sport: Sport = Sport.NBA
    url: str = URL
    include_playoffs: bool = True
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.NBA, strict=False)
    )
    skipped: list[str] = field(default_factory=list, repr=False)
    exhibition_skipped: int = field(default=0, repr=False)

    def _rows(self) -> list[dict]:
        # ~38 MB. El cache en disco hace que la segunda vez sea instantanea.
        raw = self.fetcher.get_text(self.url, suffix=".csv")
        return list(csv.DictReader(io.StringIO(raw)))

    def fetch_season(self, season: int) -> list[GameResult]:
        return self.parse(self._rows(), seasons={season})

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        """Una sola descarga cubre todo el rango."""
        return self.parse(self._rows(), seasons=set(range(first, last + 1)))

    def parse(self, rows: list[dict], seasons: set[int] | None = None) -> list[GameResult]:
        out: list[GameResult] = []
        for row in rows:
            g = self._parse_row(row, seasons)
            if g:
                out.append(g)
        return out

    def _parse_row(self, row: dict, seasons: set[int] | None) -> GameResult | None:
        # Solo partidos TERMINADOS: uno en curso trae marcador parcial.
        if (row.get("status_type_completed") or "").lower() not in TRUTHY:
            return None

        season_type = (row.get("season_type") or "2").strip()
        if season_type not in VALID_SEASON_TYPES:
            return None
        is_playoff = season_type in POSTSEASON_TYPES
        if is_playoff and not self.include_playoffs:
            return None

        try:
            season = int(row["season"])
        except (KeyError, ValueError):
            return None
        if seasons is not None and season not in seasons:
            return None

        try:
            home_score = int(float(row["home_score"]))
            away_score = int(float(row["away_score"]))
        except (KeyError, ValueError, TypeError):
            self.skipped.append(f"marcador ilegible: {row.get('id')}")
            return None

        try:
            game_date = datetime.fromisoformat(
                row["date"].replace("Z", "+00:00")
            ).date()
        except (KeyError, ValueError, AttributeError):
            self.skipped.append(f"fecha ilegible: {row.get('id')}")
            return None

        home_raw = row.get("home_display_name", "")
        away_raw = row.get("away_display_name", "")
        try:
            home = self.registry.resolve(home_raw)
            away = self.registry.resolve(away_raw)
        except UnknownTeamError:
            home = away = None

        if not home or not away or home == away:
            # Casi siempre es un partido de exhibicion (All-Star). Se cuenta
            # aparte para que no contamine el aviso de "equipos sin alias", que
            # debe señalar problemas REALES de cobertura del registro.
            if _looks_like_exhibition(home_raw) or _looks_like_exhibition(away_raw):
                self.exhibition_skipped += 1
            else:
                self.skipped.append(f"{away_raw} vs {home_raw}")
            return None

        return GameResult(
            sport=Sport.NBA,
            game_date=game_date,
            season=season,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            source_id=str(row["id"]),
            neutral_site=(row.get("neutral_site") or "").lower() in TRUTHY,
            playoff=is_playoff,
        )


def _looks_like_exhibition(name: str) -> bool:
    lowered = (name or "").lower()
    return (
        "all-star" in lowered
        or "all star" in lowered
        or lowered.startswith("team ")
        or lowered in {"world", "usa", "stars", "stripes"}
    )
