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

    min_edge: float = 0.015
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

    def sharp_probs(self, event: Event, market: Market) -> dict[str, float] | None:
        """Consenso sin vig de los libros sharp. None si no cotiza ninguno."""
        cfg = self.config
        sharps = [
            b for b in event.markets(market)
            if b.bookmaker.lower() in cfg.sharp_books
        ]
        if len(sharps) < cfg.min_sharp_books:
            return None

        acumulado: dict[str, list[float]] = {}
        for bm in sharps:
            nombres = [o.name for o in bm.outcomes]
            if len(nombres) < 2:
                continue
            try:
                justas = devig([o.decimal_odds for o in bm.outcomes], cfg.devig_method)
            except ValueError:
                continue
            for nombre, p in zip(nombres, justas, strict=True):
                acumulado.setdefault(nombre, []).append(p)

        if not acumulado:
            return None
        medias = {k: sum(v) / len(v) for k, v in acumulado.items()}
        total = sum(medias.values())
        return {k: v / total for k, v in medias.items()}

    def evaluate(self, event: Event, market: Market = Market.MONEYLINE) -> list[Signal]:
        cfg = self.config
        referencia = self.sharp_probs(event, market)
        if referencia is None:
            return []

        senales: list[Signal] = []
        for nombre, p_sharp in referencia.items():
            mejor_libro = None
            mejor_precio = 0.0
            for bm in event.markets(market):
                # NUNCA comparar un libro sharp consigo mismo: seria medir su
                # propio margen y llamarlo ventaja.
                if bm.bookmaker.lower() in cfg.sharp_books:
                    continue
                o = bm.outcome(nombre)
                if o and o.decimal_odds > mejor_precio:
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

            salida = mejor_libro.outcome(nombre)
            senales.append(
                Signal(
                    event_id=event.event_id,
                    sport=event.sport,
                    commence_time=event.commence_time,
                    matchup=f"{event.away_team} @ {event.home_team}",
                    market=market,
                    selection=nombre,
                    point=salida.point if salida else None,
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

    def scan(self, events: list[Event], market: Market = Market.MONEYLINE) -> list[Signal]:
        salida: list[Signal] = []
        for ev in events:
            salida.extend(self.evaluate(ev, market))
        return sorted(salida, key=lambda s: s.ev, reverse=True)
