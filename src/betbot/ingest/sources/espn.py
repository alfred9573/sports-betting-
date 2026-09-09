"""Scoreboard de ESPN: una sola forma de payload para NBA, NFL, MLB y futbol.

Es la fuente practica para temporadas RECIENTES, donde los datasets estaticos de
GitHub ya no llegan. API publica no documentada: gratis y sin key, pero puede
cambiar sin aviso — por eso el parseo es defensivo y los tests fijan el formato.

ADVERTENCIA DE ESTADO: no probado contra la API real (salida bloqueada en el
entorno de desarrollo). Formato cubierto con tests sobre payloads de ejemplo.
Hace falta una corrida real antes de confiar en el.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from betbot.ingest.http import CachedFetcher
from betbot.ingest.teams import TeamRegistry, UnknownTeamError
from betbot.ingest.types import GameResult
from betbot.types import Sport

log = logging.getLogger(__name__)

BASE = "https://site.api.espn.com/apis/site/v2/sports"

ESPN_PATHS: dict[Sport, str] = {
    Sport.NBA: "basketball/nba",
    Sport.NFL: "football/nfl",
    Sport.MLB: "baseball/mlb",
    Sport.SOCCER_EPL: "soccer/eng.1",
    Sport.SOCCER_LA_LIGA: "soccer/esp.1",
    Sport.SOCCER_SERIE_A: "soccer/ita.1",
    Sport.SOCCER_BUNDESLIGA: "soccer/ger.1",
    Sport.SOCCER_LIGUE_1: "soccer/fra.1",
    Sport.SOCCER_LIGA_MX: "soccer/mex.1",
    Sport.SOCCER_UCL: "soccer/uefa.champions",
}


@dataclass
class ESPNScoreboard:
    sport: Sport
    name: str = "espn"
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry | None = None
    skipped: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.sport not in ESPN_PATHS:
            raise ValueError(f"deporte no soportado por ESPN: {self.sport}")
        if self.registry is None:
            self.registry = TeamRegistry(self.sport, strict=False)

    def fetch_day(self, day: date) -> list[GameResult]:
        url = f"{BASE}/{ESPN_PATHS[self.sport]}/scoreboard?dates={day:%Y%m%d}"
        return self.parse(self.fetcher.get_json(url))

    def fetch_days(self, start: date, end: date) -> list[GameResult]:
        """Dia a dia: el scoreboard de ESPN solo devuelve una fecha por llamada.

        El cache del fetcher hace que reanudar un rango largo sea barato.
        """
        out: list[GameResult] = []
        day = start
        while day <= end:
            try:
                out.extend(self.fetch_day(day))
            except Exception as e:  # noqa: BLE001 - un dia malo no debe abortar el rango
                log.warning("fallo el dia %s: %s", day, e)
            day += timedelta(days=1)
        return out

    def parse(self, payload: dict) -> list[GameResult]:
        out: list[GameResult] = []
        for event in payload.get("events", []):
            g = self._parse_event(event)
            if g:
                out.append(g)
        return out

    def _parse_event(self, event: dict) -> GameResult | None:
        try:
            comp = event["competitions"][0]
        except (KeyError, IndexError):
            return None

        status = comp.get("status", event.get("status", {})).get("type", {})
        if not status.get("completed"):
            return None

        home_c = away_c = None
        for c in comp.get("competitors", []):
            if c.get("homeAway") == "home":
                home_c = c
            elif c.get("homeAway") == "away":
                away_c = c
        if not home_c or not away_c:
            return None

        try:
            home_raw = home_c["team"]["displayName"]
            away_raw = away_c["team"]["displayName"]
            home_score = int(home_c["score"])
            away_score = int(away_c["score"])
        except (KeyError, TypeError, ValueError):
            self.skipped.append(f"competidor incompleto: {event.get('id')}")
            return None

        try:
            home = self.registry.resolve(home_raw)
            away = self.registry.resolve(away_raw)
        except UnknownTeamError:
            home = away = None
        if not home or not away or home == away:
            self.skipped.append(f"{home_raw} vs {away_raw}")
            return None

        try:
            game_date = datetime.fromisoformat(
                event["date"].replace("Z", "+00:00")
            ).date()
        except (KeyError, ValueError):
            return None

        return GameResult(
            sport=self.sport,
            game_date=game_date,
            season=int(event.get("season", {}).get("year", game_date.year)),
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=f"{self.name}_{self.sport.value}",
            source_id=str(event["id"]),
            neutral_site=bool(comp.get("neutralSite", False)),
        )
