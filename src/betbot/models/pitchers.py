"""Ratings empiricos de abridores para MLB, derivados de los propios game logs.

Por que hace falta: el abridor es el factor individual que mas mueve una linea
de beisbol. Un Elo de equipo puro trata igual un partido con un as en el monticulo
que uno con el quinto abridor, y el mercado no — asi que el modelo estara
sistematicamente equivocado en direcciones predecibles.

Enfoque: en vez de depender de proyecciones externas (FIP/SIERA, que habria que
comprar o raspar), se estima el rating de cada abridor con el mismo Elo, tratando
al abridor como un "equipo" adicional. Retrosheet ya entrega el abridor de cada
partido, asi que el dato esta disponible sin nuevas fuentes.

RESULTADO MEDIDO — LEER ANTES DE USAR ESTO

Se midio sobre 34.914 partidos reales (Retrosheet 2010-2025), seleccionando
parametros en 2010-2020 y validando en el holdout 2021-2025:

    sin ajuste de abridor    log-loss 0.6773
    con ajuste de abridor    log-loss 0.6769
    ganancia                        +0.0004

Es ruido. Para dimensionarlo: la ventaja TOTAL del modelo MLB sobre predecir la
tasa base es 0.0118, asi que el abridor aporta ~3% de un edge que ya era
pequeno. Y el metodo es fragil: con K alto la ganancia se vuelve negativa
(-0.0060), o sea que mal configurado hace dano.

Por que falla, pese a que los ratings SI aprenden algo real (los mejores por
ajuste son Max Fried, Crochet, Kershaw, Eovaldi — abridores genuinamente
buenos): el resultado del partido depende del bullpen y del ataque tanto como
del abridor, asi que la senal del lanzador llega ahogada. Ademas un abridor
lanza ~32 veces por temporada, muestra insuficiente para separarla del ruido.

QUE HACER CON ESTO. El modulo se conserva porque esta implementado y probado, y
porque `MLBModel.pitcher_elo` acepta cualquier fuente de ajustes. Pero para que
el abridor aporte de verdad hacen falta PROYECCIONES QUE MIDAN AL LANZADOR
DIRECTAMENTE (FIP, xFIP, SIERA) en lugar de inferirlo del resultado del equipo.
Alimenta `pitcher_elo` con eso y vuelve a medir. No inviertas mas tiempo en
refinar el metodo de inferencia por resultados: ya esta medido y no llega.

Limitaciones estructurales:
  - Un abridor lanza ~32 partidos por temporada: poca muestra, de ahi el K bajo
    y el minimo de apariciones.
  - El rating captura "como le va al equipo cuando abre este lanzador", que no
    es lo mismo que la calidad del lanzador.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

DEFAULT_RATING = 0.0  # ajuste en puntos de Elo, relativo al abridor promedio


@dataclass
class PitcherRatings:
    """Ajuste en puntos de Elo por abridor, aprendido de resultados."""

    k: float = 3.0
    """Muy bajo a proposito: ~32 aperturas por temporada y el resultado depende
    de mucho mas que del abridor. Un K alto convierte esto en un generador de
    ruido con nombre propio."""

    min_starts: int = 15
    max_adjustment: float = 60.0
    """Techo del ajuste, en puntos de Elo (~8 puntos de probabilidad). Sin techo,
    un abridor con una racha corta y afortunada acumula un ajuste absurdo que se
    traduce en EV fantasma."""

    ratings: dict[str, float] = field(default_factory=dict)
    starts: dict[str, int] = field(default_factory=dict)

    def rating(self, pitcher: str) -> float:
        """Ajuste de este abridor, o 0 si no se le conoce lo suficiente."""
        if not pitcher or self.starts.get(pitcher, 0) < self.min_starts:
            return DEFAULT_RATING
        return max(-self.max_adjustment, min(self.max_adjustment,
                                             self.ratings.get(pitcher, DEFAULT_RATING)))

    def is_reliable(self, pitcher: str) -> bool:
        return bool(pitcher) and self.starts.get(pitcher, 0) >= self.min_starts

    def update(
        self,
        home_pitcher: str,
        away_pitcher: str,
        home_won: bool,
        expected_home: float,
    ) -> None:
        """Actualiza ambos abridores tras un partido.

        `expected_home` es la probabilidad que el modelo de EQUIPO le daba al
        local. Asi el abridor solo recibe credito por lo que el modelo de equipo
        no explicaba: si un equipo favorito gana como se esperaba, su abridor
        apenas se mueve.
        """
        if not home_pitcher or not away_pitcher:
            return
        actual = 1.0 if home_won else 0.0
        delta = self.k * (actual - expected_home)
        self.ratings[home_pitcher] = self.ratings.get(home_pitcher, DEFAULT_RATING) + delta
        self.ratings[away_pitcher] = self.ratings.get(away_pitcher, DEFAULT_RATING) - delta
        self.starts[home_pitcher] = self.starts.get(home_pitcher, 0) + 1
        self.starts[away_pitcher] = self.starts.get(away_pitcher, 0) + 1

    def matchup_adjustment(self, home_pitcher: str, away_pitcher: str) -> float:
        """Diferencia neta de abridores, en puntos de Elo a favor del local."""
        return self.rating(home_pitcher) - self.rating(away_pitcher)

    def regress(self, fraction: float = 0.35) -> None:
        """Regresion a la media entre temporadas.

        Mas agresiva que la de los equipos: un lanzador cambia mas de un ano a
        otro (lesiones, edad, repertorio) que una plantilla entera.
        """
        for p, r in self.ratings.items():
            self.ratings[p] = r * (1.0 - fraction)
        self.starts = {p: 0 for p in self.starts}

    def top(self, n: int = 10) -> list[tuple[str, float, int]]:
        reliable = [
            (p, self.rating(p), self.starts[p])
            for p in self.ratings
            if self.is_reliable(p)
        ]
        return sorted(reliable, key=lambda x: x[1], reverse=True)[:n]

    @property
    def n_reliable(self) -> int:
        return sum(1 for p in self.ratings if self.is_reliable(p))


def elo_to_prob_shift(elo_points: float, scale: float = 400.0) -> float:
    """Cuanta probabilidad vale un ajuste de Elo, alrededor del 50%.

    Util para interpretar: 30 puntos de Elo son ~4,3 puntos de probabilidad.
    """
    return 1.0 / (1.0 + math.pow(10.0, -elo_points / scale)) - 0.5
