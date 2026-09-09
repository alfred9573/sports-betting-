"""Motor de deteccion de valor esperado y dimensionamiento de stake.

El nucleo del bot: cruza probabilidades del modelo contra el precio de mercado
ya sin vig, y emite una senal solo si el edge sobrevive a los filtros de riesgo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from betbot.odds.devig import devig
from betbot.types import Event, Market, ModelProbabilities, Signal

log = logging.getLogger(__name__)


def expected_value(prob: float, decimal_odds: float) -> float:
    """EV por unidad apostada. 0.05 = +5% de retorno esperado por unidad."""
    if not 0.0 <= prob <= 1.0:
        raise ValueError(f"probabilidad fuera de rango: {prob}")
    return prob * (decimal_odds - 1.0) - (1.0 - prob)


def kelly_fraction(prob: float, decimal_odds: float, fraction: float = 1.0) -> float:
    """Kelly (opcionalmente fraccionado). Devuelve 0 si no hay edge.

    Kelly completo maximiza el crecimiento log pero asume que la probabilidad
    del modelo es correcta. Como no lo es, se usa fraccion 0.25-0.50: reduce la
    varianza mas rapido de lo que reduce el crecimiento, y protege contra un
    modelo mal calibrado.
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f = (prob * b - (1.0 - prob)) / b
    return max(0.0, f * fraction)


@dataclass(frozen=True)
class EVConfig:
    """Filtros de riesgo. Los defaults son deliberadamente conservadores."""

    min_ev: float = 0.03              # EV minimo para alertar
    min_edge: float = 0.02            # diferencia minima modelo vs mercado
    max_odds: float = 10.0            # evita longshots: alto EV teorico, malisima calibracion
    min_odds: float = 1.20            # evita favoritisimos: ruina si el modelo falla
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.02       # tope duro por apuesta, gane quien gane el Kelly
    bankroll: float = 1000.0
    devig_method: str = "shin"
    min_books: int = 3                # consenso minimo para creerle al precio de mercado
    sharp_books: tuple[str, ...] = ("pinnacle", "betfair_ex_eu", "circasports")

    def __post_init__(self) -> None:
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction debe estar en (0, 1]")
        if self.min_odds >= self.max_odds:
            raise ValueError("min_odds debe ser menor que max_odds")


class EVEngine:
    """Convierte (Event + ModelProbabilities) en senales accionables."""

    def __init__(self, config: EVConfig | None = None) -> None:
        self.config = config or EVConfig()

    def fair_probs(self, event: Event, market: Market) -> dict[str, float] | None:
        """Probabilidad de consenso del mercado, sin vig.

        Prefiere libros sharp (Pinnacle marca la linea; los demas la copian).
        Si no hay sharp disponible, promedia las probabilidades sin vig de todos
        los libros — promediar probabilidades, nunca odds, que no es lo mismo.
        """
        books = event.markets(market)
        if not books:
            return None

        sharp = [b for b in books if b.bookmaker.lower() in self.config.sharp_books]
        pool = sharp or books
        if not sharp and len(books) < self.config.min_books:
            log.debug("evento %s: solo %d libros, insuficiente", event.event_id, len(books))
            return None

        accum: dict[str, list[float]] = {}
        for bm in pool:
            names = [o.name for o in bm.outcomes]
            if len(names) < 2:
                continue
            try:
                fair = devig([o.decimal_odds for o in bm.outcomes], self.config.devig_method)
            except ValueError:
                continue
            for name, p in zip(names, fair, strict=True):
                accum.setdefault(name, []).append(p)

        if not accum:
            return None
        avg = {k: sum(v) / len(v) for k, v in accum.items()}
        total = sum(avg.values())
        return {k: v / total for k, v in avg.items()}

    def evaluate(self, event: Event, model: ModelProbabilities) -> list[Signal]:
        """Evalua todos los lados de un mercado y devuelve las senales que pasan filtros."""
        if model.event_id != event.event_id:
            raise ValueError(
                f"event_id no coincide: {model.event_id} vs {event.event_id}"
            )

        fair = self.fair_probs(event, model.market)
        if fair is None:
            return []

        cfg = self.config
        signals: list[Signal] = []

        for name, p_model in model.probs.items():
            p_fair = fair.get(name)
            if p_fair is None:
                log.debug("outcome '%s' no cotizado en %s", name, event.event_id)
                continue

            best = event.best_price(model.market, name)
            if best is None:
                continue
            book, outcome = best
            odds = outcome.decimal_odds

            if not cfg.min_odds <= odds <= cfg.max_odds:
                continue

            edge = p_model - p_fair
            if edge < cfg.min_edge:
                continue

            ev = expected_value(p_model, odds)
            if ev < cfg.min_ev:
                continue

            kelly = kelly_fraction(p_model, odds, cfg.kelly_fraction)
            stake_pct = min(kelly, cfg.max_stake_pct)
            if stake_pct <= 0:
                continue

            signals.append(
                Signal(
                    event_id=event.event_id,
                    sport=event.sport,
                    commence_time=event.commence_time,
                    matchup=f"{event.away_team} @ {event.home_team}",
                    market=model.market,
                    selection=name,
                    point=outcome.point,
                    bookmaker=book,
                    decimal_odds=odds,
                    model_prob=p_model,
                    fair_prob=p_fair,
                    ev=ev,
                    edge=edge,
                    kelly_stake=stake_pct,
                    stake_units=round(stake_pct * cfg.bankroll, 2),
                    model_name=model.model_name,
                )
            )

        return sorted(signals, key=lambda s: s.ev, reverse=True)

    def scan(
        self, events: list[Event], predictions: dict[str, ModelProbabilities]
    ) -> list[Signal]:
        """Corre el motor sobre un lote de eventos. Ignora eventos sin prediccion."""
        out: list[Signal] = []
        for ev in events:
            pred = predictions.get(ev.event_id)
            if pred is None:
                continue
            out.extend(self.evaluate(ev, pred))
        return sorted(out, key=lambda s: s.ev, reverse=True)
