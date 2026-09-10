"""Ligas europeas desde openfootball/football.json (GitHub).

LA UNICA FUENTE DE FUTBOL CON LA TEMPORADA EN CURSO que se ha podido verificar.
Cubre las cinco grandes ligas europeas con datos que se actualizan durante la
temporada, en JSON limpio.

Por que importa tener datos de la temporada en curso: el modelo no puede
predecir partidos de una temporada de la que no sabe nada (ver `coverage_check`
en cli.py). Las fuentes historicas —engsoccerdata llega a 2016, footballcsv de
Liga MX a 2024-25— sirven para validar metodologia, no para operar hoy.

Formato, con TRES variantes del marcador conviviendo en el mismo fichero:

    {"score": {"ht": [2,0], "ft": [3,0]}}   forma habitual
    {"score": [0, 0]}                        forma corta, sin descanso
    {"score": null} o sin clave              partido aun no jugado

Las tres aparecen mezcladas dentro de una misma temporada (en 2025-26 hay 353
del primer tipo y 27 del segundo). Asumir solo la primera hace que el parseo
reviente a mitad de la temporada — que es justo cuando ya te fiabas de el.

`team1` es el LOCAL y `team2` el visitante. No esta etiquetado como tal en el
JSON, asi que es una convencion que hay que conocer: invertirla no rompe nada
visiblemente, solo hace que el modelo aprenda la ventaja de local al reves.

CUIDADO CON MEZCLAR FUENTES: si ya tienes engsoccerdata ingerido y anades esta
para temporadas que se solapan, los mismos partidos entran dos veces con
`source` distinto, y la deduplicacion por (source, source_id) no los detecta.
`betbot ingest` avisa si lo detecta.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from betbot.ingest.http import CachedFetcher
from betbot.ingest.teams import TeamRegistry, UnknownTeamError
from betbot.ingest.types import GameResult
from betbot.types import Sport

log = logging.getLogger(__name__)

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"

LEAGUE_CODES: dict[Sport, str] = {
    Sport.SOCCER_EPL: "en.1",
    Sport.SOCCER_LA_LIGA: "es.1",
    Sport.SOCCER_BUNDESLIGA: "de.1",
    Sport.SOCCER_SERIE_A: "it.1",
    Sport.SOCCER_LIGUE_1: "fr.1",
}


@dataclass
class OpenFootballJSON:
    sport: Sport
    name: str = "openfootball"
    base_url: str = BASE_URL
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry | None = None
    skipped: list[str] = field(default_factory=list, repr=False)
    unplayed: int = field(default=0, repr=False)

    def __post_init__(self) -> None:
        if self.sport not in LEAGUE_CODES:
            raise ValueError(
                f"liga no cubierta por openfootball: {self.sport.name}. "
                f"Disponibles: {[s.name for s in LEAGUE_CODES]}"
            )
        self.name = f"openfootball_{LEAGUE_CODES[self.sport]}"
        if self.registry is None:
            self.registry = TeamRegistry(self.sport, strict=False)

    @staticmethod
    def season_label(season: int) -> str:
        """2026 -> '2026-27'. La temporada se identifica por su ano de INICIO."""
        return f"{season}-{str(season + 1)[-2:]}"

    def season_url(self, season: int) -> str:
        return f"{self.base_url}/{self.season_label(season)}/{LEAGUE_CODES[self.sport]}.json"

    def fetch_season(self, season: int) -> list[GameResult]:
        try:
            payload = self.fetcher.get_json(self.season_url(season))
        except Exception as e:  # noqa: BLE001 - una temporada ausente no es un error fatal
            log.warning("temporada %s no disponible: %s", self.season_label(season), e)
            return []
        return self.parse(payload, season)

    def fetch_range(self, first: int, last: int) -> list[GameResult]:
        out: list[GameResult] = []
        for season in range(first, last + 1):
            out.extend(self.fetch_season(season))
        return out

    def parse(self, payload: dict, season: int) -> list[GameResult]:
        out: list[GameResult] = []
        for match in payload.get("matches", []):
            g = self._parse_match(match, season)
            if g:
                out.append(g)
        return out

    def _parse_match(self, match: dict, season: int) -> GameResult | None:
        score = _final_score(match.get("score"))
        if score is None:
            # Partido programado pero no jugado. Es lo normal en una temporada
            # en curso; se cuenta aparte para no confundirlo con datos rotos.
            self.unplayed += 1
            return None
        home_score, away_score = score

        try:
            game_date = datetime.strptime(match["date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            self.skipped.append(f"fecha ilegible: {match.get('date')}")
            return None

        home_raw = match.get("team1", "")
        away_raw = match.get("team2", "")
        try:
            home = self.registry.resolve(home_raw)
            away = self.registry.resolve(away_raw)
        except UnknownTeamError:
            home = away = None
        if not home or not away or home == away:
            self.skipped.append(f"{home_raw} vs {away_raw}")
            return None

        return GameResult(
            sport=self.sport,
            game_date=game_date,
            season=season,
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            source_id=f"{game_date.isoformat()}_{home}_{away}".replace(" ", ""),
            extra={"round": match.get("round", "")},
        )


def _final_score(raw) -> tuple[int, int] | None:
    """Marcador final a partir de cualquiera de las tres variantes del formato."""
    if raw is None:
        return None

    if isinstance(raw, dict):
        valores = raw.get("ft")
    elif isinstance(raw, list):
        valores = raw
    else:
        return None

    if not isinstance(valores, list) or len(valores) != 2:
        return None
    try:
        return int(valores[0]), int(valores[1])
    except (TypeError, ValueError):
        return None
