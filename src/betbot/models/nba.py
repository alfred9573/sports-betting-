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

# CALIBRADO CON DATOS REALES, y RECALIBRADO para la era moderna.
#
# Primera calibracion, sobre 2000-2015 (dataset de FiveThirtyEight), seleccion
# en 2000-2010 y holdout 2011-2015:
#
#   defaults iniciales (k=20, hfa=60):  holdout log-loss 0.6028, gap medio -3.16%
#   calibrados         (k=10, hfa=85):  holdout log-loss 0.5979, gap medio +0.89%
#
# Lo relevante alli no fue el log-loss sino el GAP: con hfa=60 los diez deciles
# tenian sesgo negativo, o sea el modelo infravaloraba al local en todo el rango.
#
# LA VENTAJA DE LOCAL HA BAJADO. Con datos de 2016-2026 (hoopR/ESPN, 14.168
# partidos) aquel hfa=85 pasa a sobreestimar al local, porque la ventaja de local
# real cayo del 60,3% de victorias (2000-2015) al 56,6% (2016-2026). Segunda
# calibracion, seleccion en 2016-2022 y holdout 2023-2026:
#
#   hfa=85 (calibrado en 2000-2015):  holdout log-loss 0.6244, gap medio +5.02%
#   hfa=65 (calibrado en 2016-2022):  holdout log-loss 0.6191, gap medio +2.22%
#
# Los defaults son los MODERNOS, porque el caso de uso es apostar partidos de
# hoy. Para reproducir los resultados historicos del README hay que pasar
# home_advantage=85 explicitamente.
#
# Queda un sesgo residual de +2,2 puntos porcentuales que no se ha eliminado: la
# ventaja de local sigue cayendo dentro del propio periodo de validacion, asi que
# cualquier constante unica llega tarde. Merece revisarse cada temporada.
NBA_ELO = EloConfig(
    k=10.0,
    home_advantage=65.0,
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
    shrink: float = 0.95
    """Encogimiento hacia 50/50 (1.0 = desactivado).

    Historia de este parametro, que ilustra por que hay que medir en vez de
    razonar: se puso a 0.90 asumiendo que un Elo crudo esta sobreconfiado en los
    extremos. Con la calibracion de 2000-2015 result0 contraproducente (estaba
    compensando un hfa mal puesto, no un defecto del Elo) y se subio a 1.0. Con
    datos modernos vuelve a aportar, pero poco: 0.95."""

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
