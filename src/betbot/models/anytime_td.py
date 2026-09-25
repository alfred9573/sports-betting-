"""Modelo de 'anytime TD': probabilidad de que un jugador anote al menos un TD.

QUE CUENTA COMO TD. Touchdowns de carrera y de recepcion. Los de pase NO cuentan
en este mercado (el QB anota solo si corre o recibe). Los de retorno de patada
si cuentan en muchas casas, pero no estan en las columnas que guardamos; son
~2-3% del total y casi todos de especialistas, asi que para un RB/WR/TE normal
la omision es despreciable, y para un retornador el modelo se queda corto.

POR QUE NO BASTA CON LA MEDIA DE TDs DEL JUGADOR. Los touchdowns son escasos y
muy ruidosos: un receptor que anoto en 3 de sus ultimos 5 partidos no tiene un
60% de probabilidad, tuvo suerte en el lado bueno de una moneda cargada al 25%.
Lo que si es estable es el USO (acarreos + targets): de el depende cuantas
oportunidades tiene de anotar. Por eso la tasa se estima mezclando dos cosas:

  - lambda_hist: TDs por partido del propio jugador (media movil, encogida
    hacia su posicion). Captura lo que el uso no ve: rol en zona roja.
  - lambda_uso: su uso esperado por la tasa de TD por toque de su posicion.
    Captura la oportunidad, que es lo que se repite de semana a semana.

  lambda = w * lambda_hist + (1 - w) * lambda_uso
  P(al menos 1 TD) = 1 - exp(-lambda)     (Poisson: permite partidos de 2 TDs)

El peso `w` NO se elige a ojo: se ajusta en 2010-2017 y se valida en 2018-2026.
La mejora ("skill") se mide contra la tasa de anotacion de los jugadores
ELEGIBLES de su posicion, no de toda la plantilla (ver backtest/anytime_td.py):

     w    skill 2010-2017   skill 2018-2026
   0.00        0.64%             5.15%
   0.30        3.45%             6.34%
   0.50        4.60%             6.61%
   0.70        5.08%  <- elegido 6.45%
   1.00        4.26%             5.35%

SESGO CONOCIDO, NO CORREGIDO. Por encima de ~15% el modelo se pasa: en
2018-2026 dice 34.5% y pasa 32.5%, dice 62.6% y pasa 57.8%. En 2010-2017 el
exceso era mayor (44.1% -> 38.8%). Se probaron dos correcciones:
  - fija, ajustada en 2010-2017: EMPEORA el holdout (log-loss 0.50381 ->
    0.50390) y voltea el sesgo a subestimacion (43.8% -> 49.0%), porque el
    exceso fue menguando con los anos y la correccion del pasado sobra hoy;
  - movil, recalibrando cada temporada con las 3 previas: mejora el total
    (0.50542 -> 0.50473) pero solo en 9 de 14 temporadas. No se distingue del
    azar con claridad, y la ganancia es del orden de la que ya se rechazo en
    recepciones.
Ninguna se aplica. Una probabilidad de este modelo por encima del 40% hay que
leerla como unos puntos mas baja.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

POSICIONES = frozenset({"QB", "RB", "FB", "WR", "TE"})


def tds_de(fila: dict) -> float:
    return float(fila.get("rushing_tds") or 0.0) + float(fila.get("receiving_tds") or 0.0)


def toques_de(fila: dict) -> float:
    return float(fila.get("carries") or 0.0) + float(fila.get("targets") or 0.0)


@dataclass
class AnytimeTDModel:
    name: str = "anytime_td_v1"

    peso_historial: float = 0.70
    """w: cuanto pesan los TDs propios frente al uso. Elegido en 2010-2017."""

    decay: float = 0.90
    """Semivida ~6.6 partidos, como en el resto de props: el rol cambia rapido."""

    min_partidos: int = 4
    min_toques: float = 2.0
    """Uso medio minimo. Por debajo el jugador casi no toca el balon, las casas
    rara vez lo ofrecen, y la probabilidad es ruido sobre ruido."""

    fuerza_encogimiento: float = 6.0
    """Partidos virtuales de la media posicional. Mas alto que en yardas (3.0)
    porque un TD es mucho mas ruidoso por partido que una cifra de yardas."""

    # Estado aprendido, acumulado por posicion.
    _td_pos: dict[str, list[float]] = field(default_factory=dict)      # [tds, partidos]
    _toques_pos: dict[str, list[float]] = field(default_factory=dict)  # [tds, toques]

    def _media_movil(self, valores: list[float]) -> float:
        # Mismo corte que en props: pasado ~200 partidos el peso es < 1e-9.
        corte = (
            math.ceil(math.log(1e-9) / math.log(self.decay))
            if 0.0 < self.decay < 1.0 else len(valores)
        )
        num = den = 0.0
        peso = 1.0
        for v in reversed(valores[-max(1, corte):]):
            num += peso * v
            den += peso
            peso *= self.decay
        return num / den if den > 0 else 0.0

    def tasa_posicion(self, posicion: str) -> float | None:
        """P(TD) de un jugador medio de la posicion. Es la linea base a batir."""
        acum = self._td_pos.get(posicion)
        if not acum or acum[1] < 50:
            return None
        return 1.0 - math.exp(-acum[0] / acum[1])

    def predecir(
        self, hist_tds: list[float], hist_toques: list[float], posicion: str
    ) -> float | None:
        """P(al menos un TD) en el proximo partido, con historial ANTERIOR."""
        if posicion not in POSICIONES or len(hist_tds) < self.min_partidos:
            return None
        toques_esp = self._media_movil(hist_toques)
        if toques_esp < self.min_toques:
            return None
        td_pos = self._td_pos.get(posicion)
        por_toque = self._toques_pos.get(posicion)
        if not td_pos or td_pos[1] < 50 or not por_toque or por_toque[1] <= 0:
            return None

        media_pos = td_pos[0] / td_pos[1]
        n = len(hist_tds)
        propia = self._media_movil(hist_tds)
        lambda_hist = (n * propia + self.fuerza_encogimiento * media_pos) / (
            n + self.fuerza_encogimiento
        )
        lambda_uso = toques_esp * (por_toque[0] / por_toque[1])

        w = self.peso_historial
        lam = w * lambda_hist + (1.0 - w) * lambda_uso
        return 1.0 - math.exp(-max(lam, 0.0))

    def observar(self, posicion: str, tds: float, toques: float) -> None:
        """Incorpora un partido YA jugado. Siempre despues de predecirlo."""
        if posicion not in POSICIONES:
            return
        a = self._td_pos.setdefault(posicion, [0.0, 0.0])
        a[0] += tds
        a[1] += 1
        b = self._toques_pos.setdefault(posicion, [0.0, 0.0])
        b[0] += tds
        b[1] += toques
