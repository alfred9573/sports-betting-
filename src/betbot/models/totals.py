"""Modelo de puntuacion total, para mercados over/under.

Predecir el TOTAL es un problema distinto de predecir el GANADOR: no importa
quien gane, importa cuanto se anota entre los dos. Se estima la capacidad
ofensiva y defensiva de cada equipo en puntos, se suma lo esperado, y se compara
con la linea.

La probabilidad de superar la linea sale de una normal alrededor del total
esperado. La desviacion tipica NO se fija a ojo: se estima con los errores del
propio modelo sobre partidos ANTERIORES, en ventana expansiva. Fijarla a un
valor bonito seria meter conocimiento del futuro por la puerta de atras — y en
este mercado la sigma manda tanto como la media, porque decide cuanta
probabilidad cae a cada lado de la linea.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SQRT2 = math.sqrt(2.0)


def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    if sigma <= 0:
        return 0.5
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * SQRT2)))


@dataclass
class TeamScoring:
    puntos_a_favor: float = 0.0
    puntos_en_contra: float = 0.0
    peso: float = 0.0

    def ataque(self, media_liga: float) -> float:
        if self.peso <= 0:
            return media_liga
        return self.puntos_a_favor / self.peso

    def defensa(self, media_liga: float) -> float:
        if self.peso <= 0:
            return media_liga
        return self.puntos_en_contra / self.peso


@dataclass
class TotalsModel:
    """Modelo de puntuacion con medias ponderadas por recencia."""

    name: str = "totals_v1"
    decay: float = 0.97
    """Factor por partido. 0.97 da una semivida de ~23 partidos: en NFL,
    algo mas de una temporada."""

    min_games: int = 6
    media_liga: float = 22.0
    """Puntos por equipo y partido. Se recalcula con los datos vistos."""

    equipos: dict[str, TeamScoring] = field(default_factory=dict)
    _errores: list[float] = field(default_factory=list, repr=False)
    _n_vistos: int = 0
    _suma_puntos: float = 0.0

    def _equipo(self, nombre: str) -> TeamScoring:
        return self.equipos.setdefault(nombre, TeamScoring())

    def es_fiable(self, local: str, visitante: str) -> bool:
        return (
            self._equipo(local).peso >= self.min_games
            and self._equipo(visitante).peso >= self.min_games
        )

    def total_esperado(self, local: str, visitante: str) -> float | None:
        if not self.es_fiable(local, visitante):
            return None
        h, a = self._equipo(local), self._equipo(visitante)
        m = self.media_liga
        # Lo que anota cada uno = media de su ataque y la defensa del rival.
        puntos_local = (h.ataque(m) + a.defensa(m)) / 2.0
        puntos_visita = (a.ataque(m) + h.defensa(m)) / 2.0
        return puntos_local + puntos_visita

    @property
    def sigma(self) -> float:
        """Desviacion tipica del error, estimada solo con partidos pasados."""
        if len(self._errores) < 30:
            return 13.5  # valor de arranque tipico en NFL, hasta tener datos
        media = sum(self._errores) / len(self._errores)
        var = sum((e - media) ** 2 for e in self._errores) / (len(self._errores) - 1)
        return max(1.0, math.sqrt(var))

    def prob_over(self, local: str, visitante: str, linea: float) -> float | None:
        esperado = self.total_esperado(local, visitante)
        if esperado is None:
            return None
        # P(total > linea). Se resta 0.5 en lineas enteras para repartir el push.
        return 1.0 - normal_cdf(linea, esperado, self.sigma)

    def update(self, local: str, visitante: str, pts_local: int, pts_visita: int) -> None:
        esperado = self.total_esperado(local, visitante)
        if esperado is not None:
            self._errores.append((pts_local + pts_visita) - esperado)
            if len(self._errores) > 2000:
                self._errores = self._errores[-2000:]

        h, a = self._equipo(local), self._equipo(visitante)
        for eq, favor, contra in ((h, pts_local, pts_visita), (a, pts_visita, pts_local)):
            eq.puntos_a_favor = eq.puntos_a_favor * self.decay + favor
            eq.puntos_en_contra = eq.puntos_en_contra * self.decay + contra
            eq.peso = eq.peso * self.decay + 1.0

        self._n_vistos += 2
        self._suma_puntos += pts_local + pts_visita
        if self._n_vistos:
            self.media_liga = self._suma_puntos / self._n_vistos

    def nueva_temporada(self, regresion: float = 0.25) -> None:
        """Regresion a la media entre temporadas, igual que en el Elo."""
        m = self.media_liga
        for eq in self.equipos.values():
            if eq.peso <= 0:
                continue
            ataque = eq.ataque(m)
            defensa = eq.defensa(m)
            eq.puntos_a_favor = (ataque + regresion * (m - ataque)) * eq.peso
            eq.puntos_en_contra = (defensa + regresion * (m - defensa)) * eq.peso
