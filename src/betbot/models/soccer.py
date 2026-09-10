"""Modelo de futbol: Poisson bivariado con ajuste Dixon-Coles.

Estima fuerza de ataque y defensa por equipo a partir de goles (o xG, que es
mejor: xG predice resultados futuros mejor que los goles reales, porque los
goles son una muestra pequenisima de un proceso ruidoso).

El ajuste Dixon-Coles corrige el defecto conocido del Poisson independiente:
subestima 0-0 y 1-1 y sobreestima 1-0 y 0-1. En 1X2 eso se traduce en
subestimar el empate ~2-3 puntos porcentuales, que a odds de 3.40 es
exactamente el rango donde el bot creeria ver valor donde no lo hay.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from betbot.types import Event, Market, ModelProbabilities, Sport

log = logging.getLogger(__name__)

MAX_GOALS = 10  # trunca la cola; P(>10 goles) < 1e-5 con lambdas realistas
DRAW = "Draw"


# PARAMETROS POR LIGA. No es un adorno: cada liga tiene su propia ventaja de
# local y su propia tendencia al empate, y usar los de otra introduce sesgo
# sistematico. Medido entre Premier League y Liga MX: los parametros ingleses
# aplicados a Mexico sobreestiman al local 2,8 puntos porcentuales e
# infravaloran al visitante 3,0. El RPS apenas cambia (+0.0003 en holdout,
# indistinguible de ruido con 756 partidos), pero el error de calibracion se
# reduce a la mitad (2,83% -> 1,53%) — y un sesgo sistematico significa apostar
# siempre al lado equivocado de la misma moneda.
#
# Cada entrada es (home_advantage, rho, decay), calibrada walk-forward con
# seleccion en un periodo temprano y validacion en holdout posterior.
LEAGUE_PRESETS: dict[Sport, tuple[float, float, float]] = {
    # Premier League, RECALIBRADA para la era moderna. Igual que en la NBA, los
    # parametros ajustados con datos antiguos sobreestiman al local.
    #
    # Primera calibracion, 8.360 partidos de 1995-2016 (engsoccerdata):
    #   (1.44, -0.28)  ->  holdout 2010-2017: RPS 0.2012
    #
    # El futbol moderno tiene menos ventaja de local y menos empates: las tasas
    # base pasaron de 46,6/25,8/27,6 (1995-2016) a 44,6/23,3/32,1 (2016-2026).
    # Con 3.830 partidos de openfootball (2016-2026), seleccion 2016-2022 y
    # holdout 2023-2026:
    #   (1.44, -0.28)  ->  holdout RPS 0.2036, |gap|max 5.85%
    #   (1.28, -0.10)  ->  holdout RPS 0.2011, |gap|max 2.90%
    #
    # El rho sube de -0.28 a -0.10 porque hay menos empates que corregir. Se
    # adoptan los modernos porque el caso de uso es apostar partidos de hoy.
    Sport.SOCCER_EPL: (1.28, -0.10, 0.0030),
    # 2.049 partidos (2018-2024). Seleccion 2018-2021, holdout 2022-2024.
    # Muestra pequena: tomar estos valores como preliminares.
    Sport.SOCCER_LIGA_MX: (1.28, -0.18, 0.0030),
}


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dixon_coles_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Correccion de dependencia para marcadores bajos (0-0, 1-0, 0-1, 1-1)."""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


@dataclass
class TeamStrength:
    attack: float = 1.0   # goles marcados relativos a la media de la liga
    defense: float = 1.0  # goles concedidos relativos a la media (menor = mejor)
    matches: int = 0


@dataclass
class PoissonSoccerModel:
    """Poisson/Dixon-Coles. Se alimenta con goles o, preferiblemente, con xG."""

    name: str = "soccer_poisson_dc_v1"
    league_avg_goals: float = 1.35   # goles por equipo por partido (tipico top-5)

    # CALIBRADOS con 8.360 partidos reales de Premier League (engsoccerdata),
    # seleccion walk-forward en 1995-2009 y validacion en holdout 2010-2017:
    #
    #   defaults iniciales (1.30 / -0.13 / 0.0065)  holdout RPS 0.2039, |gap|max 2.29%
    #   calibrados         (1.44 / -0.28 / 0.0030)  holdout RPS 0.2012, |gap|max 1.87%
    #
    # Los dos criterios coinciden, que es la mejor senal de que no es overfitting:
    # la misma combinacion da el mejor RPS Y la mejor calibracion por clase
    # (|gap|max de 0.08% en train, practicamente perfecta).
    home_advantage: float = 1.44     # multiplicador de lambda del local

    rho: float = -0.28
    """Dixon-Coles; mas negativo aumenta la probabilidad de empate.

    Se escribio -0.13 de memoria (cercano al valor del paper original sobre
    datos ingleses de los 90). Medido sobre datos reales hace falta MAS DEL
    DOBLE: con -0.13 el empate quedaba infravalorado 2.2-2.9 puntos
    porcentuales en todas las configuraciones probadas. A cuota 3.40 eso es
    exactamente el rango donde el bot creeria ver valor en el empate sin que
    lo haya."""

    decay: float = 0.0030
    """Ponderacion exponencial por antiguedad (~1 semivida por 231 dias).

    El 0.0065 inicial (semivida ~107 dias) olvidaba demasiado rapido: con
    memoria mas larga el RPS mejora de forma consistente en todo el barrido."""
    min_matches: int = 8
    strengths: dict[str, TeamStrength] = field(default_factory=dict)

    @classmethod
    def for_league(cls, sport: Sport, **kwargs) -> PoissonSoccerModel:
        """Modelo con los parametros calibrados de esa liga.

        Si la liga no tiene preset, usa los de la Premier League y avisa: son
        los mejor validados que hay (8.360 partidos), pero aplicarlos a otra
        liga sin calibrar arrastra el sesgo documentado arriba.
        """
        preset = LEAGUE_PRESETS.get(sport)
        if preset is None:
            log.warning(
                "sin preset calibrado para %s: se usan los de Premier League. "
                "Calibra con `betbot backtest` antes de apostar esta liga.",
                sport.name,
            )
            preset = LEAGUE_PRESETS[Sport.SOCCER_EPL]
        hfa, rho, decay = preset
        return cls(home_advantage=hfa, rho=rho, decay=decay, **kwargs)

    def fit(self, matches: list[dict]) -> PoissonSoccerModel:
        """Estima fuerzas por medias ponderadas por recencia.

        `matches`: dicts con home, away, home_goals, away_goals y opcional
        `days_ago` para el decaimiento temporal. Se aceptan xG en lugar de goles.

        Nota: esto es un estimador de momentos, no maxima verosimilitud. Es
        suficiente para validar el pipeline; para produccion conviene sustituirlo
        por un MLE sobre la verosimilitud Dixon-Coles completa.
        """
        acc: dict[str, dict[str, float]] = {}
        total_w = 0.0
        total_goals = 0.0

        for m in matches:
            w = math.exp(-self.decay * m.get("days_ago", 0))
            home, away = m["home"], m["away"]
            hg, ag = float(m["home_goals"]), float(m["away_goals"])
            for team, scored, conceded in ((home, hg, ag), (away, ag, hg)):
                d = acc.setdefault(team, {"gf": 0.0, "ga": 0.0, "w": 0.0, "n": 0})
                d["gf"] += w * scored
                d["ga"] += w * conceded
                d["w"] += w
                d["n"] += 1
            total_w += 2 * w
            total_goals += w * (hg + ag)

        if total_w == 0:
            return self
        league_avg = total_goals / total_w
        self.league_avg_goals = league_avg

        for team, d in acc.items():
            if d["w"] == 0:
                continue
            self.strengths[team] = TeamStrength(
                attack=(d["gf"] / d["w"]) / league_avg if league_avg else 1.0,
                defense=(d["ga"] / d["w"]) / league_avg if league_avg else 1.0,
                matches=int(d["n"]),
            )
        return self

    def expected_goals(self, home: str, away: str) -> tuple[float, float] | None:
        h = self.strengths.get(home)
        a = self.strengths.get(away)
        if h is None or a is None:
            return None
        if h.matches < self.min_matches or a.matches < self.min_matches:
            return None
        lam_home = h.attack * a.defense * self.league_avg_goals * self.home_advantage
        lam_away = a.attack * h.defense * self.league_avg_goals
        return max(0.05, lam_home), max(0.05, lam_away)

    def score_matrix(self, lam_home: float, lam_away: float) -> list[list[float]]:
        m = [
            [
                _poisson_pmf(x, lam_home)
                * _poisson_pmf(y, lam_away)
                * _dixon_coles_tau(x, y, lam_home, lam_away, self.rho)
                for y in range(MAX_GOALS + 1)
            ]
            for x in range(MAX_GOALS + 1)
        ]
        total = sum(sum(row) for row in m)
        return [[c / total for c in row] for row in m]

    def predict(self, event: Event) -> ModelProbabilities | None:
        lams = self.expected_goals(event.home_team, event.away_team)
        if lams is None:
            return None
        matrix = self.score_matrix(*lams)

        p_home = sum(matrix[x][y] for x in range(MAX_GOALS + 1) for y in range(x))
        p_draw = sum(matrix[i][i] for i in range(MAX_GOALS + 1))
        p_away = 1.0 - p_home - p_draw

        return ModelProbabilities(
            event_id=event.event_id,
            market=Market.MONEYLINE,
            probs={
                event.home_team: p_home,
                DRAW: p_draw,
                event.away_team: max(0.0, p_away),
            },
            model_name=self.name,
            meta={"xg_home": round(lams[0], 2), "xg_away": round(lams[1], 2)},
        )

    def total_goals_probs(self, event: Event, line: float = 2.5) -> dict[str, float] | None:
        """Over/Under. Util porque los mercados de totales suelen tener menos
        vig y menos atencion de sharps que el 1X2."""
        lams = self.expected_goals(event.home_team, event.away_team)
        if lams is None:
            return None
        matrix = self.score_matrix(*lams)
        under = sum(
            matrix[x][y]
            for x in range(MAX_GOALS + 1)
            for y in range(MAX_GOALS + 1)
            if x + y < line
        )
        return {"Under": under, "Over": 1.0 - under}
