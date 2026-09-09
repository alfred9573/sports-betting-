"""Captura de la linea de cierre para medir CLV.

POR QUE ESTO ES LO MAS URGENTE DEL PROYECTO

El CLV (closing line value) compara el precio que conseguiste contra el precio
al que el mercado cerro. Es el mejor indicador temprano que existe de si tienes
ventaja real, y la razon es estadistica, no filosofica:

  - El ROI necesita del orden de 4400 apuestas para separarse de cero a dos
    sigmas con un edge del 3%. Son varias temporadas.
  - El CLV da senal con 100-200 apuestas, porque el precio de cierre es una
    estimacion muchisimo menos ruidosa del resultado que el resultado mismo.

Un CLV medio positivo y estable con ROI todavia negativo significa "vas bien,
aguanta la varianza". Un ROI positivo con CLV negativo significa "tuviste
suerte, esto no se sostiene". Sin capturar el cierre no puedes distinguir los
dos casos, y esa es exactamente la decision que hay que tomar en los primeros
meses.

EL DETALLE QUE IMPORTA: hay que tomar el precio LO MAS CERCA POSIBLE del inicio.
Este job esta pensado para correr en cron cada 10-15 minutos; captura todo lo
que arranque dentro de la ventana y aun no tenga cierre registrado.

Se guardan DOS referencias por senal:
  - `closing_odds`: el precio final en el MISMO libro donde salio la senal. Mide
    si le ganaste a ese libro concreto.
  - `closing_fair_prob`: la probabilidad de consenso sin vig al cierre. Es el
    benchmark mas honesto: un libro blando puede dejar la linea quieta y hacerte
    creer que acertaste cuando el mercado real se movio en tu contra.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from betbot.ev.engine import EVEngine
from betbot.storage import SignalStore
from betbot.types import Market, Sport

log = logging.getLogger(__name__)


@dataclass
class CaptureReport:
    checked: int = 0
    captured: int = 0
    no_event: int = 0
    no_price: int = 0
    missed: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"Cierres capturados: {self.captured}/{self.checked} senales pendientes",
        ]
        if self.no_event:
            lines.append(f"  {self.no_event} sin evento en el feed (¿ya empezo?)")
        if self.no_price:
            lines.append(f"  {self.no_price} sin precio para esa seleccion")
        if self.errors:
            lines.append(f"  {len(self.errors)} errores: {self.errors[:3]}")
        if self.missed:
            lines.append(
                f"\n  AVISO: {self.missed} senales quedaron sin cierre y su partido\n"
                f"  ya empezo. Esas apuestas no se podran evaluar por CLV nunca.\n"
                f"  Si el numero crece, el job no corre con frecuencia suficiente."
            )
        return "\n".join(lines)


class ClosingCapture:
    """Captura precios de cierre para las senales que estan por comenzar.

    El proveedor de odds se inyecta para que esto sea testeable sin red: en
    produccion es TheOddsAPI, en tests un doble.
    """

    def __init__(
        self,
        store: SignalStore,
        provider,
        engine: EVEngine | None = None,
        window_minutes: int = 30,
    ) -> None:
        self.store = store
        self.provider = provider
        self.engine = engine or EVEngine()
        self.window_minutes = window_minutes

    def run(self, sports: list[Sport] | None = None) -> CaptureReport:
        report = CaptureReport()
        pending = self.store.pending_close(self.window_minutes)
        report.checked = len(pending)
        if not pending:
            report.missed = len(self.store.missed_close())
            return report

        # Un fetch por deporte, no uno por senal: la cuota de la API se cuenta
        # por llamada y varias senales comparten evento y deporte.
        needed = {s["sport"] for s in pending}
        if sports:
            needed &= {s.value for s in sports}

        events_by_id: dict[str, object] = {}
        for sport_value in needed:
            try:
                sport = Sport(sport_value)
            except ValueError:
                report.errors.append(f"deporte desconocido: {sport_value}")
                continue
            try:
                for ev in self.provider.fetch_events(sport, [Market.MONEYLINE]):
                    events_by_id[ev.event_id] = ev
            except Exception as e:  # noqa: BLE001 - un deporte caido no aborta el resto
                log.error("fallo obteniendo odds de %s: %s", sport_value, e)
                report.errors.append(f"{sport_value}: {e}")

        for sig in pending:
            event = events_by_id.get(sig["event_id"])
            if event is None:
                report.no_event += 1
                continue

            try:
                market = Market(sig["market"])
            except ValueError:
                report.errors.append(f"mercado invalido: {sig['market']}")
                continue

            price = self._price_at_book(event, market, sig["selection"], sig["bookmaker"])
            if price is None:
                report.no_price += 1
                continue

            fair = self.engine.fair_probs(event, market) or {}
            self.store.record_closing_odds(
                sig["event_id"],
                sig["selection"],
                price,
                fair.get(sig["selection"]),
            )
            report.captured += 1

        report.missed = len(self.store.missed_close())
        return report

    @staticmethod
    def _price_at_book(event, market: Market, selection: str, bookmaker: str) -> float | None:
        """Precio en el MISMO libro de la senal; si ya no cotiza, el mejor disponible.

        El fallback importa: un libro que retira el mercado antes del inicio no
        debe hacernos perder la observacion de CLV entera.
        """
        for bm in event.markets(market):
            if bm.bookmaker == bookmaker:
                outcome = bm.outcome(selection)
                if outcome:
                    return outcome.decimal_odds
        best = event.best_price(market, selection)
        return best[1].decimal_odds if best else None
