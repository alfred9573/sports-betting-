"""Modelo de props de jugador: proyeccion y distribucion.

QUE RESUELVE Y QUE NO. Una prop se apuesta contra una linea ("Mahomes over 275.5
yardas"), asi que no basta con proyectar la media: hace falta la DISTRIBUCION
completa, porque lo unico que importa es cuanta probabilidad queda a cada lado
de esa linea. Un modelo que acierta la media y se equivoca en la dispersion
pierde dinero igual.

Este modulo estima las dos cosas y se puede validar HOY con las 27 temporadas de
nflverse, sin necesidad de lineas historicas (que no existen). Lo que NO puede
responder es si el mercado se equivoca: eso exige comparar contra precios
reales, y esos se empiezan a archivar ahora con `betbot collect`.

POR QUE UNA DISTRIBUCION EMPIRICA Y NO UNA NORMAL. El rendimiento de un jugador
no es simetrico ni continuo: un receptor tiene una masa real de probabilidad
EXACTAMENTE en cero (no le llega el balon) y una cola derecha larga (una
recepcion de 70 yardas). Una normal alrededor de la media, que es lo que sirve
para el total de un partido, aqui asigna probabilidad a valores negativos y
subestima la cola. En vez de eso se usa la distribucion empirica del cociente
(real / proyectado), agrupada por posicion y mercado sobre partidos pasados: no
asume forma alguna, respeta el cero y respeta la cola.

CONDICIONADO A JUGAR, Y QUE SESGO QUEDA DE VERDAD. Solo hay fila para un
jugador si registro alguna estadistica, asi que el modelo esta condicionado a
que el jugador participe. Eso encaja con como se liquidan las props en la
mayoria de casas: si el jugador NO juega, la apuesta se anula y se devuelve el
dinero, de modo que el libro tampoco cobra ese riesgo. (Una version anterior de
esta nota afirmaba lo contrario —que el libro precia la baja y el modelo no— y
exageraba el sesgo.) Las reglas varian por casa; hay que comprobar la propia.

El sesgo que si queda es mas estrecho: un jugador que salta al campo pero no
registra ninguna estadistica (un receptor sin targets) puede no tener fila, y
entonces el modelo ve menos ceros de los que hay. Eso empuja la probabilidad de
'over' hacia arriba, sobre todo en jugadores de poco uso. Para titulares es
pequeno; para suplentes, no.
"""

from __future__ import annotations

import math
from bisect import bisect_left, insort
from collections.abc import Sequence
from dataclasses import dataclass, field

# Mercados que se modelan, y de que columna sale cada uno. Se limita a los que
# `collect` archiva de verdad: modelar props que no podemos cotizar no sirve.
MERCADOS: dict[str, str] = {
    "player_pass_yds": "passing_yards",
    "player_pass_tds": "passing_tds",
    "player_rush_yds": "rushing_yards",
    "player_reception_yds": "receiving_yards",
    "player_receptions": "receptions",
}

# NBA: mismas props, otra fuente. Columnas de `nba_player_games`.
MERCADOS_NBA: dict[str, str] = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_threes": "threes",
}

# Uso minimo para que un jugador sea cotizable. No es un detalle: la distribucion
# del cociente se vuelve inestable cuando la proyeccion tiende a cero (dividir
# por casi nada), y ademas ningun libro ofrece props del tercer receptor. Filtrar
# aqui evita medir el modelo en un universo que nunca vamos a apostar.
PROYECCION_MINIMA: dict[str, float] = {
    "player_pass_yds": 120.0,
    "player_pass_tds": 0.7,
    "player_rush_yds": 25.0,
    "player_reception_yds": 20.0,
    "player_receptions": 2.0,
    # NBA: por debajo de esto las casas casi nunca abren la linea.
    "player_points": 8.0,
    "player_rebounds": 3.0,
    "player_assists": 2.0,
    "player_threes": 0.8,
}


# Correccion de calibracion por mercado, en espacio logit: p' = sig(a*logit(p)).
# a<1 encoge hacia 0.5, a>1 estira. CADA VALOR SE AJUSTO EN 2010-2017 Y SE
# VALIDO EN 2018-2026; solo esta aqui el que mejoro en holdout.
#
#   player_pass_yds       a=1.10 en train -> holdout 0.6587 -> 0.6589  (empeora)
#   player_pass_tds       a=0.84 en train -> holdout 0.6630 -> 0.6608  (mejora)
#   player_rush_yds       a=1.00                                       (nada que hacer)
#   player_reception_yds  a=0.96 en train -> holdout 0.6662 -> 0.6663  (empeora)
#   player_receptions     a=0.90 en train -> holdout 0.6653 -> 0.6652  (irrelevante)
#
# Los tres mercados de yardas salen en a~1.00: el procedimiento no encuentra
# nada donde no habia defecto, que es la mejor senal de que no esta inventando.
#
# RECEPCIONES SE QUEDA SIN CORREGIR A PROPOSITO. Su mala calibracion en la cola
# alta (63.2% -> 57.8%) es real y estable en las dos eras, pero un parametro
# global no la arregla: encoger corrige el extremo y estropea los cubos medios,
# que van en sentido contrario (33.6% -> 35.1%). El defecto tiene forma, no es
# sobreconfianza uniforme. Meter el 0.90 porque "mejora" 0.0001 en log-loss,
# con el Brier idendico, seria anadir una palanca que no hace nada.
#
# NBA, mismo procedimiento (2010-2017 -> 2018-2026, 890.000 predicciones):
#
#   player_points    a=0.98 -> holdout 0.66979 -> 0.66980  (nada que hacer)
#   player_rebounds  a=0.98 -> holdout 0.66589 -> 0.66589  (nada que hacer)
#   player_assists   a=0.96 -> holdout 0.66630 -> 0.66621  (irrelevante)
#   player_threes    a=0.88 -> holdout 0.65654 -> 0.65556  (mejora)
#
# Triples es el caso limpio de "probabilidades demasiado extremas": las DOS
# colas se desvian hacia fuera, igual en ambas eras (65.0->59.8 y 65.7->59.6
# arriba; 19.7->24.3 y 19.6->24.2 abajo). Un encogimiento global es justo la
# correccion para ese defecto. Es, como touchdowns de pase, un conteo con pocos
# valores posibles: el mismo mecanismo en otro deporte.
#
# Asistencias repite el caso de recepciones: la cola alta sobreestima en las dos
# eras (63.8->59.4 y 63.8->60.1) pero los cubos medios van al reves, asi que un
# parametro global no la arregla. Se marca, no se corrige.
CALIBRACION: dict[str, float] = {
    "player_pass_tds": 0.84,
    "player_threes": 0.88,
}

# Mercados cuya probabilidad alta NO es de fiar aunque el modelo la emita. Es un
# aviso, no un filtro: lo consume quien decide si apostar.
#
# Incluye los dos mercados CORREGIDOS, porque la correccion los mejora pero no
# los deja limpios: tras aplicarla, pass_tds sigue en 66.0% -> 61.5% y threes en
# 64.0% -> 59.9%. Corregido no significa fiable arriba del 60%.
COLA_ALTA_DUDOSA: frozenset[str] = frozenset({
    "player_receptions", "player_assists",   # sin correccion posible
    "player_pass_tds", "player_threes",      # corregidos, pero no del todo
})


def _corrige(p: float, mercado: str) -> float:
    """Aplica la correccion de calibracion del mercado, si tiene."""
    a = CALIBRACION.get(mercado, 1.0)
    if a == 1.0:
        return p
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    z = math.log(p / (1.0 - p))
    return 1.0 / (1.0 + math.exp(-a * z))


@dataclass(frozen=True)
class Proyeccion:
    """Proyeccion puntual mas la distribucion empirica que la envuelve."""

    media: float
    # Ordenados: real/proyectado de casos pasados. Es una VISTA de la lista del
    # modelo, no una copia: copiarla en cada prediccion costaba O(n) con listas
    # de cientos de miles de elementos en NBA, y dominaba el tiempo del
    # backtest. Consecuencia que hay que respetar: una Proyeccion se usa ANTES
    # del siguiente `observar` del mismo mercado y posicion, no se guarda para
    # despues. El backtest y el uso en vivo la consumen al momento.
    cocientes: Sequence[float]
    n_partidos: int                # cuantos partidos del jugador la sustentan
    mercado: str = ""              # para saber que correccion de calibracion toca

    @property
    def cola_alta_dudosa(self) -> bool:
        """Si este mercado sobreestima en la cola alta pese a la correccion."""
        return self.mercado in COLA_ALTA_DUDOSA

    def prob_over(self, linea: float) -> float:
        """P(resultado > linea).

        Se trabaja en el espacio del cociente: superar la linea equivale a que
        el cociente supere linea/media. Como las lineas de props son .5 casi
        siempre, el empate exacto es raro, pero se trata igual (estrictamente
        mayor) para no regalar medio punto de probabilidad.
        """
        if self.media <= 0 or not self.cocientes:
            return 0.0
        objetivo = linea / self.media
        # Cuantos cocientes pasados quedan por DEBAJO o igual al objetivo.
        i = bisect_left(self.cocientes, objetivo)
        # bisect_left deja a la izquierda los estrictamente menores; los iguales
        # empiezan en i. Para "estrictamente mayor" hay que saltarselos tambien.
        n = len(self.cocientes)
        while i < n and self.cocientes[i] <= objetivo:
            i += 1
        p = (n - i) / n
        # Suavizado de Laplace: con muestra finita, una cola vacia daria 0.0 o
        # 1.0, y una probabilidad de 0 o 1 en una apuesta es siempre mentira.
        return _corrige((p * n + 0.5) / (n + 1.0), self.mercado)

    def prob_under(self, linea: float) -> float:
        return 1.0 - self.prob_over(linea)

    def _acumuladas(self, real: float) -> tuple[float, float]:
        """(P(X < real), P(X <= real)) sin suavizar, en el espacio del cociente."""
        n = len(self.cocientes)
        if self.media <= 0 or n == 0:
            return (0.5, 0.5)
        objetivo = real / self.media
        i = bisect_left(self.cocientes, objetivo)     # estrictamente menores
        j = i
        while j < n and self.cocientes[j] <= objetivo:
            j += 1                                   # menores o iguales
        return (i / n, j / n)

    def cuantil_del_resultado(self, real: float, u: float = 0.5) -> float:
        """Donde cae el resultado observado dentro de la distribucion predicha.

        Es la transformada integral de probabilidad (PIT). Si el modelo esta
        bien calibrado, estos valores se reparten UNIFORMEMENTE en [0,1] a lo
        largo de muchas predicciones. Es la unica forma de validar la
        distribucion entera —no solo la media— sin necesidad de lineas reales.

        VERSION ALEATORIZADA, Y NO ES UN DETALLE. El PIT clasico solo se reparte
        uniforme si la variable es continua. Los touchdowns de pase toman SIETE
        valores enteros (0 a 6): con un soporte tan grueso, la masa se amontona
        en unos pocos deciles hagas lo que hagas, y el histograma acusa al
        modelo de estar mal calibrado cuando el defecto es de la metrica. La
        correccion estandar (Dawid 1984) es repartir la masa del empate con un
        uniforme: PIT = F(x-) + u * (F(x) - F(x-)). Con `u` fijo a 0.5 se
        obtiene el punto medio, determinista; el backtest pasa un `u` sorteado
        con semilla fija para poder reproducirlo.

        NO lleva la correccion de calibracion de `prob_over`, y es deliberado:
        el PIT es el diagnostico de la distribucion CRUDA. Corregirlo aqui
        tambien seria taparse los ojos, porque el histograma dejaria de mostrar
        el defecto que la correccion intenta compensar.
        """
        menor, menor_igual = self._acumuladas(real)
        return menor + u * (menor_igual - menor)


@dataclass
class PropsModel:
    """Proyeccion por media movil exponencial con encogimiento hacia la posicion.

    La media movil pondera los partidos recientes; el encogimiento evita que un
    jugador con dos partidos buenos se proyecte como una estrella. Ambos son
    necesarios: sin recencia el modelo ignora que un titular cambio de rol, y
    sin encogimiento sobrerreacciona a muestras diminutas.
    """

    name: str = "props_v1"

    decay: float = 0.90
    """Peso por partido hacia atras. 0.90 da semivida de ~6.6 partidos: menos de
    media temporada de NFL. El rol de un jugador cambia rapido (lesiones,
    cambios de esquema), asi que la ventana util es corta."""

    min_partidos: int = 4
    """Por debajo de esto no se proyecta. Con 1-3 partidos la varianza de la
    media movil es mayor que la senal que aporta."""

    fuerza_encogimiento: float = 3.0
    """Partidos 'virtuales' de la media posicional que se anaden al jugador.
    Con 4 partidos reales, la proyeccion es 4/7 del jugador y 3/7 de su
    posicion; con 30, el jugador ya manda casi del todo."""

    min_cocientes: int = 200
    """Muestra minima de la distribucion empirica. Con menos, las colas —que es
    donde viven las lineas interesantes— son puro ruido."""

    # Estado aprendido. Nada de esto se toca desde fuera.
    # Media posicional como acumulado (suma, n) y no como lista. Con la lista,
    # cada prediccion volvia a sumar TODO el historico de la posicion: era la
    # mitad del tiempo del backtest y crecia sin limite. El acumulado da la
    # misma media en tiempo constante.
    _base_posicion: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    _cocientes: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    _memo_media: tuple | None = field(default=None, repr=False)

    # -- proyeccion ---------------------------------------------------------

    def _media_movil(self, valores: list[float]) -> float:
        """Media exponencial, recordando el ultimo calculo.

        El backtest pide la media del MISMO historial dos veces seguidas: al
        proyectar el partido y al aprender de el. Se recuerda el ultimo
        (lista, longitud, valor) y se reutiliza si coincide. La longitud forma
        parte de la clave porque el historial crece: con el mismo objeto lista
        pero un partido mas, la media es otra.
        """
        memo = self._memo_media
        if memo is not None and memo[0] is valores and memo[1] == len(valores):
            return memo[2]
        valor = self._media_movil_calculada(valores)
        self._memo_media = (valores, len(valores), valor)
        return valor

    def _media_movil_calculada(self, valores: list[float]) -> float:
        """Media exponencial; `valores` en orden cronologico (mas viejo primero).

        Solo se recorren los ultimos partidos cuyo peso todavia importa. Recorrer
        la carrera entera en cada prediccion es cuadratico en su longitud: en NFL
        (~50 partidos por jugador) no se notaba, pero en NBA hay carreras de mas
        de 1.500 partidos y el backtest pasaba de minutos a horas. Con decay 0.90
        el corte cae en ~200 partidos, donde el peso restante es < 1e-9 del
        total: el resultado es el mismo salvo en el noveno decimal.
        """
        num = den = 0.0
        peso = 1.0
        for v in reversed(valores[-self._ventana_efectiva():]):
            num += peso * v
            den += peso
            peso *= self.decay
        return num / den if den > 0 else 0.0

    def _ventana_efectiva(self) -> int:
        if not 0.0 < self.decay < 1.0:
            return 10**9   # sin decaimiento: todo pesa igual, no se puede cortar
        return max(1, math.ceil(math.log(1e-9) / math.log(self.decay)))

    def proyectar(
        self, historial: list[float], posicion: str, mercado: str
    ) -> Proyeccion | None:
        """Proyecta el proximo partido a partir de los ANTERIORES del jugador.

        `historial` debe contener solo partidos previos al que se predice. El
        modelo no tiene forma de comprobarlo, asi que esa garantia es del
        llamante; el backtest la impone consultando la base con corte temporal.
        """
        if len(historial) < self.min_partidos:
            return None
        clave = (posicion, mercado)
        propia = self._media_movil(historial)
        base = self._base_posicion.get(clave)
        media_pos = (base[0] / base[1]) if base else propia

        n = len(historial)
        media = (n * propia + self.fuerza_encogimiento * media_pos) / (
            n + self.fuerza_encogimiento
        )
        if media < PROYECCION_MINIMA.get(mercado, 0.0):
            return None

        cocientes = self._cocientes.get(clave)
        if not cocientes or len(cocientes) < self.min_cocientes:
            return None
        return Proyeccion(
            media=media, cocientes=cocientes, n_partidos=n, mercado=mercado
        )

    # -- aprendizaje --------------------------------------------------------

    def observar(
        self, historial: list[float], posicion: str, mercado: str, real: float
    ) -> None:
        """Incorpora un partido YA jugado al conocimiento del modelo.

        Se llama DESPUES de haber predicho ese partido, nunca antes: es la misma
        disciplina walk-forward del resto del sistema. El cociente que se guarda
        es real/proyectado con la proyeccion que se habria hecho en su momento.
        """
        clave = (posicion, mercado)
        acum = self._base_posicion.setdefault(clave, [0.0, 0])
        acum[0] += real
        acum[1] += 1
        if len(historial) < self.min_partidos:
            return
        media = self._media_movil(historial)
        if media < PROYECCION_MINIMA.get(mercado, 0.0):
            return
        # Insercion ordenada: `prob_over` hace busqueda binaria sobre la lista.
        # Antes se anadia al final y se reordenaba la lista entera cuando el
        # nuevo valor quedaba fuera de sitio; con las listas de NBA (cientos de
        # miles de cocientes) eso era el 40% del tiempo del backtest.
        insort(self._cocientes.setdefault(clave, []), real / media)

    def ordenar(self) -> None:
        """Deja todas las distribuciones ordenadas. Llamar antes de predecir."""
        for lista in self._cocientes.values():
            lista.sort()

    def cobertura(self) -> dict[tuple[str, str], int]:
        return {k: len(v) for k, v in self._cocientes.items()}


def log_score(p: float) -> float:
    """-log(p) del resultado que ocurrio. Menor es mejor."""
    return -math.log(max(p, 1e-12))
