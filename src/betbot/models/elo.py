"""Elo generico con margen de victoria y ventaja de local.

Base compartida por NBA y MLB. La constante K y el factor de margen se calibran
por deporte (ver nba.py / mlb.py): un Elo con K de futbol aplicado a la NBA
sobre-reacciona a cada partido y produce probabilidades demasiado extremas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class EloConfig:
    k: float = 20.0
    home_advantage: float = 60.0     # en puntos de Elo
    initial_rating: float = 1500.0
    scale: float = 400.0             # 400 pts de Elo = 10:1 en odds
    mov_multiplier: bool = True      # ajustar K por margen de victoria
    regression_to_mean: float = 0.25  # fraccion que vuelve a 1500 entre temporadas
    min_games: int = 10              # partidos minimos antes de confiar en el rating


@dataclass
class EloRatings:
    """Estado mutable de ratings. Se actualiza partido a partido."""

    config: EloConfig = field(default_factory=EloConfig)
    ratings: dict[str, float] = field(default_factory=dict)
    games_played: dict[str, int] = field(default_factory=dict)

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.config.initial_rating)

    def is_reliable(self, team: str) -> bool:
        return self.games_played.get(team, 0) >= self.config.min_games

    def win_prob(self, home: str, away: str, neutral: bool = False) -> float:
        """Probabilidad de que gane el local (sin empates)."""
        hfa = 0.0 if neutral else self.config.home_advantage
        diff = self.rating(home) + hfa - self.rating(away)
        return 1.0 / (1.0 + 10.0 ** (-diff / self.config.scale))

    def update(
        self,
        home: str,
        away: str,
        home_score: int,
        away_score: int,
        neutral: bool = False,
    ) -> tuple[float, float]:
        """Actualiza ambos ratings tras un partido. Devuelve los nuevos valores."""
        expected_home = self.win_prob(home, away, neutral)
        if home_score > away_score:
            actual_home = 1.0
        elif home_score < away_score:
            actual_home = 0.0
        else:
            actual_home = 0.5

        k = self.config.k
        if self.config.mov_multiplier:
            k *= self._mov_factor(home, away, home_score, away_score, neutral)

        delta = k * (actual_home - expected_home)
        new_home = self.rating(home) + delta
        new_away = self.rating(away) - delta

        self.ratings[home] = new_home
        self.ratings[away] = new_away
        self.games_played[home] = self.games_played.get(home, 0) + 1
        self.games_played[away] = self.games_played.get(away, 0) + 1
        return new_home, new_away

    def _mov_factor(
        self, home: str, away: str, hs: int, as_: int, neutral: bool
    ) -> float:
        """Multiplicador de margen (formato FiveThirtyEight).

        Escala con el margen pero se amortigua cuando el favorito gana como se
        esperaba, evitando que las palizas de un equipo ya fuerte inflen su rating.
        """
        margin = abs(hs - as_)
        if margin == 0:
            return 1.0
        hfa = 0.0 if neutral else self.config.home_advantage
        diff = self.rating(home) + hfa - self.rating(away)
        winner_diff = diff if hs > as_ else -diff
        return math.log(margin + 1.0) * (2.2 / (winner_diff * 0.001 + 2.2))

    def new_season(self) -> None:
        """Regresion a la media entre temporadas. Sin esto el modelo arrastra
        rosters que ya no existen."""
        r = self.config.regression_to_mean
        base = self.config.initial_rating
        for team, rating in self.ratings.items():
            self.ratings[team] = rating + r * (base - rating)
        self.games_played = {t: 0 for t in self.games_played}
