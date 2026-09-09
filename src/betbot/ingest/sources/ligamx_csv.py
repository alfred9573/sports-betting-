"""Liga MX desde footballcsv/mexico (cache de worldfootball en GitHub).

Cobertura: 2018-19 a 2024-25, ~2.050 partidos utiles. Estatico, sin cuota.

TRES PARTICULARIDADES DE LA LIGA MX QUE HAY QUE MODELAR BIEN

1. DOS TORNEOS POR ANO. Apertura (jul-dic) y Clausura (ene-may) son campeonatos
   independientes, cada uno de 17 jornadas. Para el modelo se tratan como
   continuos —la plantilla sigue siendo la misma entre torneos— pero se etiqueta
   el stage por si se quiere separar. Lo que NO hay que hacer es tratar cada
   torneo como una temporada nueva y regresar los ratings a la media dos veces
   por ano: eso borraria la mitad de la informacion disponible.

2. LIGUILLA. Cada torneo termina en playoff de ida y vuelta, donde el marcador
   agregado cambia los incentivos (un equipo puede jugar a defender una ventaja).
   Se marcan como `playoff=True` para poder excluirlos.

3. CLAUSURA 2020 CANCELADO. La COVID corto el torneo y el dataset trae esos
   partidos con marcador "(*)" en vez de vacio. Son 144 filas: parsearlas como
   0-0 meteria 144 empates falsos en un dataset de 2.000 partidos, un 7% de
   contaminacion directa sobre la clase peor calibrada del modelo.
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

BASE_URL = "https://raw.githubusercontent.com/footballcsv/mexico/master"

# Temporadas con archivo mx.1.csv publicado.
AVAILABLE = (
    "2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
)


@dataclass
class LigaMX:
    name: str = "footballcsv_mx"
    sport: Sport = Sport.SOCCER_LIGA_MX
    base_url: str = BASE_URL
    include_playoffs: bool = True
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.SOCCER_LIGA_MX, strict=False)
    )
    skipped: list[str] = field(default_factory=list, repr=False)

    def season_label(self, season: int) -> str:
        """2018 -> '2018-19'. La temporada se identifica por su ano de inicio."""
        return f"{season}-{str(season + 1)[-2:]}"

    def season_url(self, season: int) -> str:
        return f"{self.base_url}/{self.season_label(season)}/mx.1.csv"

    def fetch_season(self, season: int) -> list[GameResult]:
        label = self.season_label(season)
        if label not in AVAILABLE:
            log.warning("temporada %s no disponible en la fuente", label)
            return []
        raw = self.fetcher.get_text(self.season_url(season), suffix=".csv")
        return self.parse(raw, season)

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        out: list[GameResult] = []
        for season in range(first, last + 1):
            try:
                out.extend(self.fetch_season(season))
            except Exception as e:  # noqa: BLE001 - una temporada caida no aborta el rango
                log.warning("temporada %d fallo: %s", season, e)
        return out

    def parse(self, raw: str, season: int) -> list[GameResult]:
        out: list[GameResult] = []
        for row in csv.DictReader(io.StringIO(raw)):
            g = self._parse_row(row, season)
            if g:
                out.append(g)
        return out

    def _parse_row(self, row: dict, season: int) -> GameResult | None:
        stage = (row.get("Stage") or "").strip()
        is_playoff = "liguilla" in stage.lower()
        if is_playoff and not self.include_playoffs:
            return None

        score = self._parse_score(row.get("FT", ""))
        if score is None:
            # Incluye el "(*)" del Clausura 2020 cancelado. Silencioso a
            # proposito: son partidos que no se jugaron, no errores de datos.
            return None
        home_score, away_score = score

        game_date = self._parse_date(row)
        if game_date is None:
            self.skipped.append(f"fecha ilegible: {row.get('Date')}")
            return None

        try:
            home = self.registry.resolve(row["Team 1"])
            away = self.registry.resolve(row["Team 2"])
        except UnknownTeamError:
            home = away = None
        if not home or not away or home == away:
            self.skipped.append(f"{row.get('Team 1')} vs {row.get('Team 2')}")
            return None

        round_ = (row.get("Round") or "").strip()
        return GameResult(
            sport=Sport.SOCCER_LIGA_MX,
            game_date=game_date,
            season=season,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            source_id=f"{game_date.isoformat()}_{home}_{away}_{round_}".replace(" ", ""),
            playoff=is_playoff,
            extra={"stage": stage, "round": round_},
        )

    @staticmethod
    def _parse_score(value: str) -> tuple[int, int] | None:
        value = (value or "").strip()
        if not value or "-" not in value:
            return None
        head = value.split()[0]  # descarta anotaciones tipo "1-1 pen."
        parts = head.split("-")
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    @staticmethod
    def _parse_date(row: dict) -> date | None:
        """Prefiere el timestamp UTC; cae al campo Date cuando falta (63 filas)."""
        utc = (row.get("UTC") or "").strip()
        if utc:
            try:
                return datetime.fromisoformat(utc.replace("Z", "+00:00")).date()
            except ValueError:
                pass
        raw = (row.get("Date") or "").strip()
        for fmt in ("%a %b %d %Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
