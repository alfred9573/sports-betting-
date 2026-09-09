"""CLI del bot.

  python -m betbot.cli demo            # pipeline completo con datos sinteticos
  python -m betbot.cli scan --sport nba
  python -m betbot.cli report
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta

from betbot.alerts.console import ConsoleAlerter
from betbot.alerts.telegram import TelegramAlerter
from betbot.backtest.metrics import roi_summary
from betbot.config import Settings
from betbot.ev.engine import EVEngine
from betbot.models.nba import NBAModel
from betbot.storage import SignalStore
from betbot.types import BookMarket, Event, Market, Outcome, Sport

log = logging.getLogger("betbot")

SPORT_ALIASES = {
    "nba": Sport.NBA,
    "mlb": Sport.MLB,
    "nfl": Sport.NFL,
    "ligamx": Sport.SOCCER_LIGA_MX,
    "epl": Sport.SOCCER_EPL,
    "laliga": Sport.SOCCER_LA_LIGA,
    "seriea": Sport.SOCCER_SERIE_A,
    "bundesliga": Sport.SOCCER_BUNDESLIGA,
    "ligue1": Sport.SOCCER_LIGUE_1,
    "ucl": Sport.SOCCER_UCL,
}


def _alerters(settings: Settings):
    out = [ConsoleAlerter()]
    if settings.telegram_enabled:
        out.append(TelegramAlerter(settings.telegram_bot_token, settings.telegram_chat_id))
    return out


def cmd_demo(args: argparse.Namespace) -> int:
    """Ejercita odds -> modelo -> EV -> alerta sin tocar la red ni gastar cuota."""
    settings = Settings.from_env()
    engine = EVEngine(settings.ev_config())

    model = NBAModel()
    # Historial sintetico: el local gana el 70% de sus partidos.
    games = []
    for i in range(40):
        home_win = i % 10 < 7
        games.append(
            {
                "home": "Boston Celtics" if i % 2 == 0 else "Miami Heat",
                "away": "Miami Heat" if i % 2 == 0 else "Boston Celtics",
                "home_score": 112 if home_win else 100,
                "away_score": 100 if home_win else 112,
            }
        )
    model.fit(games)

    now = datetime.now(UTC)
    event = Event(
        event_id="demo-001",
        sport=Sport.NBA,
        commence_time=now + timedelta(hours=6),
        home_team="Boston Celtics",
        away_team="Miami Heat",
        books=(
            BookMarket("pinnacle", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.80), Outcome("Miami Heat", 2.15)), now),
            BookMarket("draftkings", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.74), Outcome("Miami Heat", 2.25)), now),
            BookMarket("fanduel", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.77), Outcome("Miami Heat", 2.10)), now),
        ),
    )

    pred = model.predict(event)
    if pred is None:
        print("El modelo no tiene datos suficientes para este evento.")
        return 1

    print(f"Modelo: {pred.model_name} {pred.meta}")
    print("Probabilidades del modelo: " +
          ", ".join(f"{k} {v:.1%}" for k, v in pred.probs.items()))
    fair = engine.fair_probs(event, Market.MONEYLINE)
    print("Mercado sin vig (Pinnacle): " +
          ", ".join(f"{k} {v:.1%}" for k, v in (fair or {}).items()))

    signals = engine.evaluate(event, pred)
    for alerter in _alerters(settings):
        alerter.send(signals)

    if signals and not args.no_store:
        stored = SignalStore(settings.db_path).save_many(signals)
        print(f"Guardadas {stored} senales en {settings.db_path}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Escaneo real contra The Odds API. Consume cuota."""
    from betbot.odds.the_odds_api import OddsAPIError, TheOddsAPI

    settings = Settings.from_env()
    if not settings.odds_api_key:
        print("Falta ODDS_API_KEY. Copia .env.example a .env y rellenala.", file=sys.stderr)
        return 2

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}. Opciones: {sorted(SPORT_ALIASES)}",
              file=sys.stderr)
        return 2

    provider = TheOddsAPI(settings.odds_api_key, regions=settings.regions)
    try:
        events = provider.fetch_events(sport, [Market.MONEYLINE])
    except OddsAPIError as e:
        print(f"Error consultando odds: {e}", file=sys.stderr)
        return 1

    print(f"{len(events)} eventos obtenidos. Cuota restante: {provider.credits_remaining}")

    # NOTA: aqui falta cargar un modelo ENTRENADO con resultados historicos reales.
    # Sin `fit()` sobre datos reales, predict() devuelve None por el filtro
    # min_games y el escaneo no produce senales — que es el comportamiento
    # correcto y deliberado: no se apuesta con un modelo sin entrenar.
    model = NBAModel() if sport is Sport.NBA else None
    if model is None:
        print(f"Todavia no hay modelo cableado para {sport.name}.", file=sys.stderr)
        return 2

    engine = EVEngine(settings.ev_config())
    preds = {}
    for ev in events:
        p = model.predict(ev)
        if p is not None:
            preds[ev.event_id] = p

    if not preds:
        print("Ningun evento tiene modelo con datos suficientes. "
              "Entrena el modelo con resultados historicos antes de escanear.")
        return 0

    signals = engine.scan(events, preds)
    store = SignalStore(settings.db_path)
    fresh = [s for s in signals
             if not store.already_alerted(s.event_id, s.market.value, s.selection)]
    for alerter in _alerters(settings):
        alerter.send(fresh)
    store.save_many(fresh)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    store = SignalStore(settings.db_path)
    settled = store.settled_signals()
    open_ = store.open_signals()

    print(f"Senales abiertas: {len(open_)}")
    if not settled:
        print("Todavia no hay senales liquidadas.")
        return 0
    print(roi_summary(settled))

    clvs = [
        (1 / s["closing_odds"]) - (1 / s["decimal_odds"])
        for s in settled
        if s.get("closing_odds")
    ]
    if clvs:
        avg = sum(clvs) / len(clvs)
        beat = sum(1 for c in clvs if c > 0) / len(clvs)
        print(f"\nCLV medio {avg:+.3%} sobre {len(clvs)} apuestas "
              f"| le ganaste al cierre en {beat:.1%}")
        print("CLV medio positivo y estable > cualquier ROI de muestra corta.")
    else:
        print("\nSin odds de cierre registradas: no se puede evaluar CLV.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="betbot", description="Bot de senales de apuestas por EV")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_demo = sub.add_parser("demo", help="pipeline completo con datos sinteticos")
    p_demo.add_argument("--no-store", action="store_true", help="no escribir en la BD")
    p_demo.set_defaults(func=cmd_demo)

    p_scan = sub.add_parser("scan", help="escaneo real (consume cuota de la API)")
    p_scan.add_argument("--sport", default="nba", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_scan.set_defaults(func=cmd_scan)

    p_report = sub.add_parser("report", help="ROI y CLV de las senales guardadas")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
