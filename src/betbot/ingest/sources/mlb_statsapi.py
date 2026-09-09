"""MLB desde la StatsAPI oficial (statsapi.mlb.com).

Gratis, sin API key, sin registro, y es la fuente que alimenta a MLB.com — o
sea, autoritativa. Cubre temporada en curso e historico.

ADVERTENCIA DE ESTADO: este adaptador NO se ha podido probar contra la API real
porque el entorno donde se escribio tiene bloqueado el acceso saliente a
statsapi.mlb.com. El parseo esta cubierto con tests sobre payloads de ejemplo
que reproducen el formato documentado, pero hace falta UNA corrida real
(`betbot ingest --sport mlb --season 2024 --limit-days 3`) antes de fiarse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from betbot.ingest.http import CachedFetcher
from betbot.ingest.teams import TeamRegistry, UnknownTeamError
from betbot.ingest.types import GameResult
from betbot.types import Sport

log = logging.getLogger(__name__)

BASE = "https://statsapi.mlb.com/api/v1"

# Solo estados que representan un partido REALMENTE terminado. Incluir
# suspendidos o en curso mete marcadores parciales en el entrenamiento.
FINAL_STATES = {"Final", "Completed Early", "Game Over"}


@dataclass
class MLBStatsAPI:
    name: str = "mlb_statsapi"
    sport: Sport = Sport.MLB
    fetcher: CachedFetcher = field(default_factory=CachedFetcher)
    registry: TeamRegistry = field(
        default_factory=lambda: TeamRegistry(Sport.MLB, strict=False)
    )
    include_playoffs: bool = True
    skipped: list[str] = field(default_factory=list, repr=False)

    def fetch_season(self, season: int) -> list[GameResult]:
        """Temporada completa en una sola llamada por rango de fechas."""
        game_types = "R,F,D,L,W" if self.include_playoffs else "R"
        url = (
            f"{BASE}/schedule?sportId=1&gameTypes={game_types}"
            f"&startDate={season}-01-01&endDate={season}-12-31"
        )
        return self.parse(self.fetcher.get_json(url), season)

    def fetch_date(self, day: date) -> list[GameResult]:
        url = f"{BASE}/schedule?sportId=1&date={day.isoformat()}"
        return self.parse(self.fetcher.get_json(url), day.year)

    def parse(self, payload: dict, season: int) -> list[GameResult]:
        out: list[GameResult] = []
        for day in payload.get("dates", []):
            for g in day.get("games", []):
                result = self._parse_game(g, season)
                if result:
                    out.append(result)
        return out

    def _parse_game(self, g: dict, season: int) -> GameResult | None:
        status = g.get("status", {}).get("detailedState", "")
        if status not in FINAL_STATES:
            return None

        try:
            teams = g["teams"]
            home_raw = teams["home"]["team"]["name"]
            away_raw = teams["away"]["team"]["name"]
            home_score = int(teams["home"]["score"])
            away_score = int(teams["away"]["score"])
        except (KeyError, TypeError, ValueError):
            self.skipped.append(f"payload incompleto: {g.get('gamePk')}")
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
            game_date = datetime.strptime(g["officialDate"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            self.skipped.append(f"fecha invalida: {g.get('gamePk')}")
            return None

        return GameResult(
            sport=Sport.MLB,
            game_date=game_date,
            season=int(g.get("season", season)),
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            source=self.name,
            # gamePk distingue los dos partidos de un doubleheader; el par
            # (fecha, equipos) NO lo hace.
            source_id=str(g["gamePk"]),
            playoff=g.get("gameType", "R") != "R",
        )
