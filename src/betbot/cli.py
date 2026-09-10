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
    # Miniliga sintetica de cuatro equipos con resultados equilibrados, para que
    # los ratings se queden cerca de 1500 y el modelo produzca una ventaja
    # MODESTA. Es deliberado: un demo que enseñe un EV del 28% educa mal, porque
    # en un escaneo real un EV asi es casi siempre un bug (nombres de equipo que
    # no casan, devig roto o un modelo sin calibrar), no una oportunidad.
    equipos = ["Boston Celtics", "Miami Heat", "New York Knicks", "Chicago Bulls"]
    games = []
    for vuelta in range(12):
        for i, local in enumerate(equipos):
            visitante = equipos[(i + 1 + vuelta % 3) % len(equipos)]
            if local == visitante:
                continue
            # El local gana ~58% de las veces: la ventaja de local real de la NBA.
            gana_local = (vuelta * 4 + i) % 12 < 7
            games.append({
                "home": local, "away": visitante,
                "home_score": 110 if gana_local else 102,
                "away_score": 102 if gana_local else 110,
            })
    model.fit(games)

    now = datetime.now(UTC)
    event = Event(
        event_id="demo-001",
        sport=Sport.NBA,
        commence_time=now + timedelta(hours=6),
        home_team="Boston Celtics",
        away_team="Miami Heat",
        books=(
            # Pinnacle marca la linea; los libros blandos la copian con retraso.
            # Aqui draftkings paga algo mejor el lado del local: eso es line
            # shopping, la capa complementaria a la deteccion de EV.
            BookMarket("pinnacle", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.66), Outcome("Miami Heat", 2.32)), now),
            BookMarket("draftkings", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.72), Outcome("Miami Heat", 2.20)), now),
            BookMarket("fanduel", Market.MONEYLINE,
                       (Outcome("Boston Celtics", 1.68), Outcome("Miami Heat", 2.26)), now),
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

    # Reserva de cuota: ver Settings.quota_reserve.
    restante = provider.credits_remaining
    if restante is not None and restante <= settings.quota_reserve and not args.force:
        print(
            f"Quedan {restante} creditos, por debajo de la reserva de "
            f"{settings.quota_reserve}. `scan` se abstiene para que `close` pueda "
            f"seguir capturando cierres: una senal perdida cuesta una oportunidad, "
            f"un cierre perdido cuesta poder evaluar la apuesta."
        )
        return 0

    print(f"{len(events)} eventos obtenidos. Cuota restante: {provider.credits_remaining}")

    # ESTRATEGIA lineshop: no usa modelo propio. La referencia es el precio de
    # los libros sharp, asi que no le afecta la barrera de cobertura de
    # temporada ni necesita datos historicos.
    if args.strategy == "lineshop":
        from betbot.ev.lineshop import LineShopConfig, LineShopEngine

        motor = LineShopEngine(LineShopConfig(
            bankroll=settings.bankroll,
            kelly_fraction=settings.kelly_fraction,
            max_stake_pct=settings.max_stake_pct,
        ))
        senales = motor.scan(events)
        almacen = SignalStore(settings.db_path)
        nuevas = [x for x in senales
                  if not almacen.already_alerted(x.event_id, x.market.value, x.selection)]
        for alerter in _alerters(settings):
            alerter.send(nuevas)
        almacen.save_many(nuevas)
        if not senales:
            print(
                "Sin desacuerdos suficientes entre casas. Es lo normal: la "
                "ventana se cierra en minutos."
            )
        return 0

    model, err = load_trained_model(sport, args.games_db)
    if model is None:
        print(err, file=sys.stderr)
        return 2

    _, last_trained = model_freshness(sport, args.games_db)

    engine = EVEngine(settings.ev_config())
    preds = {}
    fuera_de_cobertura = 0
    motivo = ""
    for ev in events:
        permitido, razon = coverage_check(sport, last_trained, ev.commence_time)
        if not permitido and not args.force:
            fuera_de_cobertura += 1
            motivo = razon
            continue
        p = model.predict(ev)
        if p is not None:
            preds[ev.event_id] = p

    if fuera_de_cobertura:
        print(
            f"\nBLOQUEADOS {fuera_de_cobertura} de {len(events)} eventos:\n"
            f"  {motivo}\n"
            f"  Ingiere los resultados de la temporada en curso antes de escanear:\n"
            f"    python -m betbot.cli ingest --sport {args.sport} --source hoopr "
            f"--from <ano> --to <ano>\n"
            f"  (--force ignora esta barrera; no lo uses para apostar de verdad)"
        )

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
    solapados = store.cross_source_duplicates(sport)
    if solapados:
        print(
            f"\nAVISO: {len(solapados)} partidos aparecen con MAS DE UNA FUENTE.\n"
            f"  Ejemplo: {solapados[0]['game_date']} "
            f"{solapados[0]['away_team']} @ {solapados[0]['home_team']} "
            f"({solapados[0]['fuentes']})\n"
            f"  Cada uno cuenta DOS VECES en el entrenamiento y el modelo exagera\n"
            f"  las diferencias entre equipos. Usa una sola fuente por rango de\n"
            f"  temporadas, o borra la BD y reingiere:  rm {args.db}"
        )

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

    # REGRESION DE ENTRETEMPORADA. `fit` solo la aplica cuando encuentra el
    # cambio de temporada DENTRO de los datos. Si la ultima temporada ingerida
    # ya termino y ha empezado otra, los ratings se quedan tal cual estaban en
    # la final — sin regresar a la media pese a que las plantillas cambiaron.
    #
    # Importa justo cuando mas: en las primeras semanas de temporada, el equipo
    # que arraso en junio conserva un rating inflado, el mercado ya ha
    # descontado los fichajes y las bajas, y el modelo "encuentra valor" a
    # favor de un favorito que ya no es tan favorito. Es una forma sistematica
    # de perder dinero en octubre y noviembre.
    pendientes = seasons_started_since(sport, rows[-1]["date"])
    if pendientes and hasattr(model, "ratings"):
        for _ in range(min(pendientes, 3)):
            # reset_games=False: ver la docstring de EloRatings.new_season. En
            # vivo, un equipo con historia y rating regresado es predecible
            # desde la jornada 1; poner el contador a cero dejaria al bot mudo
            # todo octubre.
            model.ratings.new_season(reset_games=False)
        log.info(
            "aplicada regresion de entretemporada x%d (ultimo partido: %s)",
            min(pendientes, 3), rows[-1]["date"],
        )

    return model, None


def coverage_check(sport, last_trained: str, event_date) -> tuple[bool, str]:
    """¿Puede el modelo opinar sobre un partido de esta fecha?

    LA BARRERA MAS IMPORTANTE DEL SISTEMA. Un Elo entrenado hasta junio no sabe
    NADA del verano: draft, traspasos, agencia libre, lesiones. El mercado si lo
    sabe y ya lo ha puesto en el precio. El modelo, en cambio, sigue creyendo
    que las plantillas son las de la final, y cuando su vision choca de frente
    con la del mercado interpreta esa diferencia como VALOR.

    Es el peor fallo posible del bot, porque no se parece a un fallo: produce
    muchas senales, con EV enorme, todas en la misma direccion. Justo lo que uno
    querria ver si funcionara.

    Devuelve (permitido, motivo).
    """
    from datetime import date

    if not last_trained:
        return False, "el modelo no tiene datos de entrenamiento"

    if hasattr(event_date, "date"):
        event_day = event_date.date()
    elif isinstance(event_date, str):
        try:
            event_day = date.fromisoformat(event_date[:10])
        except ValueError:
            return True, ""
    else:
        event_day = event_date

    saltos = _season_starts_between(sport, last_trained, event_day)
    if saltos:
        return False, (
            f"el partido es de una temporada POSTERIOR a los datos de "
            f"entrenamiento (ultimo partido conocido: {last_trained}). El modelo "
            f"desconoce el mercado de fichajes y sus predicciones no valen nada."
        )
    return True, ""


def seasons_started_since(sport, last_game_date: str) -> int:
    """Cuantas temporadas han ARRANCADO desde el ultimo partido ingerido.

    Se cuenta por inicios de temporada en lugar de por el numero de temporada
    porque cada fuente usa su propia convencion: hoopR y 538 etiquetan la NBA
    por el ano de FIN, nflverse etiqueta la NFL por el de INICIO, y en MLB
    coincide con el ano natural. Contar arranques evita depender de eso.
    """
    from datetime import date

    try:
        from betbot.ingest.sources.espn import _SEASON_WINDOWS
    except ImportError:
        return 0

    window = _SEASON_WINDOWS.get(sport)
    if window is None:
        return 0

    return _season_starts_between(sport, last_game_date, date.today())


def _season_starts_between(sport, desde: str, hasta) -> int:
    """Cuantos arranques de temporada caen en (desde, hasta]."""
    from datetime import date

    try:
        from betbot.ingest.sources.espn import _SEASON_WINDOWS
    except ImportError:
        return 0

    window = _SEASON_WINDOWS.get(sport)
    if window is None:
        return 0
    (start_month, start_day), _, _ = window

    try:
        inicio = date.fromisoformat(str(desde)[:10])
    except (ValueError, TypeError):
        return 0

    count = 0
    for year in range(inicio.year, hasta.year + 1):
        arranque = date(year, start_month, start_day)
        if inicio < arranque <= hasta:
            count += 1
    return count


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


def in_season(sport) -> bool:
    """Si el deporte esta en temporada AHORA MISMO.

    Sin esto, el aviso de datos desactualizados salta todo el verano en la NBA:
    en septiembre el ultimo partido tiene 87 dias y no falta ningun dato, la
    temporada simplemente termino. Un aviso que grita en falso durante meses se
    acaba ignorando, y entonces no sirve cuando el problema es real.
    """
    from datetime import date

    try:
        from betbot.ingest.sources.espn import _SEASON_WINDOWS
    except ImportError:
        return True

    window = _SEASON_WINDOWS.get(sport)
    if window is None:
        return True

    (sm, sd), (em, ed), crosses = window
    today = date.today()
    start = date(today.year, sm, sd)
    end = date(today.year + (1 if crosses else 0), em, ed)
    if crosses:
        # La temporada cruza el ano: se esta dentro si hoy cae despues del
        # inicio de esta temporada o antes del fin de la anterior.
        return today >= start or today <= date(today.year, em, ed)
    return start <= today <= end


def _make_source(sport, name: str | None = None):
    """Fuente historica del deporte. `name` elige explicitamente cual.

    Por defecto se usa la fuente con MAS HISTORIA de cada deporte, que suele ser
    un dataset estatico. Pero esos datasets se congelan: el de NBA termina en
    2015. Para temporadas recientes hay que pedir `--source espn` de forma
    explicita, y por eso este parametro tiene que respetarse.
    """
    from betbot.types import Sport

    if name in ("openfootball", "of"):
        from betbot.ingest.sources.openfootball_json import LEAGUE_CODES, OpenFootballJSON
        if sport not in LEAGUE_CODES:
            return None
        return OpenFootballJSON(sport)

    if name in ("hoopr", "nba-reciente"):
        from betbot.ingest.sources.hoopr_nba import HoopRNBA
        if sport is not Sport.NBA:
            return None
        return HoopRNBA()

    if name == "espn":
        from betbot.ingest.sources.espn import ESPN_PATHS, ESPNScoreboard
        if sport not in ESPN_PATHS:
            return None
        return ESPNScoreboard(sport)

    if sport is Sport.NBA:
        # Por defecto, la fuente con MAS historia (1946-2015). Para temporadas
        # recientes hay que pedir `--source hoopr` de forma explicita.
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
        activo = in_season(sport)
        if days > 365:
            marca = "OBSOLETO"
            warnings.append(
                f"{sport.name}: el ultimo partido ingerido es de hace {days} dias "
                f"({last}). Los ratings no reflejan las plantillas actuales — "
                f"NO escanees en vivo con esto."
            )
        elif days > 30 and activo:
            marca = "desactualizado"
            warnings.append(
                f"{sport.name}: {days} dias sin actualizar y la temporada esta "
                f"en curso. Vuelve a ingerir antes de escanear."
            )
        elif not activo:
            marca = "fuera de temporada"
        else:
            marca = "al dia"

        # ¿Puede el modelo opinar sobre partidos de HOY? Es distinto de la
        # frescura: en pretemporada los datos estan completos y aun asi el
        # modelo no sabe nada de la temporada que viene.
        from datetime import date as _date

        permitido, _ = coverage_check(sport, last, _date.today())
        if not permitido and days <= 365:
            warnings.append(
                f"{sport.name}: los datos llegan hasta {last}, pero ya empezo una "
                f"temporada posterior. El modelo desconoce el mercado de fichajes: "
                f"`scan` bloqueara esos partidos hasta que ingieras la temporada "
                f"en curso."
            )
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


def cmd_simulate(args: argparse.Namespace) -> int:
    """Simula la estrategia completa contra odds de cierre historicas REALES.

    Es la unica prueba que responde "¿esto gana dinero?". Todo lo demas mide si
    el modelo predice mejor que la tasa base, que es un rival trivial comparado
    con un mercado de apuestas.

    Solo NFL por ahora: nflverse es la unica fuente gratuita encontrada que
    publica moneylines historicas junto a los resultados.
    """
    import csv

    from betbot.backtest.strategy import simulate
    from betbot.ingest.http import CachedFetcher
    from betbot.ingest.sources.nfl_nflverse import URL
    from betbot.ingest.teams import TeamRegistry
    from betbot.models.elo import EloConfig, EloRatings
    from betbot.models.nfl import NFL_ELO
    from betbot.types import Sport

    settings = Settings.from_env()
    reg = TeamRegistry(Sport.NFL, strict=False)

    def american_a_decimal(valor: str) -> float:
        v = float(valor)
        return 1 + v / 100 if v > 0 else 1 + 100 / abs(v)

    raw = CachedFetcher().get_text(URL, suffix=".csv")
    partidos = []
    anterior = None
    for row in sorted(csv.DictReader(raw.splitlines()), key=lambda r: r["gameday"] or ""):
        if not row.get("home_score") or not row.get("gameday"):
            continue
        home = reg.resolve(row["home_team"])
        away = reg.resolve(row["away_team"])
        if not home or not away or home == away:
            continue
        temporada = int(row["season"])
        juego = {
            "home": home, "away": away,
            "home_score": int(row["home_score"]), "away_score": int(row["away_score"]),
            "date": row["gameday"], "season": temporada,
            "neutral": row.get("location") == "Neutral",
            "new_season": anterior is not None and temporada != anterior,
        }
        if row.get("home_moneyline") and row.get("away_moneyline"):
            try:
                juego["home_odds"] = american_a_decimal(row["home_moneyline"])
                juego["away_odds"] = american_a_decimal(row["away_moneyline"])
            except (ValueError, ZeroDivisionError):
                pass
        partidos.append(juego)
        anterior = temporada

    con_odds = sum(1 for g in partidos if g.get("home_odds"))
    print(f"{len(partidos)} partidos, {con_odds} con moneyline de cierre")
    print(f"{partidos[0]['date']} -> {partidos[-1]['date']}\n")

    def crear():
        return EloRatings(EloConfig(
            k=NFL_ELO.k, home_advantage=NFL_ELO.home_advantage, mov_multiplier=True,
            min_games=NFL_ELO.min_games, regression_to_mean=NFL_ELO.regression_to_mean))

    def predecir(elo, g):
        if not (elo.is_reliable(g["home"]) and elo.is_reliable(g["away"])):
            return None
        p = elo.win_prob(g["home"], g["away"], neutral=g.get("neutral", False))
        return 0.5 + (p - 0.5) * 0.90

    def actualizar(elo, g):
        if g.get("new_season"):
            elo.new_season(reset_games=False)
        elo.update(g["home"], g["away"], g["home_score"], g["away_score"],
                   neutral=g.get("neutral", False))

    cfg = settings.ev_config()
    resultado = simulate(partidos, crear, predecir, actualizar, cfg)
    print(resultado)
    print(
        "\nESTAS SON LINEAS DE CIERRE: el precio mas eficiente del mercado y la "
        "prueba\nmas dura posible. Un ROI negativo aqui significa que el modelo no "
        "tiene ventaja\nsuficiente para superar el margen — saberlo ANTES de "
        "arriesgar dinero es el\nmotivo de que exista este comando."
    )
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
    p_scan.add_argument("--strategy", default="model", choices=["model", "lineshop"],
                        help="'model' compara contra tu modelo propio; 'lineshop' "
                             "busca casas blandas que se quedaron atras del precio "
                             "sharp, sin usar modelo")
    p_scan.add_argument("--force", action="store_true",
                        help="ignora la barrera de cobertura de temporada. Solo "
                             "para depurar: las senales que produce no valen nada")
    p_scan.set_defaults(func=cmd_scan)

    p_ing = sub.add_parser("ingest", help="descarga resultados historicos")
    p_ing.add_argument("--sport", default="nba", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_ing.add_argument("--from", dest="start", type=int, default=2000)
    p_ing.add_argument("--to", dest="end", type=int, default=2015)
    p_ing.add_argument("--source", default=None,
                       help="'hoopr' para NBA reciente (2002-hoy), 'espn' para "
                            "consulta dia a dia; por defecto, el dataset "
                            "historico de cada deporte")
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

    p_sim = sub.add_parser("simulate",
                           help="simula la estrategia contra odds historicas reales")
    p_sim.set_defaults(func=cmd_simulate)

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
