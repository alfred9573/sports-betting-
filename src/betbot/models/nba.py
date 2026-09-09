"""Modelo NBA: Elo con margen de victoria + ratings ofensivo/defensivo.

Por que la NBA primero: 1230 partidos por temporada regular, sin empates,
mercados moneyline liquidos y un Elo simple ya alcanza ~66-68% de acierto y
log-loss competitivo. Es el terreno correcto para validar el pipeline antes de
meterse con futbol (empates, baja anotacion) o NFL (17 partidos, puro ruido).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from betbot.models.elo import EloConfig, EloRatings
from betbot.types import Event, Market, ModelProbabilities

# K bajo: 82 partidos dan mucha senal, no hace falta reaccionar fuerte a cada uno.
# HFA ~2.5 puntos de spread; la ventaja de local en NBA cayo de ~3.5 a ~2.5 pts
# en la ultima decada, y 60 pts de Elo (~1.5-2 pts) es el rango post-2020.
NBA_ELO = EloConfig(
    k=20.0,
    home_advantage=60.0,
    initial_rating=1500.0,
    mov_multiplier=True,
    regression_to_mean=0.25,
    min_games=10,
)


@dataclass
class NBAModel:
    """Elo NBA. `ratings` se entrena con `fit()` sobre resultados historicos."""

    name: str = "nba_elo_v1"
    ratings: EloRatings = field(default_factory=lambda: EloRatings(NBA_ELO))
    shrink: float = 0.90
    """Encogimiento hacia 50/50. Un Elo crudo esta sistematicamente
    sobreconfiado en los extremos; sin esto se generan senales fantasma en
    favoritos de -400 que el mercado ya tiene bien valorados."""

    def fit(self, games: list[dict]) -> NBAModel:
        """Entrena en orden cronologico.

        `games`: dicts con home, away, home_score, away_score y opcional
        `new_season` (bool) para disparar la regresion a la media.
        """
        for g in games:
            if g.get("new_season"):
                self.ratings.new_season()
            self.ratings.update(
                g["home"], g["away"], g["home_score"], g["away_score"],
                neutral=g.get("neutral", False),
            )
        return self

    def predict(self, event: Event) -> ModelProbabilities | None:
        home, away = event.home_team, event.away_team
        if not (self.ratings.is_reliable(home) and self.ratings.is_reliable(away)):
            return None

        p_home = self.ratings.win_prob(home, away)
        p_home = 0.5 + (p_home - 0.5) * self.shrink

        return ModelProbabilities(
            event_id=event.event_id,
            market=Market.MONEYLINE,
            probs={home: p_home, away: 1.0 - p_home},
            model_name=self.name,
            meta={
                "elo_home": round(self.ratings.rating(home), 1),
                "elo_away": round(self.ratings.rating(away), 1),
            },
        )
