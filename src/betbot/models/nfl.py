"""Modelo NFL: Elo con margen de victoria.

CORRECCION DE UNA SUPOSICION EQUIVOCADA

Este deporte estaba pospuesto con el argumento de que "17 partidos por temporada
es demasiado poco para separar senal de ruido". Medido, resulta falso:

    NFL   7.276 partidos (1999-2025), holdout 2016-2025 (1.444 no vistos)
          log-loss 0.6254  vs baseline 0.6854   mejora +0.0600   acierto 65.6%

Esa mejora es comparable a la de NBA (+0.0778) y CINCO VECES la de MLB (+0.0116).
El futbol americano se modela bien por las mismas razones que el baloncesto:
diferencias de talento grandes entre equipos, sin empates practicamente, y
ventaja de local que pesa.

Lo que SI sigue siendo cierto del argumento original, pero es otra cosa: la NFL
ofrece ~285 partidos por temporada frente a 1.230 de NBA y 2.430 de MLB. Eso no
degrada la calidad de la prediccion — degrada la VELOCIDAD A LA QUE PUEDES
VALIDAR con CLV y ROI. Son pocas oportunidades de apuesta, no malas.

Conclusion practica: la NFL es un buen deporte para modelar y uno lento para
validar. No es la primera parada, pero tampoco el descarte que parecia.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from betbot.models.elo import EloConfig, EloRatings
from betbot.types import Event, Market, ModelProbabilities

# Calibrado sobre datos reales (nflverse 1999-2025): seleccion en 1999-2015,
# validacion en holdout 2016-2025. La configuracion es notablemente plana — la
# generica (k=20, hfa=55) y la elegida por el barrido rinden practicamente igual
# en el holdout (+0.0601 vs +0.0600), lo que sugiere que no hay mucho que afinar.
#
# regression_to_mean alta (0.33): las plantillas de NFL cambian mucho de un ano
# a otro (draft, agencia libre, lesiones), mas que en NBA o MLB.
#
# min_games=8: media temporada. Exigir 16 partidos deja fuera casi todo el
# calendario util y empeora los resultados.
NFL_ELO = EloConfig(
    k=20.0,
    home_advantage=55.0,
    initial_rating=1500.0,
    mov_multiplier=True,
    regression_to_mean=0.33,
    min_games=8,
)


@dataclass
class NFLModel:
    """Elo NFL. Mismo contrato que NBA y MLB."""

    name: str = "nfl_elo_v1"
    ratings: EloRatings = field(default_factory=lambda: EloRatings(NFL_ELO))
    shrink: float = 0.90
    """Encogimiento hacia 50/50. A diferencia de NBA — donde resulto
    contraproducente una vez calibrada la ventaja de local — aqui si ayuda
    ligeramente: con menos partidos por equipo los ratings son mas ruidosos."""

    def fit(self, games: list[dict]) -> NFLModel:
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
