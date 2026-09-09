"""Registro canonico de un partido terminado, comun a todas las fuentes.

Cada fuente (Retrosheet, 538, MLB StatsAPI, ESPN...) trae su propio formato.
Todas se normalizan a `GameResult` antes de tocar la base de datos, de forma que
los modelos nunca sepan de donde salio el dato.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from betbot.types import Sport


@dataclass(frozen=True)
class GameResult:
    """Un partido terminado, con nombres de equipo YA canonicalizados."""

    sport: Sport
    game_date: date
    season: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    source: str
    source_id: str
    neutral_site: bool = False
    playoff: bool = False
    extra: dict | None = None

    def __post_init__(self) -> None:
        if self.home_score < 0 or self.away_score < 0:
            raise ValueError(f"marcador negativo: {self.home_score}-{self.away_score}")
        if self.home_team == self.away_team:
            raise ValueError(f"un equipo no puede jugar contra si mismo: {self.home_team}")
        if not self.source_id:
            raise ValueError("source_id es obligatorio para la deduplicacion")

    @property
    def home_won(self) -> bool:
        return self.home_score > self.away_score

    @property
    def is_draw(self) -> bool:
        return self.home_score == self.away_score

    def to_training_dict(self) -> dict:
        """Formato que consumen los `fit()` de los modelos."""
        return {
            "home": self.home_team,
            "away": self.away_team,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "neutral": self.neutral_site,
            "date": self.game_date.isoformat(),
            "season": self.season,
        }


@runtime_checkable
class HistoricalSource(Protocol):
    """Fuente de resultados historicos."""

    name: str
    sport: Sport

    def fetch_season(self, season: int) -> list[GameResult]:
        ...
