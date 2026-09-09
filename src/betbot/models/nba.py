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

# CALIBRADO CON DATOS REALES, no de memoria. Procedimiento: barrido de
# parametros sobre 2000-2010 (14.213 partidos, dataset Elo de FiveThirtyEight),
# seleccion por log-loss walk-forward, y validacion en el holdout 2011-2015
# (5.543 partidos nunca vistos en la seleccion).
#
#   defaults iniciales (k=20, hfa=60):  holdout log-loss 0.6028, gap medio -3.16%
#   calibrados (k=10, hfa=85):          holdout log-loss 0.5979, gap medio +0.89%
#
# Lo relevante no es el log-loss (mejora modesta) sino el GAP: con hfa=60 los
# diez deciles de calibracion tenian sesgo negativo, o sea el modelo infravaloraba
# al local en todo el rango. Un sesgo sistematico asi no se ve en las metricas
# agregadas y se traduce en apostar siempre al lado equivocado de la misma
# moneda. La ventaja de local en NBA vale ~85 puntos de Elo, no 60.
NBA_ELO = EloConfig(
    k=10.0,
    home_advantage=85.0,
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
    shrink: float = 1.0
    """Encogimiento hacia 50/50 (1.0 = desactivado).

    Se puso a 0.90 asumiendo que un Elo crudo esta sobreconfiado en los extremos.
    Los datos dicen que no: con la ventaja de local bien calibrada (85 pts), el
    encogimiento empeora el holdout — estaba compensando el sesgo de hfa=60, no
    un defecto real del Elo. Se conserva el parametro porque un modelo entrenado
    con menos historia si puede necesitarlo."""

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
