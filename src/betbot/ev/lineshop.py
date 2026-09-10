"""Deteccion de valor por DESACUERDO ENTRE CASAS, sin usar modelo propio.

POR QUE EXISTE ESTE MODULO

La simulacion contra lineas de cierre reales dejo claro que el modelo propio no
le gana al mercado: Brier 0.2209 frente a 0.2110 del mercado sobre 5.166
partidos de NFL. Intentar predecir mejor que un mercado liquido es competir
contra gente que se dedica a eso a tiempo completo, con datos de lesiones,
alineaciones y flujo de dinero que aqui no hay.

Pero hay otra fuente de ventaja que NO exige ser mas listo que el mercado:

    que una casa blanda tarde en mover su linea cuando la sharp ya se movio

Pinnacle y los exchanges marcan el precio; las casas de cara al publico lo
copian con retraso, y mientras tanto ofrecen precios que ya estan desfasados.
Apostar ahi no es predecir mejor: es cobrar la diferencia entre dos precios
publicados a la vez por dos sitios distintos.

DONDE ES MAS FUERTE ESTA ESTRATEGIA: en totales y handicap, no en moneyline.

Medido sobre 5.107 partidos de NFL, el mercado de over/under tiene un Brier de
0.2501 frente al 0.2500 de predecir siempre la tasa base. O sea: el mercado NO
predice mejor que una moneda al aire, porque la casa coloca la linea exactamente
donde el resultado es 50/50. En handicap igual (el local cubre el 49,0%).

Si no hay nada que predecir, lo unico que queda es el precio — y ahi la
diferencia entre casas es enorme: acertando el 50%, un -110 pierde 4,5% y un
+105 gana 2,5%. Siete puntos de swing sin predecir nada. La ventaja que podria
sacar el mejor modelo del mundo en ese mercado es CERO.

LA TRAMPA DE LOS MERCADOS CON LINEA: dos casas pueden ofrecer el mismo mercado
con LINEAS DISTINTAS (45.5 en una, 46 en otra). Comparar sus precios como si
fueran lo mismo es un error grave: no son la misma apuesta. Aqui solo se comparan
precios sobre la MISMA linea. Se pierde algo de cobertura, pero la alternativa
—interpolar entre lineas— mete un error de estimacion en el sitio exacto donde
se supone que esta la ventaja.

QUE HACE FALTA PARA QUE ESTO FUNCIONE
  - Al menos un libro sharp cotizando (Pinnacle, Betfair, Circa).
  - Al menos un libro blando con precio distinto.
  - Rapidez: la ventana se cierra en minutos, a veces segundos.
  - Y sobre todo: que las casas blandas te dejen seguir apostando. Limitan a los
    ganadores, y esta estrategia es la que mas rapido te marca.

LIMITACION HONESTA: esto NO se ha podido backtestear. Haria falta historico de
odds de varias casas simultaneas y no existe gratis. La unica forma de saber si
funciona es medir el CLV en vivo, que es justo para lo que esta montada la
captura de cierres.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from betbot.ev.engine import expected_value, kelly_fraction
from betbot.odds.devig import devig
from betbot.types import Event, Market, Signal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineShopConfig:
    """Filtros de la estrategia de desacuerdo entre casas."""

    min_ev: float = 0.02
    """Umbral mas bajo que el del modelo (3%): aqui la referencia es el precio
    sharp, no una estimacion propia, asi que un margen pequeno ya es real."""

    min_edge: float = 0.005
    """Suelo de ruido en puntos de probabilidad, NO el filtro principal.

    Ojo con subir esto: `edge` y `ev` no son criterios independientes, sino la
    misma cantidad escalada por la cuota (EV = edge x cuota). Un min_edge del
    1,5% a cuota 2.05 exige un EV del 3,1%, o sea que anula al min_ev del 2% y
    rechaza exactamente el caso que esta estrategia busca: +105 frente a -110 en
    un mercado 50/50, que son 2,5% de EV limpio. El filtro economico es min_ev;
    esto solo descarta diferencias que son ruido de redondeo."""
    max_odds: float = 15.0
    min_odds: float = 1.10
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.02
    bankroll: float = 1000.0
    devig_method: str = "shin"
    sharp_books: tuple[str, ...] = (
        "pinnacle", "betfair_ex_eu", "betfair_ex_uk", "circasports", "betonlineag",
    )
    min_sharp_books: int = 1


class LineShopEngine:
    """Emite senales cuando una casa ofrece peor precio del que marca la sharp.

    No usa modelo. La probabilidad de referencia sale de los libros sharp, sin
    vig; la apuesta va al libro blando que mas se haya quedado atras.
    """

    def __init__(self, config: LineShopConfig | None = None) -> None:
        self.config = config or LineShopConfig()

    def sharp_probs(
        self, event: Event, market: Market
    ) -> dict[tuple[str, float | None], float] | None:
        """Consenso sin vig de los libros sharp, indexado por (resultado, linea).

        La LINEA forma parte de la clave: "Over 45.5" y "Over 46" llevan el
        mismo nombre y son apuestas distintas. Mezclarlas compararia precios de
        cosas diferentes justo donde se supone que esta la ventaja.
        """
        cfg = self.config
        sharps = [
            b for b in event.markets(market)
            if b.bookmaker.lower() in cfg.sharp_books
        ]
        if len(sharps) < cfg.min_sharp_books:
            return None

        acumulado: dict[tuple[str, float | None], list[float]] = {}
        for bm in sharps:
            if len(bm.outcomes) < 2:
                continue
            try:
                justas = devig([o.decimal_odds for o in bm.outcomes], cfg.devig_method)
            except ValueError:
                continue
            for salida, p in zip(bm.outcomes, justas, strict=True):
                acumulado.setdefault((salida.name, salida.point), []).append(p)

        if not acumulado:
            return None
        return {k: sum(v) / len(v) for k, v in acumulado.items()}

    def evaluate(self, event: Event, market: Market = Market.MONEYLINE) -> list[Signal]:
        cfg = self.config
        referencia = self.sharp_probs(event, market)
        if referencia is None:
            return []

        senales: list[Signal] = []
        for (nombre, linea), p_sharp in referencia.items():
            mejor_libro = None
            mejor_precio = 0.0
            for bm in event.markets(market):
                # NUNCA comparar un libro sharp consigo mismo: seria medir su
                # propio margen y llamarlo ventaja.
                if bm.bookmaker.lower() in cfg.sharp_books:
                    continue
                for o in bm.outcomes:
                    # Misma seleccion Y MISMA LINEA, o no es la misma apuesta.
                    if (
                        o.name == nombre
                        and o.point == linea
                        and o.decimal_odds > mejor_precio
                    ):
                        mejor_precio = o.decimal_odds
                        mejor_libro = bm

            if mejor_libro is None or not cfg.min_odds <= mejor_precio <= cfg.max_odds:
                continue

            # La ventaja es la diferencia entre lo que paga el libro blando y lo
            # que el sharp dice que vale ese resultado.
            p_implicita = 1.0 / mejor_precio
            edge = p_sharp - p_implicita
            if edge < cfg.min_edge:
                continue

            ev = expected_value(p_sharp, mejor_precio)
            if ev < cfg.min_ev:
                continue

            frac = min(
                kelly_fraction(p_sharp, mejor_precio, cfg.kelly_fraction),
                cfg.max_stake_pct,
            )
            if frac <= 0:
                continue

            senales.append(
                Signal(
                    event_id=event.event_id,
                    sport=event.sport,
                    commence_time=event.commence_time,
                    matchup=f"{event.away_team} @ {event.home_team}",
                    market=market,
                    selection=nombre,
                    point=linea,
                    bookmaker=mejor_libro.bookmaker,
                    decimal_odds=mejor_precio,
                    model_prob=p_sharp,
                    fair_prob=p_sharp,
                    ev=ev,
                    edge=edge,
                    kelly_stake=frac,
                    stake_units=round(frac * cfg.bankroll, 2),
                    model_name="lineshop_v1",
                )
            )

        return sorted(senales, key=lambda s: s.ev, reverse=True)

    def scan(
        self, events: list[Event], markets: tuple[Market, ...] | None = None
    ) -> list[Signal]:
        """Escanea varios mercados. Por defecto los tres: moneyline, handicap y
        totales — con estos dos ultimos por delante, que es donde el argumento
        de esta estrategia es mas fuerte."""
        if markets is None:
            markets = (Market.TOTALS, Market.SPREAD, Market.MONEYLINE)
        salida: list[Signal] = []
        for ev in events:
            for m in markets:
                salida.extend(self.evaluate(ev, m))
        return sorted(salida, key=lambda s: s.ev, reverse=True)
