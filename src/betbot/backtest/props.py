"""Backtest walk-forward del modelo de props.

QUE SE PUEDE MEDIR SIN LINEAS HISTORICAS. No existe archivo publico de lineas
pasadas de props, asi que no se puede calcular ROI contra el mercado. Lo que SI
se puede medir, y es el requisito previo, es si el modelo describe bien al
jugador: si dice "60% de que supere 275 yardas", ¿lo supera el 60% de las veces?

Dos pruebas, ambas sin mercado:

1. PIT (transformada integral de probabilidad). Se mira en que cuantil de la
   distribucion predicha cayo el resultado real. Con un modelo bien calibrado
   esos cuantiles se reparten UNIFORMEMENTE. Si se amontonan en los extremos, la
   distribucion es demasiado estrecha (el modelo se cree mas seguro de lo que
   es) y eso, apostando props, se traduce en pagar de mas por los overs y unders
   lejanos. Si se amontonan en el centro, es demasiado ancha.

2. Calibracion sobre lineas sinteticas. Se colocan lineas donde las pondria un
   libro (el medio punto mas cercano a la proyeccion, y a ±15%), se agrupan las
   predicciones por probabilidad y se compara con la frecuencia observada.

DISCIPLINA TEMPORAL. Se recorre en orden cronologico estricto: para cada
partido se predice con el historial ANTERIOR del jugador y solo despues se
incorpora el resultado. El estado compartido (medias por posicion, distribucion
de cocientes) se actualiza igual, siempre despues de predecir.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from betbot.models.props import MERCADOS, PropsModel, log_score


@dataclass
class ResultadoProps:
    mercado: str
    predicciones: int
    pit: list[float] = field(default_factory=list)
    prob_y_resultado: list[tuple[float, bool]] = field(default_factory=list)

    @property
    def pit_deciles(self) -> list[int]:
        """Cuantos resultados cayeron en cada decima de la distribucion predicha."""
        cubos = [0] * 10
        for q in self.pit:
            i = min(9, max(0, int(q * 10)))
            cubos[i] += 1
        return cubos

    @property
    def desviacion_uniforme(self) -> float:
        """Cuanto se aleja el PIT de la uniformidad, en puntos porcentuales.

        Es la desviacion media absoluta de cada decil respecto al 10% esperado.
        0 seria perfecto; por encima de ~2pp la distribucion tiene forma mala.
        """
        n = len(self.pit)
        if n == 0:
            return 0.0
        esperado = n / 10.0
        return sum(abs(c - esperado) for c in self.pit_deciles) / 10.0 / n * 100.0

    @property
    def log_score_medio(self) -> float:
        if not self.prob_y_resultado:
            return 0.0
        return sum(
            log_score(p if ocurrio else 1.0 - p)
            for p, ocurrio in self.prob_y_resultado
        ) / len(self.prob_y_resultado)

    @property
    def brier(self) -> float:
        if not self.prob_y_resultado:
            return 0.0
        return sum(
            (p - (1.0 if ocurrio else 0.0)) ** 2 for p, ocurrio in self.prob_y_resultado
        ) / len(self.prob_y_resultado)

    def calibracion(self, n_cubos: int = 10) -> list[tuple[float, float, int]]:
        """(prob media predicha, frecuencia observada, n) por cubo."""
        cubos: list[list[tuple[float, bool]]] = [[] for _ in range(n_cubos)]
        for p, ocurrio in self.prob_y_resultado:
            i = min(n_cubos - 1, max(0, int(p * n_cubos)))
            cubos[i].append((p, ocurrio))
        salida = []
        for c in cubos:
            if not c:
                continue
            pm = sum(p for p, _ in c) / len(c)
            obs = sum(1 for _, o in c if o) / len(c)
            salida.append((pm, obs, len(c)))
        return salida


MUESTRA_MINIMA_CUBO = 100


def marca_calibracion(predicho: float, observado: float, n: int) -> str:
    """Etiqueta de un cubo de calibracion: solo marca lo que no puede ser ruido.

    La version anterior marcaba cualquier hueco de mas de 5 puntos sin mirar el
    tamano del cubo, y asi un cubo de UNA sola prediccion (70% -> 100%) salia
    senalado como "desviado". Con n=1 el observado solo puede ser 0% o 100%:
    eso no es un defecto, es aritmetica. Ahora se exige muestra minima y que
    el hueco supere dos errores estandar de la proporcion, ademas de 3 puntos
    para no senalar diferencias reales pero irrelevantes en cubos enormes.
    """
    if n < MUESTRA_MINIMA_CUBO:
        return "  (muestra chica, ignorar)"
    p = min(max(predicho, 1e-6), 1 - 1e-6)
    error_estandar = (p * (1 - p) / n) ** 0.5
    if abs(predicho - observado) > max(0.03, 2 * error_estandar):
        return "  <-- desviado"
    return ""


def lineas_sinteticas(media: float) -> list[float]:
    """Donde pondria un libro la linea: medio punto cerca de la proyeccion.

    Se prueban tres: la central y ±15%. Las de los lados son las que de verdad
    interrogan las colas, que es donde un modelo mal calibrado pierde dinero.
    """
    salida = []
    for factor in (0.85, 1.0, 1.15):
        x = media * factor
        linea = round(x - 0.5) + 0.5  # medio punto, estilo casa de apuestas
        if linea > 0:
            salida.append(linea)
    return sorted(set(salida))


def walk_forward_props(
    filas: list[dict],
    mercados: tuple[str, ...] = tuple(MERCADOS),
    modelo: PropsModel | None = None,
    desde_temporada: int | None = None,
    semilla: int = 20260914,
    progreso: Callable[[int], None] | None = None,
) -> dict[str, ResultadoProps]:
    """Recorre las filas en orden cronologico y evalua antes de aprender.

    `filas` debe venir ordenada por (season, week). Cada fila necesita
    player_id, position y la columna de estadistica de cada mercado.

    `progreso` se llama con cada temporada nueva que empieza a procesarse. El
    recorrido completo tarda minutos sin producir salida, y sin esto no hay
    forma de distinguir un proceso que avanza de uno colgado.

    `desde_temporada` descarta de la EVALUACION los primeros anos, no del
    aprendizaje: las distribuciones empiricas necesitan cientos de casos antes
    de significar algo, y puntuar el modelo mientras se esta llenando mide el
    arranque en frio, no el modelo.
    """
    modelo = modelo or PropsModel()
    # Semilla fija: el PIT aleatorizado necesita un uniforme para repartir la
    # masa de los empates, pero un backtest que da un numero distinto en cada
    # corrida no se puede auditar.
    rng = random.Random(semilla)
    resultados = {m: ResultadoProps(mercado=m, predicciones=0) for m in mercados}
    # Historial por (jugador, mercado). Se mantiene en memoria para no consultar
    # la base una vez por fila: son cientos de miles de filas.
    historiales: dict[tuple[str, str], list[float]] = {}
    temporada_actual = None

    for fila in filas:
        if progreso is not None and fila["season"] != temporada_actual:
            temporada_actual = fila["season"]
            progreso(temporada_actual)
        pid = fila["player_id"]
        posicion = fila["position"] or "?"
        for mercado in mercados:
            columna = MERCADOS[mercado]
            real = float(fila.get(columna) or 0.0)
            clave = (pid, mercado)
            historial = historiales.setdefault(clave, [])

            proy = modelo.proyectar(historial, posicion, mercado)
            evaluable = proy is not None and (
                desde_temporada is None or fila["season"] >= desde_temporada
            )
            if proy is not None and evaluable:
                r = resultados[mercado]
                r.predicciones += 1
                r.pit.append(proy.cuantil_del_resultado(real, u=rng.random()))
                for linea in lineas_sinteticas(proy.media):
                    r.prob_y_resultado.append((proy.prob_over(linea), real > linea))

            # Aprender SIEMPRE despues de predecir, nunca antes.
            modelo.observar(historial, posicion, mercado, real)
            historial.append(real)

    modelo.ordenar()
    return resultados
