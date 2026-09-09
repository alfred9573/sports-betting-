"""Modelo MLB: Elo + expectativa Pythagorean, con ajuste de pitcher abridor.

Por que MLB es el mejor banco de pruebas: 2430 partidos por temporada. Con ese
volumen se puede medir calibracion de verdad (un ROI del 3% sobre 200 apuestas
no distingue skill de suerte; sobre 2000 si empieza a hacerlo).

La contra: el abridor mueve la linea mas que cualquier otro factor individual
—entre 20 y 40 puntos de probabilidad de victoria entre un as y un quinto
abridor— asi que un Elo puro de equipo esta estructuralmente incompleto. Aqui
se modela como un ajuste explicito en puntos de Elo, no como una constante.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from betbot.models.elo import EloConfig, EloRatings
from betbot.types import Event, Market, ModelProbabilities

# K muy bajo: 162 partidos y altisima varianza por partido. Reaccionar fuerte a
# resultados individuales en beisbol es ajustar a ruido.
MLB_ELO = EloConfig(
    k=4.0,
    home_advantage=24.0,   # ventaja de local en MLB ~54%, la mas baja de las 4 ligas
    initial_rating=1500.0,
    mov_multiplier=False,  # el margen de carreras dice poco del talento real
    regression_to_mean=0.30,
    min_games=25,
)


def pythagorean_expectation(runs_scored: int, runs_allowed: int, exp: float = 1.83) -> float:
    """Win% esperado de James. El exponente 1.83 es el ajuste empirico para MLB.

    Predice el record futuro mejor que el record actual: filtra la suerte en
    partidos de una carrera, que es puro ruido.
    """
    if runs_scored <= 0 and runs_allowed <= 0:
        return 0.5
    rs, ra = runs_scored**exp, runs_allowed**exp
    return rs / (rs + ra)


@dataclass
class MLBModel:
    """Blend de Elo y Pythagorean, mas ajuste por abridor."""

    name: str = "mlb_elo_pythag_v1"
    ratings: EloRatings = field(default_factory=lambda: EloRatings(MLB_ELO))
    runs: dict[str, tuple[int, int]] = field(default_factory=dict)  # team -> (RS, RA)
    pythag_weight: float = 0.35
    shrink: float = 0.92
    prob_cap: float = 0.70
    """Techo duro de probabilidad. El beisbol es el deporte de liga mas aleatorio
    que existe: el mejor equipo le gana al peor ~65-70% de las veces, y el
    favorito mas grande de una temporada rara vez pasa de -280 (73%). Cualquier
    modelo que devuelva 80% esta sobreajustando a una racha, y ese exceso se
    traduce directamente en EV fantasma — a odds de 2.00, creer 80% en vez de
    68% inventa 24 puntos de EV que no existen. El techo es la ultima defensa
    contra que un bug de datos se convierta en una apuesta."""
    pitcher_elo: dict[str, float] = field(default_factory=dict)
    """Ajuste en puntos de Elo por abridor (nombre -> puntos), relativo a un
    abridor promedio. Un as vale ~+35, uno de relleno ~-30. Se alimenta desde
    fuera (proyecciones tipo FIP/SIERA); vacio = sin ajuste."""

    starting_pitchers: dict[str, tuple[str, str]] = field(default_factory=dict)
    """event_id -> (abridor_local, abridor_visitante). Sin entrada, el ajuste es 0."""

    def fit(self, games: list[dict]) -> MLBModel:
        for g in games:
            if g.get("new_season"):
                self.ratings.new_season()
                self.runs.clear()
            home, away = g["home"], g["away"]
            hs, as_ = g["home_score"], g["away_score"]
            self.ratings.update(home, away, hs, as_)
            h_rs, h_ra = self.runs.get(home, (0, 0))
            a_rs, a_ra = self.runs.get(away, (0, 0))
            self.runs[home] = (h_rs + hs, h_ra + as_)
            self.runs[away] = (a_rs + as_, a_ra + hs)
        return self

    def _pythag_prob(self, home: str, away: str) -> float | None:
        """Prob. de local derivada de la fuerza Pythagorean relativa (log5)."""
        if home not in self.runs or away not in self.runs:
            return None
        ph = pythagorean_expectation(*self.runs[home])
        pa = pythagorean_expectation(*self.runs[away])
        denom = ph * (1 - pa) + pa * (1 - ph)
        if denom == 0:
            return 0.5
        base = ph * (1 - pa) / denom
        return min(0.95, base + 0.04)  # +4pp por localia, ~54% global de MLB

    def predict(self, event: Event) -> ModelProbabilities | None:
        home, away = event.home_team, event.away_team
        if not (self.ratings.is_reliable(home) and self.ratings.is_reliable(away)):
            return None

        home_sp, away_sp = self.starting_pitchers.get(event.event_id, ("", ""))
        adj_home = self.pitcher_elo.get(home_sp, 0.0)
        adj_away = self.pitcher_elo.get(away_sp, 0.0)

        diff = (
            self.ratings.rating(home) + MLB_ELO.home_advantage + adj_home
            - self.ratings.rating(away) - adj_away
        )
        p_elo = 1.0 / (1.0 + 10.0 ** (-diff / MLB_ELO.scale))

        p_pyth = self._pythag_prob(home, away)
        if p_pyth is None:
            p_home = p_elo
        else:
            w = self.pythag_weight
            p_home = (1 - w) * p_elo + w * p_pyth

        p_home = 0.5 + (p_home - 0.5) * self.shrink
        p_home = min(self.prob_cap, max(1.0 - self.prob_cap, p_home))

        return ModelProbabilities(
            event_id=event.event_id,
            market=Market.MONEYLINE,
            probs={home: p_home, away: 1.0 - p_home},
            model_name=self.name,
            meta={
                "p_elo": round(p_elo, 4),
                "p_pythag": round(p_pyth, 4) if p_pyth is not None else None,
                "sp_adj": round(adj_home - adj_away, 1),
            },
        )
