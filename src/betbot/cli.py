"""CLI del bot.

  python -m betbot.cli doctor                        # diagnostico antes de operar
  python -m betbot.cli demo                          # pipeline con datos sinteticos
  python -m betbot.cli ingest --sport nba --from 2000 --to 2015
  python -m betbot.cli backtest --sport nba --holdout 2011
  python -m betbot.cli scan --sport nba
  python -m betbot.cli close                         # cron cada 10-15 min
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

    model, err = load_trained_model(sport, args.games_db)
    if model is None:
        print(err, file=sys.stderr)
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


def cmd_ingest(args: argparse.Namespace) -> int:
    """Descarga resultados historicos y los guarda normalizados."""
    from betbot.ingest.store import GameStore

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}", file=sys.stderr)
        return 2

    source = _make_source(sport, args.source)
    if source is None:
        print(f"No hay fuente historica para {sport.name}.", file=sys.stderr)
        return 2

    store = GameStore(args.db)
    total_new = 0
    print(f"Fuente: {source.name} | temporadas {args.start}-{args.end}")

    for season in range(args.start, args.end + 1):
        if not args.force and store.season_is_done(sport, source.name, season):
            print(f"  {season}: ya ingerida (usa --force para rehacerla)")
            continue
        try:
            games = source.fetch_season(season)
        except Exception as e:  # noqa: BLE001
            print(f"  {season}: ERROR {e}", file=sys.stderr)
            continue
        new = store.upsert_many(games)
        store.mark_season_done(sport, source.name, season, len(games))
        total_new += new
        skipped = len(getattr(source, "skipped", []))
        note = f" | {skipped} descartados" if skipped else ""
        print(f"  {season}: {len(games)} partidos, {new} nuevos{note}")

    print(f"\nTotal nuevo: {total_new} | en BD: {store.count(sport)} partidos de {sport.name}")

    # El aviso se basa en partidos REALMENTE descartados, no en fallos de lookup:
    # una fuente puede intentar varios campos y resolver por el segundo. Avisar
    # por cada intento fallido genera alarmas falsas que se acaban ignorando, que
    # es justo como se cuela despues una perdida de datos de verdad.
    dropped = getattr(source, "skipped", [])
    if dropped:
        muestra = sorted(dict.fromkeys(dropped))[:10]
        print(
            f"\nAVISO: {len(dropped)} partidos DESCARTADOS por equipo no reconocido.\n"
            f"  {muestra}\n"
            "  Anadelos a ingest/teams.py: hasta entonces no entran al entrenamiento."
        )
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Walk-forward sobre los datos ya ingeridos. Sin fuga temporal."""
    from betbot.backtest.walkforward import walk_forward_elo
    from betbot.ingest.store import GameStore

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}", file=sys.stderr)
        return 2

    factory = _model_factory(sport)
    if factory is None and not sport.value.startswith("soccer"):
        print(f"No hay modelo Elo para {sport.name}.", file=sys.stderr)
        return 2

    store = GameStore(args.db)
    rows = store.training_rows(sport)
    if not rows:
        print(f"No hay datos de {sport.name}. Corre primero: "
              f"betbot ingest --sport {args.sport}", file=sys.stderr)
        return 1

    print(f"{len(rows)} partidos | {rows[0]['date']} -> {rows[-1]['date']}\n")
    if sport.value.startswith("soccer"):
        # El futbol tiene tres resultados: metricas multiclase (RPS), no binarias.
        from betbot.backtest.walkforward import walk_forward_soccer
        from betbot.models.soccer import PoissonSoccerModel
        result = walk_forward_soccer(rows, lambda: PoissonSoccerModel.for_league(sport))
    else:
        result = walk_forward_elo(rows, factory)
    print(result)

    if not result.beats_baseline:
        print("\nEl modelo NO le gana a predecir la tasa base. No lo uses para apostar.")
    return 0


def load_trained_model(sport, games_db: str = "data/games.db"):
    """Carga el modelo del deporte ENTRENADO con los resultados ya ingeridos.

    Devuelve (modelo, None) o (None, mensaje_de_error). Nunca devuelve un modelo
    sin entrenar: apostar con ratings a 1500 para todos es peor que no apostar.
    """
    from betbot.ingest.store import GameStore

    store = GameStore(games_db)
    rows = store.training_rows(sport)
    if not rows:
        alias = next((k for k, v in SPORT_ALIASES.items() if v is sport), sport.name)
        return None, (
            f"No hay datos historicos de {sport.name} en {games_db}.\n"
            f"Corre primero:  python -m betbot.cli ingest --sport {alias}"
        )

    if sport.value.startswith("soccer"):
        from betbot.models.soccer import PoissonSoccerModel
        model = PoissonSoccerModel.for_league(sport)
    else:
        factory = _model_factory(sport)
        if factory is None:
            return None, f"No hay modelo implementado para {sport.name}."
        model = factory()

    model.fit(rows)
    return model, None


def model_freshness(sport, games_db: str = "data/games.db") -> tuple[int | None, str | None]:
    """Dias desde el ultimo partido ingerido. Es el chequeo que evita el fallo
    mas silencioso de todos: escanear partidos de hoy con ratings de hace anos.
    """
    from datetime import date

    from betbot.ingest.store import GameStore

    rows = GameStore(games_db).iter_games(sport)
    if not rows:
        return None, None
    last = rows[-1]["game_date"]
    return (date.today() - date.fromisoformat(last)).days, last


def _make_source(sport, name: str | None = None):
    """Fuente historica del deporte. `name` elige explicitamente cual.

    Por defecto se usa la fuente con MAS HISTORIA de cada deporte, que suele ser
    un dataset estatico. Pero esos datasets se congelan: el de NBA termina en
    2015. Para temporadas recientes hay que pedir `--source espn` de forma
    explicita, y por eso este parametro tiene que respetarse.
    """
    from betbot.types import Sport

    if name == "espn":
        from betbot.ingest.sources.espn import ESPN_PATHS, ESPNScoreboard
        if sport not in ESPN_PATHS:
            return None
        return ESPNScoreboard(sport)

    if sport is Sport.NBA:
        from betbot.ingest.sources.fivethirtyeight_nba import FiveThirtyEightNBA
        return FiveThirtyEightNBA()
    if sport is Sport.MLB:
        from betbot.ingest.sources.retrosheet import Retrosheet
        return Retrosheet()
    if sport is Sport.SOCCER_EPL:
        from betbot.ingest.sources.soccer_csv import EngSoccerData
        return EngSoccerData()
    if sport is Sport.NFL:
        from betbot.ingest.sources.nfl_nflverse import NFLverse
        return NFLverse()
    if sport is Sport.SOCCER_LIGA_MX:
        from betbot.ingest.sources.ligamx_csv import LigaMX
        return LigaMX()
    return None


def _model_factory(sport):
    from betbot.types import Sport

    if sport is Sport.NBA:
        return NBAModel
    if sport is Sport.MLB:
        from betbot.models.mlb import MLBModel
        return MLBModel
    if sport is Sport.NFL:
        from betbot.models.nfl import NFLModel
        return NFLModel
    return None


def cmd_close(args: argparse.Namespace) -> int:
    """Captura la linea de cierre de las senales que estan por comenzar.

    Pensado para cron cada 10-15 minutos:
        */10 * * * * cd /ruta && python -m betbot.cli close
    """
    from betbot.closing import ClosingCapture
    from betbot.odds.the_odds_api import TheOddsAPI

    settings = Settings.from_env()
    if not settings.odds_api_key:
        print("Falta ODDS_API_KEY.", file=sys.stderr)
        return 2

    store = SignalStore(settings.db_path)
    provider = TheOddsAPI(settings.odds_api_key, regions=settings.regions)
    capture = ClosingCapture(store, provider, EVEngine(settings.ev_config()),
                             window_minutes=args.window)
    report = capture.run()
    print(report)
    if provider.credits_remaining is not None:
        print(f"\nCuota restante en The Odds API: {provider.credits_remaining}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnostico completo antes de operar. No consume cuota salvo con --api.

    Comprueba, en este orden: configuracion, datos historicos, FRESCURA de esos
    datos, y conectividad real con The Odds API. La frescura es la que importa
    mas y la que nadie mira: un modelo entrenado con datos que terminan hace
    anos escanea partidos de hoy con ratings obsoletos y no da ningun error.
    """
    from betbot.types import Sport

    settings = Settings.from_env()
    problems: list[str] = []
    warnings: list[str] = []

    print("=== CONFIGURACION ===")
    print(f"  ODDS_API_KEY      {'definida' if settings.odds_api_key else 'FALTA'}")
    print(f"  Telegram          {'activo' if settings.telegram_enabled else 'no configurado'}")
    print(f"  bankroll          {settings.bankroll:.2f}")
    print(f"  min_ev            {settings.min_ev:.1%}")
    print(f"  kelly_fraction    {settings.kelly_fraction:.2f}")
    print(f"  max_stake_pct     {settings.max_stake_pct:.1%}")
    if not settings.odds_api_key:
        problems.append("Falta ODDS_API_KEY: copia .env.example a .env y rellenala.")

    print("\n=== DATOS HISTORICOS Y MODELOS ===")
    sports = [Sport.NBA, Sport.NFL, Sport.MLB, Sport.SOCCER_EPL, Sport.SOCCER_LIGA_MX]
    any_data = False
    for sport in sports:
        alias = next((k for k, v in SPORT_ALIASES.items() if v is sport), sport.name)
        days, last = model_freshness(sport, args.games_db)
        if days is None:
            print(f"  {sport.name:<16} sin datos    (betbot ingest --sport {alias})")
            continue
        any_data = True
        model, err = load_trained_model(sport, args.games_db)
        estado = "modelo OK" if model else "MODELO NO CARGA"
        if not model:
            problems.append(f"{sport.name}: {err}")
        if days > 365:
            marca = "OBSOLETO"
            warnings.append(
                f"{sport.name}: el ultimo partido ingerido es de hace {days} dias "
                f"({last}). Los ratings no reflejan las plantillas actuales — "
                f"NO escanees en vivo con esto."
            )
        elif days > 30:
            marca = "desactualizado"
            warnings.append(f"{sport.name}: {days} dias sin actualizar datos.")
        else:
            marca = "al dia"
        print(f"  {sport.name:<16} ultimo {last} ({days}d, {marca}) | {estado}")

    if not any_data:
        problems.append("No hay ningun dato historico ingerido todavia.")

    print("\n=== SENALES Y CLV ===")
    store = SignalStore(settings.db_path)
    abiertas = store.open_signals()
    liquidadas = store.settled_signals()
    sin_cierre = store.missed_close()
    print(f"  senales abiertas       {len(abiertas)}")
    print(f"  senales liquidadas     {len(liquidadas)}")
    print(f"  sin cierre capturado   {len(sin_cierre)}")
    if sin_cierre:
        warnings.append(
            f"{len(sin_cierre)} senales sin linea de cierre: no son evaluables por "
            f"CLV. Programa `betbot close` en cron cada 10-15 minutos."
        )

    if args.api:
        print("\n=== THE ODDS API (consume 1 credito) ===")
        if not settings.odds_api_key:
            print("  omitido: falta la API key")
        else:
            from betbot.odds.the_odds_api import OddsAPIError, TheOddsAPI

            provider = TheOddsAPI(settings.odds_api_key, regions=settings.regions)
            try:
                events = provider.fetch_events(Sport.NBA, [Market.MONEYLINE])
                print(f"  conexion OK | {len(events)} eventos NBA")
                print(f"  cuota restante: {provider.credits_remaining}")
                if events:
                    ev = events[0]
                    print(f"  ejemplo: {ev.away_team} @ {ev.home_team} "
                          f"({len(ev.books)} cotizaciones)")
                    nombres = {o.name for b in ev.books for o in b.outcomes}
                    print(f"  nombres de equipo en el feed: {sorted(nombres)[:4]}")
                    print("  ^ COMPRUEBA que coinciden con los de tu BD historica.")
                if provider.credits_remaining is not None and provider.credits_remaining < 100:
                    warnings.append(
                        f"Cuota baja: {provider.credits_remaining} requests restantes."
                    )
            except OddsAPIError as e:
                problems.append(f"The Odds API no responde: {e}")
                print(f"  ERROR: {e}")
    else:
        print("\n=== THE ODDS API ===")
        print("  omitido (usa --api para probar la conexion, cuesta 1 credito)")

    print()
    if problems:
        print("PROBLEMAS QUE BLOQUEAN LA OPERACION:")
        for x in problems:
            print(f"  - {x}")
    if warnings:
        print("AVISOS:")
        for x in warnings:
            print(f"  - {x}")
    if not problems and not warnings:
        print("Todo en orden.")
    return 1 if problems else 0


def cmd_validate_source(args: argparse.Namespace) -> int:
    """Prueba de humo de una fuente EN VIVO sobre una sola fecha.

    Existe porque dos adaptadores (ESPN y MLB StatsAPI) tienen el parseo cubierto
    por tests contra payloads fijados, pero nunca se han ejecutado contra la API
    real: el entorno donde se desarrollaron tiene esos hosts bloqueados. Antes de
    bajar una temporada entera conviene comprobar una fecha.
    """
    from datetime import date, datetime

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}", file=sys.stderr)
        return 2

    source = _make_source(sport, args.source or "espn")
    if source is None:
        print(f"La fuente '{args.source or 'espn'}' no cubre {sport.name}.",
              file=sys.stderr)
        return 2

    if args.date:
        try:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Fecha invalida: {args.date} (usa YYYY-MM-DD)", file=sys.stderr)
            return 2
    else:
        day = date.today()

    print(f"Fuente: {source.name} | deporte: {sport.name} | fecha: {day}\n")

    fetch_day = getattr(source, "fetch_day", None)
    if fetch_day is None:
        print("Esta fuente no soporta consulta por dia (es un dataset estatico).",
              file=sys.stderr)
        return 2

    try:
        games = fetch_day(day)
    except Exception as e:  # noqa: BLE001 - queremos el diagnostico, no el traceback
        print(f"FALLO la consulta: {type(e).__name__}: {e}\n")
        print("Causas probables:")
        print("  - sin conexion o el host esta bloqueado en tu red")
        print("  - la API cambio de formato -> revisa _parse_event en la fuente")
        return 1

    skipped = getattr(source, "skipped", [])
    unresolved = sorted(getattr(getattr(source, "registry", None), "unresolved", set()))

    print(f"  partidos obtenidos : {len(games)}")
    print(f"  descartados        : {len(skipped)}")
    print(f"  equipos sin alias  : {unresolved if unresolved else 'ninguno'}")

    for g in games[:5]:
        print(f"    {g.game_date} {g.away_team} @ {g.home_team} "
              f"{g.away_score}-{g.home_score}")

    print()
    if not games and not skipped:
        print("Sin partidos ese dia. No es un fallo: prueba otra fecha en "
              "temporada.")
        return 0
    if unresolved:
        print("PROBLEMA: hay equipos sin alias. Esos partidos NO entran al")
        print("entrenamiento. Anadelos a src/betbot/ingest/teams.py y repite.")
        return 1

    print("Fuente validada. Ya puedes bajar temporadas completas:")
    alias = next((k for k, v in SPORT_ALIASES.items() if v is sport), sport.name)
    print(f"  python -m betbot.cli ingest --sport {alias} --source espn "
          f"--from {day.year if day.month > 6 else day.year - 1} --to {day.year}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from betbot.backtest.metrics import clv_summary

    settings = Settings.from_env()
    store = SignalStore(settings.db_path)
    settled = store.settled_signals()
    open_ = store.open_signals()
    missed = store.missed_close()

    print(f"Senales abiertas: {len(open_)}")

    # El CLV se puede evaluar en TODAS las senales con cierre, esten liquidadas
    # o no: no hace falta esperar al resultado del partido. Esa es justamente su
    # ventaja practica sobre el ROI.
    with_close = [s for s in (settled + open_) if s.get("closing_odds")]
    if with_close:
        print()
        print(clv_summary(with_close))
    else:
        print("\nSin odds de cierre registradas: no se puede evaluar CLV.")
        print("Programa `betbot close` en cron cada 10-15 minutos.")

    if missed:
        print(f"\n{len(missed)} senales se quedaron sin cierre capturado "
              f"(no evaluables por CLV).")

    if not settled:
        print("\nTodavia no hay senales liquidadas: sin ROI aun.")
        return 0

    print()
    print(roi_summary(settled))
    print("\nRecuerda: con muestras cortas el CLV manda sobre el ROI.")
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
    p_scan.add_argument("--games-db", default="data/games.db",
                        help="BD de resultados historicos para entrenar el modelo")
    p_scan.set_defaults(func=cmd_scan)

    p_ing = sub.add_parser("ingest", help="descarga resultados historicos")
    p_ing.add_argument("--sport", default="nba", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_ing.add_argument("--from", dest="start", type=int, default=2000)
    p_ing.add_argument("--to", dest="end", type=int, default=2015)
    p_ing.add_argument("--source", default=None,
                       help="'espn' para temporadas recientes; por defecto, el "
                            "dataset historico de cada deporte")
    p_ing.add_argument("--db", default="data/games.db")
    p_ing.add_argument("--force", action="store_true", help="rehacer temporadas ya ingeridas")
    p_ing.set_defaults(func=cmd_ingest)

    p_bt = sub.add_parser("backtest", help="walk-forward sobre los datos ingeridos")
    p_bt.add_argument("--sport", default="nba", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_bt.add_argument("--db", default="data/games.db")
    p_bt.set_defaults(func=cmd_backtest)

    p_doc = sub.add_parser("doctor", help="diagnostico: config, datos, frescura, API")
    p_doc.add_argument("--api", action="store_true",
                       help="probar tambien la conexion con The Odds API (1 credito)")
    p_doc.add_argument("--games-db", default="data/games.db")
    p_doc.set_defaults(func=cmd_doctor)

    p_val = sub.add_parser("validate-source",
                           help="prueba de humo de una fuente en vivo (ESPN)")
    p_val.add_argument("--sport", default="nba", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_val.add_argument("--date", default=None, help="YYYY-MM-DD (def. hoy)")
    p_val.add_argument("--source", default="espn")
    p_val.set_defaults(func=cmd_validate_source)

    p_close = sub.add_parser("close", help="captura lineas de cierre (cron cada 10-15 min)")
    p_close.add_argument("--window", type=int, default=30,
                         help="minutos antes del inicio a capturar (def. 30)")
    p_close.set_defaults(func=cmd_close)

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
