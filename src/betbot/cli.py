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
from pathlib import Path

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
    # lineshop rinde mas en totales y handicap, donde el mercado ya esta en
    # 50/50 y solo queda el precio. Cuesta 3 creditos en vez de 1: la cuota se
    # cobra por mercado.
    mercados = (
        [Market.TOTALS, Market.SPREAD, Market.MONEYLINE]
        if args.strategy == "lineshop"
        else [Market.MONEYLINE]
    )
    try:
        events = provider.fetch_events(sport, mercados)
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


def cmd_survey(args: argparse.Namespace) -> int:
    """Mide si existe la oportunidad que busca `lineshop`, sin apostar nada.

    POR QUE ESTE COMANDO. Antes de pagar un plan de odds mas grande conviene
    saber si hay algo que capturar. Un solo escaneo (3 creditos) responde:
    ¿cuanto se desvian las casas blandas del precio sharp, y con que frecuencia
    esa desviacion es suficiente para apostar?

    Si los desacuerdos son raros o pequenos, ningun plan lo arregla y el gasto
    no tiene sentido. Si son frecuentes, el plan se justifica solo.
    """
    from betbot.ev.lineshop import LineShopConfig, LineShopEngine
    from betbot.odds.the_odds_api import OddsAPIError, TheOddsAPI

    settings = Settings.from_env()
    if not settings.odds_api_key:
        print("Falta ODDS_API_KEY.", file=sys.stderr)
        return 2

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}", file=sys.stderr)
        return 2

    provider = TheOddsAPI(settings.odds_api_key, regions=settings.regions)
    mercados = [Market.TOTALS, Market.SPREAD, Market.MONEYLINE]
    try:
        events = provider.fetch_events(sport, mercados)
    except OddsAPIError as e:
        print(f"Error consultando odds: {e}", file=sys.stderr)
        return 1

    n_regiones = len([r for r in settings.regions.split(",") if r.strip()])
    coste = len(mercados) * n_regiones
    print(f"{len(events)} eventos | cuota restante: {provider.credits_remaining}")
    print(
        f"(este escaneo costo {coste} creditos: {len(mercados)} mercados x "
        f"{n_regiones} region(es). La API multiplica por AMBOS.)\n"
    )

    if not events:
        print("Sin eventos. Prueba otro deporte o vuelve en temporada.")
        return 0

    # FILTRO TEMPORAL. Un feed de NFL en septiembre trae la temporada entera.
    # Las casas blandas publican lineas de partidos a semanas vista con margenes
    # anchos, limites ridiculos y sin haberlas ajustado: ahi SIEMPRE parecera
    # que hay valor, y no lo hay. Solo cuenta lo que empieza pronto.
    from datetime import UTC, datetime, timedelta

    limite = datetime.now(UTC) + timedelta(hours=args.horas)
    proximos = [e for e in events if e.commence_time <= limite]
    if len(proximos) < len(events):
        print(
            f"De {len(events)} eventos, {len(proximos)} empiezan en las proximas "
            f"{args.horas}h. El resto se ignora: las lineas a semanas vista tienen\n"
            f"margenes anchos y limites minimos, y producen 'valor' que no existe.\n"
        )
    events = proximos
    if not events:
        print(f"Ningun partido empieza en las proximas {args.horas}h.")
        print("Sube --horas o prueba cuando haya jornada cerca.")
        return 0

    motor = LineShopEngine(LineShopConfig(min_ev=-99, min_edge=-99))
    todas: list[tuple[str, float, str]] = []
    con_sharp = 0
    libros_vistos: dict[str, int] = {}

    for ev in events:
        for bm in ev.books:
            libros_vistos[bm.bookmaker] = libros_vistos.get(bm.bookmaker, 0) + 1
        tiene_sharp = False
        for m in mercados:
            if motor.sharp_probs(ev, m) is not None:
                tiene_sharp = True
            for s in motor.evaluate(ev, m):
                todas.append((m.value, s.ev, s.bookmaker))
        if tiene_sharp:
            con_sharp += 1

    print(f"Eventos con al menos un libro sharp cotizando: {con_sharp}/{len(events)}")
    if not con_sharp:
        print(
            "\nSIN REFERENCIA SHARP no hay estrategia posible: comparar dos casas\n"
            "blandas entre si no dice cual tiene razon. Prueba con "
            "ODDS_REGIONS=eu,uk\nen .env, que es donde suele estar Pinnacle."
        )
        return 0

    mias = {c.strip().lower() for c in (args.casas or "").split(",") if c.strip()}
    print(f"Libros presentes: {len(libros_vistos)}")
    for nombre in sorted(libros_vistos):
        marca = "  <-- TUYA" if nombre.lower() in mias else ""
        print(f"  {nombre}{marca}")
    if mias:
        faltan = mias - {b.lower() for b in libros_vistos}
        if faltan:
            print(
                f"\n  NO APARECEN en el feed: {', '.join(sorted(faltan))}\n"
                f"  No podras actuar sobre lo que se detecte en otras casas."
            )
    print()

    if not todas:
        print("Ninguna discrepancia medible. La oportunidad no existe ahora mismo.")
        return 0

    # SOLO CUENTAN LAS CASAS DONDE PUEDES APOSTAR. Una oportunidad en un libro
    # sin cuenta no es una oportunidad; incluirla infla el diagnostico justo en
    # la direccion que lleva a pagar una suscripcion.
    if mias:
        antes = len(todas)
        todas = [t for t in todas if t[2].lower() in mias]
        print(
            f"Filtrado a TUS casas: {len(todas)} de {antes} discrepancias caen "
            f"donde puedes apostar.\n"
        )
        if not todas:
            print(
                "NINGUNA discrepancia cae en tus casas. Lo que se detecta esta en\n"
                "libros que no puedes usar, asi que no es accionable. Si esto se\n"
                "repite, la estrategia no te sirve — no por los modelos, sino por\n"
                "donde puedes apostar."
            )
            return 0

    # Solo se cuentan discrepancias FAVORABLES: que una casa pague peor que la
    # sharp no es una oportunidad, es simplemente un mal precio que se ignora.
    print("CUANTAS OPORTUNIDADES HABRIA SEGUN EL UMBRAL DE EV")
    print(f"{'umbral':>10} {'oportunidades':>15} {'% de eventos':>14}")
    for umbral in (0.00, 0.01, 0.02, 0.03, 0.05):
        n = sum(1 for _, ev_val, _ in todas if ev_val >= umbral)
        print(f"{umbral:>9.0%} {n:>15} {n / len(events):>13.0%}")

    positivas = sorted((e for _, e, _ in todas if e > 0), reverse=True)
    if positivas:
        print(f"\nMejor EV encontrado ahora: {positivas[0]:+.2%}")
        mediana = positivas[len(positivas) // 2]
        print(f"Mediana de las positivas:  {mediana:+.2%}")

    por_mercado: dict[str, int] = {}
    for mercado, ev_val, _ in todas:
        if ev_val >= 0.02:
            por_mercado[mercado] = por_mercado.get(mercado, 0) + 1
    if por_mercado:
        print("\nDonde aparecen (EV >= 2%):")
        for m, n in sorted(por_mercado.items(), key=lambda x: -x[1]):
            print(f"  {m:<10} {n}")

    por_libro: dict[str, int] = {}
    for _, ev_val, libro in todas:
        if ev_val >= 0.02:
            por_libro[libro] = por_libro.get(libro, 0) + 1
    if por_libro:
        print("\nQue casas se quedan atras (EV >= 2%):")
        for libro, n in sorted(por_libro.items(), key=lambda x: -x[1])[:8]:
            print(f"  {libro:<20} {n}")
        print(
            "\n  COMPRUEBA QUE PUEDES APOSTAR AHI. Encontrar valor en una casa\n"
            "  donde no tienes cuenta —o que no acepta tu pais— no es una\n"
            "  oportunidad, es un ejercicio teorico."
        )

    n_util = sum(1 for _, e, _ in todas if e >= 0.02)

    # Registro acumulado: una foto no dice nada, la serie si. Sin esto hay que
    # comparar quince salidas a ojo, que es como no medir.
    if args.log:
        from datetime import datetime
        from pathlib import Path

        ruta = Path(args.log)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not ruta.exists()
        mejor = max((e for _, e, _ in todas), default=0.0)
        with ruta.open("a") as fh:
            if nuevo:
                fh.write("momento,deporte,eventos,con_sharp,libros,oportunidades,mejor_ev\n")
            fh.write(
                f"{datetime.now().isoformat(timespec='seconds')},{args.sport},"
                f"{len(events)},{con_sharp},{len(libros_vistos)},{n_util},"
                f"{mejor:.4f}\n"
            )
        print(f"\nAnotado en {ruta}")

        lineas = ruta.read_text().strip().splitlines()[1:]
        if len(lineas) > 1:
            oportunidades = [int(x.split(",")[5]) for x in lineas]
            con_algo = sum(1 for x in oportunidades if x > 0)
            print(
                f"Historico: {len(lineas)} mediciones | "
                f"{con_algo} con alguna oportunidad ({con_algo / len(lineas):.0%}) | "
                f"media {sum(oportunidades) / len(oportunidades):.1f} por escaneo"
            )

    print("\n" + "=" * 62)
    print("QUE SIGNIFICA ESTO PARA DECIDIR SI PAGAR UN PLAN")
    print("=" * 62)
    if n_util == 0:
        print(
            "En este momento NO hay ninguna oportunidad por encima del 2%.\n"
            "Un solo escaneo no es concluyente —las ventanas duran minutos— pero\n"
            "si repites esto varias veces y sigue en cero, pagar mas cuota no\n"
            "sirve de nada: no hay nada que capturar."
        )
    else:
        print(
            f"Hay {n_util} oportunidades por encima del 2% AHORA MISMO.\n\n"
            "Cuidado con extrapolar: esto es una foto, no una pelicula. Una\n"
            "linea desfasada dura minutos, asi que lo que ves aqui puede\n"
            "desaparecer antes de que apuestes. Repite el comando varias veces\n"
            "a lo largo de un dia: si el numero se mantiene, la oportunidad es\n"
            "estructural y justifica pagar por escanear mas seguido. Si aparece\n"
            "y desaparece sin patron, estas viendo ruido de sincronizacion."
        )
    print(
        "\nCoste de escanear un deporte, segun frecuencia (3 creditos por vez):"
    )
    for etiqueta, cada_min in (("cada 10 min", 10), ("cada 30 min", 30),
                               ("cada 2 horas", 120), ("cada 6 horas", 360)):
        por_mes = (60 / cada_min) * 24 * 30 * 3
        print(f"  {etiqueta:<14} ~{por_mes:>7,.0f} creditos/mes")
    print("\nCompara con el limite de tu plan actual antes de decidir.")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    """Archiva el feed de cuotas sin apostar ni alertar nada.

    Este comando no produce picks y no debe producirlos. Su unico trabajo es
    acumular el historico que no se puede comprar: precios de todas las casas,
    en todos los mercados, a lo largo del tiempo. Es la respuesta a los dos
    callejones sin salida del proyecto (props sin lineas historicas, line
    shopping sin odds multi-casa del pasado): si nadie lo vende, se fabrica.

    Coste: igual que cualquier llamada, n_mercados * n_regiones creditos. Con
    los tres mercados base y una region son 3 creditos por deporte y barrido.
    Con --props el coste se multiplica por partido, asi que se estima y se
    imprime ANTES de gastar, y se corta con --max-eventos.
    """
    from betbot.collect import PROPS_POR_DEPORTE, OddsArchive, parse_evento
    from betbot.odds.the_odds_api import OddsAPIError, TheOddsAPI

    archivo = OddsArchive(args.db)

    if args.stats:
        r = archivo.resumen()
        if not r["filas"]:
            print(f"Archivo vacio ({args.db}). Corre `collect --sport nfl` para empezar.")
            return 0
        print(f"Archivo: {args.db}")
        print(f"  {r['filas']:,} precios | {r['eventos']:,} eventos | "
              f"{r['casas']} casas | {r['mercados']} mercados")
        print(f"  desde {r['desde'][:16]} hasta {r['hasta'][:16]}\n")
        print("Por deporte:")
        for d in r["por_deporte"]:
            print(f"  {d['sport']:<32} {d['n']:>9,} precios  {d['ev']:>5} eventos")
        print("\nPor mercado:")
        for m in r["por_mercado"]:
            print(f"  {m['market']:<32} {m['n']:>9,} precios  {m['ev']:>5} eventos")
        # Sin esto el archivo es un numero que no significa nada. La barrera
        # real para medir cualquier cosa son ~100-200 observaciones cerradas.
        print(
            "\nQue falta para poder medir algo: el archivo sirve cuando haya"
            "\nvarios cientos de eventos YA JUGADOS con su linea de apertura y"
            "\nde cierre. Antes de eso cualquier conclusion es ruido."
        )
        return 0

    settings = Settings.from_env()
    if not settings.odds_api_key:
        print("Falta ODDS_API_KEY.", file=sys.stderr)
        return 2

    sport = SPORT_ALIASES.get(args.sport)
    if sport is None:
        print(f"Deporte desconocido: {args.sport}", file=sys.stderr)
        return 2

    # `--regiones` permite pedir las props a menos regiones que las lineas de
    # partido: las props de NFL y NBA las ofrecen casi solo casas de EE.UU., y
    # cada region anadida multiplica el coste por partido.
    regiones = args.regiones or settings.regions
    n_regiones = max(1, len([r for r in regiones.split(",") if r.strip()]))
    mercados = [m.strip() for m in args.markets.split(",") if m.strip()]
    provider = TheOddsAPI(settings.odds_api_key, regions=regiones)

    total_vistas = total_nuevas = 0

    if mercados:
        print(f"Feed de liga: {len(mercados)} mercados x {n_regiones} region(es) "
              f"= {len(mercados) * n_regiones} creditos")
        try:
            crudo = provider.fetch_odds_raw(sport, mercados)
        except OddsAPIError as e:
            print(f"Error consultando odds: {e}", file=sys.stderr)
            return 1
        filas = [f for item in crudo for f in parse_evento(item, sport.value)]
        res = archivo.archivar(filas)
        total_vistas += res.vistas
        total_nuevas += res.nuevas
        print(f"  {len(crudo)} eventos | {res.vistas:,} precios vistos | "
              f"{res.nuevas:,} nuevos | {res.sin_cambio:,} sin cambio")

    if args.props:
        claves = PROPS_POR_DEPORTE.get(sport.value)
        if not claves:
            print(f"No hay catalogo de props para {sport.value}.", file=sys.stderr)
            return 2
        try:
            eventos = provider.fetch_event_list(sport)
        except OddsAPIError as e:
            print(f"Error listando eventos: {e}", file=sys.stderr)
            return 1
        ahora = datetime.now(UTC)
        limite = ahora + timedelta(hours=args.horas)
        # Solo partidos que aun no empiezan: los ya en juego tambien salen en
        # la lista, y sus props en vivo costarian creditos sin servir de nada.
        proximos = [
            e for e in eventos
            if _ts_evento(e.get("commence_time")) is not None
            and ahora < _ts_evento(e["commence_time"]) <= limite
        ]
        if args.solo_apostadas:
            # Cierre de las apuestas en papel: solo los partidos que tienen alguna
            # pendiente. Sin apuestas en la ventana, no se gasta un credito.
            from betbot.papel import RegistroPapel

            con_apuestas = {f["event_id"] for f in RegistroPapel(args.registro).todas(sport.value)
                            if f["estado"] == "pendiente"}
            proximos = [e for e in proximos if e.get("id") in con_apuestas]
        # Desempate por id: la decision del sabado y el cierre del domingo tienen
        # que elegir LOS MISMOS partidos cuando varios empiezan a la misma hora.
        proximos.sort(key=lambda e: (e["commence_time"], e.get("id", "")))
        proximos = proximos[: args.max_eventos]
        coste = len(proximos) * len(claves) * n_regiones
        print(f"\nProps: {len(proximos)} partidos x {len(claves)} mercados x "
              f"{n_regiones} region(es) = {coste} creditos")
        if not proximos:
            print("  Sin partidos en la ventana. Nada que pedir.")
        elif args.dry_run:
            print("  --dry-run: no se gasto nada.")
        else:
            fallos = 0
            for ev in proximos:
                try:
                    crudo_ev = provider.fetch_event_odds_raw(sport, ev["id"], list(claves))
                except OddsAPIError as e:
                    # Un 422 aqui casi siempre significa "tu plan no incluye
                    # props". Se reporta una vez y se corta: reintentar 10
                    # partidos para recibir 10 veces el mismo 422 solo quema cuota.
                    fallos += 1
                    print(f"  {ev.get('home_team','?')}: {e}", file=sys.stderr)
                    if fallos == 1 and "422" in str(e):
                        print("  Corto aqui: parece que el plan no da acceso a props.",
                              file=sys.stderr)
                        break
                    continue
                res = archivo.archivar(parse_evento(crudo_ev, sport.value))
                total_vistas += res.vistas
                total_nuevas += res.nuevas
                print(f"  {ev.get('away_team','?')} @ {ev.get('home_team','?')}: "
                      f"{res.vistas:,} precios, {res.nuevas:,} nuevos")

    print(f"\nTotal archivado: {total_nuevas:,} precios nuevos de "
          f"{total_vistas:,} vistos | cuota restante: {provider.credits_remaining}")
    return 0


def _ts_evento(valor) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def temporada_en_curso(deporte: str, hoy=None) -> int:
    """Temporada vigente o mas reciente, en la convencion de cada fuente.

    NFL (nflverse) nombra la temporada por su ano de INICIO: la que se juega de
    septiembre de 2026 a febrero de 2027 es la 2026. NBA (hoopR) la nombra por su
    ano de FIN: la de octubre de 2026 a junio de 2027 es la 2027. Confundirlas
    hace que el refresco semanal pida una temporada que no existe o se salte la
    que esta en juego.
    """
    from datetime import date

    hoy = hoy or date.today()
    if deporte == "nba":
        return hoy.year + 1 if hoy.month >= 10 else hoy.year
    return hoy.year if hoy.month >= 8 else hoy.year - 1


def _rango_por_defecto(args: argparse.Namespace) -> None:
    if getattr(args, "actual", False):
        args.desde = args.hasta = temporada_en_curso(args.sport)
        return
    primera = {"nfl": 1999, "nba": 2002}[args.sport]
    if args.desde is None:
        args.desde = primera
    if args.hasta is None:
        args.hasta = temporada_en_curso(args.sport)


def cmd_ingest_players(args: argparse.Namespace) -> int:
    """Descarga estadisticas semanales de jugador de NFL (nflverse, 1999-actual).

    Es la base del modelo de props: sin historial de rendimiento por jugador no
    hay forma de estimar una probabilidad propia con la que juzgar el precio del
    libro. La reingesta es idempotente, asi que correrlo cada semana durante la
    temporada refresca sin duplicar.
    """
    _rango_por_defecto(args)
    if args.sport == "nba":
        return _ingest_players_nba(args)

    from betbot.ingest.player_store import PlayerStore
    from betbot.ingest.sources.nfl_player_stats import NFLPlayerStats

    args.db = args.db or "data/players.db"
    store = PlayerStore(args.db)
    if args.stats:
        r = store.resumen()
        if not r["filas"]:
            print(f"Sin datos en {args.db}. Corre: ingest-players --from 1999 --to 2026")
            return 0
        print(f"{r['filas']:,} lineas | {r['jugadores']:,} jugadores | "
              f"temporadas {r['desde']}-{r['hasta']} | ultima semana cargada: "
              f"{r['ultima_semana']}")
        return 0

    fuente = NFLPlayerStats()
    total = 0
    for temporada in range(args.desde, args.hasta + 1):
        try:
            filas = fuente.fetch_season(temporada)
        except Exception as e:
            print(f"  {temporada}: no disponible ({e})", file=sys.stderr)
            continue
        total += store.upsert_many(filas)
        print(f"  {temporada}: {len(filas):,} lineas")
    print(f"\n{total:,} lineas guardadas en {args.db}")
    if fuente.descartados:
        # Casi siempre son filas sin player_id, que no sirven para una serie
        # temporal. Se informa el numero para que un salto raro se note.
        print(f"({len(fuente.descartados)} filas descartadas por falta de id)")
    return 0


def _ingest_players_nba(args: argparse.Namespace) -> int:
    """Estadisticas por partido de NBA (hoopR). Temporada = ano en que TERMINA."""
    from betbot.ingest.nba_player_store import NBAPlayerStore
    from betbot.ingest.sources.nba_player_box import FALTA_PYARROW, NBAPlayerBox

    args.db = args.db or "data/nba_players.db"
    store = NBAPlayerStore(args.db)
    if args.stats:
        r = store.resumen()
        if not r["filas"]:
            print(f"Sin datos en {args.db}. Corre: ingest-players --sport nba "
                  f"--from 2002 --to 2026")
            return 0
        print(f"{r['filas']:,} filas ({r['jugados']:,} partidos jugados) | "
              f"{r['jugadores']:,} jugadores | temporadas {r['desde']}-{r['hasta']} | "
              f"ultimo partido: {r['ultimo']}")
        return 0

    try:
        import pyarrow  # noqa: F401
    except ImportError:
        print(FALTA_PYARROW, file=sys.stderr)
        return 2

    fuente = NBAPlayerBox()
    total = 0
    for temporada in range(args.desde, args.hasta + 1):
        try:
            filas = fuente.fetch_season(temporada)
        except Exception as e:
            print(f"  {temporada}: no disponible ({e})", file=sys.stderr)
            continue
        total += store.upsert_many(filas)
        jugados = sum(1 for f in filas if f.jugo)
        print(f"  {temporada} ({temporada - 1}-{str(temporada)[-2:]}): "
              f"{len(filas):,} filas, {jugados:,} jugados")
    print(f"\n{total:,} filas guardadas en {args.db}")
    return 0


def cmd_backtest_props(args: argparse.Namespace) -> int:
    """Valida el MODELO de props contra 27 temporadas reales.

    IMPORTANTE, PARA NO MALINTERPRETAR LA SALIDA. Esto NO mide si se le gana al
    mercado: no existen lineas historicas de props con las que compararse. Mide
    si el modelo describe bien al jugador, que es el requisito previo. Un modelo
    mal calibrado pierde seguro; uno bien calibrado puede perder igual si el
    mercado es mejor. Lo segundo solo se sabra con las lineas que `collect` vaya
    archivando.
    """
    import sqlite3

    from betbot.backtest.props import marca_calibracion, walk_forward_props
    from betbot.models.props import MERCADOS, MERCADOS_NBA, PropsModel

    if args.sport == "nba":
        from betbot.ingest.nba_player_store import NBAPlayerStore

        args.db = args.db or "data/nba_players.db"
        mapa = MERCADOS_NBA
        como_ingerir = "ingest-players --sport nba --from 2002 --to 2026"
        filas = NBAPlayerStore(args.db).filas_para_modelo()
    else:
        args.db = args.db or "data/players.db"
        mapa = MERCADOS
        como_ingerir = "ingest-players --from 1999 --to 2026"
        columnas = sorted(set(MERCADOS.values()))
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        try:
            filas = [dict(r) for r in conn.execute(
                f"SELECT player_id, position, season, week, {','.join(columnas)} "
                f"FROM player_weeks WHERE season_type='REG' ORDER BY season, week"
            )]
        except sqlite3.OperationalError as e:
            print(f"No hay datos de jugador en {args.db}: {e}", file=sys.stderr)
            print(f"Corre primero: {como_ingerir}", file=sys.stderr)
            return 2
        finally:
            conn.close()

    if not filas:
        print(f"Base vacia. Corre: {como_ingerir}", file=sys.stderr)
        return 2

    temporadas = sorted({f["season"] for f in filas})
    print(f"{len(filas):,} lineas de jugador | evaluando desde {args.desde}")
    print(f"Recorre {len(temporadas)} temporadas en orden; tarda unos minutos.\n")

    def avance(temporada: int) -> None:
        hecho = temporadas.index(temporada)
        fase = "evaluando" if temporada >= args.desde else "solo aprende"
        print(f"  {temporada}  ({hecho + 1}/{len(temporadas)}, {fase})", flush=True)

    res = walk_forward_props(
        filas, mercados=tuple(mapa), columnas=mapa,
        modelo=PropsModel(decay=args.decay), desde_temporada=args.desde,
        progreso=avance,
    )
    print()

    print(f"{'mercado':<24}{'preds':>10}{'PIT desv':>11}{'Brier':>9}{'logloss':>10}")
    for m, r in res.items():
        print(f"{m:<24}{r.predicciones:>10,}{r.desviacion_uniforme:>10.2f}%"
              f"{r.brier:>9.4f}{r.log_score_medio:>10.4f}")

    print("\nPIT por decil (un modelo calibrado da ~10.0 en los diez):")
    for m, r in res.items():
        n = len(r.pit) or 1
        print(f"  {m:<24}" + " ".join(f"{100 * d / n:5.1f}" for d in r.pit_deciles))

    if args.calibracion:
        print("\nCalibracion sobre lineas sinteticas (predicho vs observado):")
        for m, r in res.items():
            print(f"\n  {m}")
            for pm, obs, n in r.calibracion():
                marca = marca_calibracion(pm, obs, n)
                print(f"    {pm:5.1%} -> {obs:5.1%}  (n={n:,}){marca}")
    return 0


def cmd_backtest_anytime_td(args: argparse.Namespace) -> int:
    """Valida el modelo de 'anytime TD' contra la tasa base de su posicion.

    Igual que backtest-props, esto NO mide si se le gana al libro. Mide si el
    modelo sabe mas que "un RB titular anota ~27% de las veces". Si ni siquiera
    le gana a eso, no tiene sentido compararlo con el mercado.
    """
    import sqlite3

    from betbot.backtest.anytime_td import (
        brier,
        calibracion,
        skill,
        walk_forward_anytime_td,
    )
    from betbot.backtest.props import marca_calibracion
    from betbot.models.anytime_td import AnytimeTDModel

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        filas = [dict(r) for r in conn.execute(
            "SELECT player_id, position, season, week, carries, targets, "
            "rushing_tds, receiving_tds FROM player_weeks "
            "WHERE season_type='REG' ORDER BY season, week"
        )]
    except sqlite3.OperationalError as e:
        print(f"No hay datos de jugador en {args.db}: {e}", file=sys.stderr)
        print("Corre primero: ingest-players --from 1999 --to 2026", file=sys.stderr)
        return 2
    finally:
        conn.close()
    if not filas:
        print("Base vacia. Corre: ingest-players --from 1999 --to 2026", file=sys.stderr)
        return 2

    preds = walk_forward_anytime_td(filas, AnytimeTDModel(peso_historial=args.peso))
    for nombre, desde, hasta in (("2010-2017", 2010, 2017), ("2018-hoy", 2018, 9999)):
        sub = [x for x in preds if desde <= x.season <= hasta]
        if not sub:
            continue
        print(f"\n{nombre}: {len(sub):,} predicciones | Brier {brier(sub):.5f} "
              f"(base {brier(sub, base=True):.5f}) | mejora sobre la base {skill(sub):.2%}")
        for pm, obs, n in calibracion(sub):
            print(f"   {pm:5.1%} -> {obs:5.1%}  (n={n:,}){marca_calibracion(pm, obs, n)}")
    print("\nOjo: por encima de ~40% el modelo se pasa unos puntos (ver RESEARCH.md).")
    return 0


def cmd_papel(args: argparse.Namespace) -> int:
    """Apuestas en papel sobre props: apunta, califica y resume. No apuesta nada.

    Es el paso que falta entre "el modelo esta calibrado" y "el modelo le gana
    a la casa". Usa las lineas que archiva `collect --props`, asi que sin props
    archivadas no hay nada que apuntar.
    """
    import os
    import time

    from betbot.papel import UNIDAD_PCT, RegistroPapel, ahora_utc, calificar, generar, resumir
    from betbot.papel_deportes import AdaptadorNBA, AdaptadorNFL
    from betbot.papel_mensajes import mensaje_apuesta, mensaje_calificacion, mensaje_generacion

    registro = RegistroPapel(args.registro)
    sport_key = Sport.NBA.value if args.sport == "nba" else Sport.NFL.value
    telegram = _telegram_para_papel() if args.telegram else None
    settings = Settings.from_env()
    # El equivalente en pesos solo se muestra si el bankroll esta puesto a
    # proposito en el .env: el valor por defecto (1000) inventaria una cifra.
    pesos_u = settings.bankroll * UNIDAD_PCT if os.getenv("BANKROLL") else None

    if not args.resumen:
        db = args.db or ("data/nba_players.db" if args.sport == "nba" else "data/players.db")
        if not Path(db).exists():
            print(f"No hay estadisticas de jugador en {db}. Corre primero: "
                  f"ingest-players --sport {args.sport}", file=sys.stderr)
            return 2
        if not Path(args.archivo).exists():
            print(f"No hay archivo de lineas en {args.archivo}. Corre primero: "
                  f"collect --sport {args.sport} --props", file=sys.stderr)
            return 2
        print(f"Entrenando modelos de {args.sport.upper()} con todo el historial...", flush=True)
        t0 = time.time()
        adaptador = (AdaptadorNBA(db=db) if args.sport == "nba" else AdaptadorNFL(db=db)).entrenar()
        print(f"  listo en {time.time() - t0:.0f}s\n")

        ahora = ahora_utc()
        detalle: list = []
        cal = calificar(adaptador, args.archivo, registro, ahora, detalle=detalle)
        if cal:
            print("Calificadas: " + ", ".join(f"{k} {v}" for k, v in sorted(cal.items())))
        if telegram and detalle:
            telegram.send_plain(mensaje_calificacion(
                detalle, args.sport, resumir(registro.todas(sport_key))))

        inf = generar(adaptador, args.archivo, registro, ahora, min_ev=args.min_ev,
                      max_atraso=args.max_atraso, fraccion_kelly=settings.kelly_fraction,
                      tope_unidades=settings.max_stake_pct / UNIDAD_PCT)
        # Cada apuesta sale en su propio mensaje en cuanto se decide, que es como
        # tendra que funcionar con dinero real. Con muchas de golpe, las primeras
        # van sueltas y el resto en el resumen, para no inundar el telefono.
        enviadas = 0
        if telegram:
            for a in sorted(inf.nuevas, key=lambda a: (a.linea.commence, a.linea.partido)):
                if enviadas >= MAX_MENSAJES_SUELTOS:
                    break
                telegram.send_plain(mensaje_apuesta(a, args.sport, pesos_u))
                enviadas += 1
        # Con lineas se avisa siempre, haya o no apuestas: el mensaje es la prueba
        # de que el bot trabajo. Sin lineas solo si se pide (--avisar-vacio, el
        # del sabado): ese silencio es justo el fallo que hay que ver.
        if telegram and (inf.lineas or args.avisar_vacio):
            telegram.send_plain(mensaje_generacion(
                inf, args.sport, resumir(registro.todas(sport_key)), enviadas))
        print(f"Lineas de props vigentes: {inf.lineas:,} | apuntadas nuevas: {inf.apuntadas} "
              f"| ya apuntadas antes: {inf.ya_apuntadas} | sin valor: {inf.sin_valor:,}")
        if inf.descartes:
            print("Descartadas:")
            for motivo, n in sorted(inf.descartes.items(), key=lambda kv: -kv[1]):
                print(f"  {n:>6,}  {motivo}")
        if inf.nuevas:
            print("\nNuevas apuestas EN PAPEL (precio = mediana entre casas):")
            for a in sorted(inf.nuevas, key=lambda a: a.linea.commence):
                ln = a.linea
                pt = f" {ln.point:g}" if ln.point is not None else ""
                print(f"  {ln.commence:%a %d %H:%M}Z  {ln.partido}")
                print(f"      {ln.jugador} {ln.market.replace('player_', '')} "
                      f"{ln.lado}{pt} @ {ln.precio:.2f} (minima {a.cuota_minima:.2f}, "
                      f"{ln.n_casas} casas) | modelo {a.p:.1%} | EV {a.ev:+.1%} | "
                      f"{a.unidades:g} u")
        print()

    r = resumir(registro.todas(sport_key))
    print(f"=== Historial en papel ({args.sport.upper()}) ===")
    print(f"apuntadas {r['apuntadas']} | pendientes {r['pendientes']} | calificadas "
          f"{r['calificadas']} ({r['ganadas']} ganadas) | nulas {r['nulas']} | "
          f"sin calificar {r['sin_calificar']}")
    if r["calificadas"]:
        print(f"En unidades: {r['ganancia_u']:+.2f} u sobre {r['unidades_arriesgadas']:g} "
              f"arriesgadas (ROI {r['roi_u']:+.2%})")
        print(f"A stake plano: ROI {r['roi']:+.2%} (el modelo esperaba {r['ev_medio']:+.2%})")
    if r["n_clv"]:
        print(f"CLV medio: {r['clv_medio']:+.2%} | le gano al cierre en "
              f"{r['clv_positivo']:.0%} de {r['n_clv']} apuestas")
    n = r["calificadas"]
    if n < 200:
        # El ROI de pocas apuestas es casi todo suerte: con 50 apuestas a cuota
        # ~1.9 la desviacion tipica del ROI ronda el 13%. El CLV se estabiliza
        # antes, pero tampoco dice nada serio por debajo de ~100.
        print(f"\nCon {n} apuestas calificadas esto es RUIDO. No saques conclusiones "
              f"antes de ~200; mira primero el CLV.")
    return 0


MAX_MENSAJES_SUELTOS = 12


def _telegram_para_papel():
    """Alertador de Telegram, o None si no esta configurado (sin abortar)."""
    from betbot.alerts.telegram import TelegramAlerter

    settings = Settings.from_env()
    if not settings.telegram_enabled:
        print("Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en .env): "
              "sigo sin mandar mensajes. Pruebalo con: betbot test-telegram",
              file=sys.stderr)
        return None
    return TelegramAlerter(settings.telegram_bot_token, settings.telegram_chat_id)


def cmd_bankroll(args: argparse.Namespace) -> int:
    """Muestra o fija el bankroll del .env. 1 unidad = 1% de el."""
    from betbot.alerts.configurar import escribir_variable, leer_variable
    from betbot.papel import UNIDAD_PCT

    if args.monto is None:
        actual = leer_variable(args.env, "BANKROLL")
        if not actual:
            print("No hay bankroll puesto. Los avisos van en unidades, sin pesos.")
            print("Para fijarlo:  betbot bankroll 700")
            return 0
        print(f"Bankroll: ${float(actual):,.0f} -> 1 unidad = ${float(actual) * UNIDAD_PCT:,.2f}")
        return 0
    if args.monto <= 0:
        print("El bankroll tiene que ser mayor que cero.", file=sys.stderr)
        return 2
    escribir_variable(args.env, "BANKROLL", f"{args.monto:g}")
    print(f"Bankroll: ${args.monto:,.0f} -> 1 unidad = ${args.monto * UNIDAD_PCT:,.2f}")
    print("Pon aqui SOLO el dinero que estas dispuesto a perder entero.")
    return 0


def cmd_configurar_telegram(args: argparse.Namespace) -> int:
    """Pide el token, encuentra el chat id solo y manda un mensaje de prueba."""
    from betbot.alerts.configurar import configurar

    return configurar(env=args.env)


def cmd_test_telegram(args: argparse.Namespace) -> int:
    """Comprueba la configuracion de Telegram enviando un mensaje de prueba."""
    from betbot.alerts.telegram import TelegramAlerter

    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        print(
            "Falta TELEGRAM_BOT_TOKEN en .env.\n\n"
            "Lo mas facil:  betbot configurar-telegram\n\n"
            "A mano:\n"
            "  1. En Telegram, habla con @BotFather\n"
            "  2. Manda /newbot y sigue las instrucciones\n"
            "  3. Te da un token tipo 123456789:AAF...\n"
            "  4. Pegalo en .env como TELEGRAM_BOT_TOKEN=",
            file=sys.stderr,
        )
        return 2

    if not settings.telegram_chat_id:
        print(
            "Falta TELEGRAM_CHAT_ID en .env.\n\n"
            "Como conseguirlo:\n"
            "  1. Manda cualquier mensaje a TU bot desde Telegram\n"
            "  2. Abre en el navegador:\n"
            f"     https://api.telegram.org/bot{settings.telegram_bot_token}"
            "/getUpdates\n"
            '  3. Busca "chat":{"id":NUMERO — ese numero es tu chat_id\n'
            "  4. Pegalo en .env como TELEGRAM_CHAT_ID=",
            file=sys.stderr,
        )
        return 2

    alerter = TelegramAlerter(settings.telegram_bot_token, settings.telegram_chat_id)
    print("Enviando mensaje de prueba...")
    if alerter.send_text(
        "*betbot* conectado.\n\n"
        "Si lees esto, las alertas funcionan. Las senales llegaran por aqui."
    ):
        print("Enviado. Revisa Telegram.")
        return 0

    print(
        "No se pudo enviar. Revisa que el token sea correcto y que hayas "
        "escrito al bot al menos una vez desde tu cuenta.",
        file=sys.stderr,
    )
    return 1


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

    p_sv = sub.add_parser("survey",
                          help="mide si existe oportunidad de lineshop (3 creditos)")
    p_sv.add_argument("--sport", default="nfl", help=f"uno de {sorted(SPORT_ALIASES)}")
    p_sv.add_argument("--casas", default=None, metavar="LISTA",
                      help="casas donde PUEDES apostar, separadas por coma "
                           "(ej: bet365,betmgm). Filtra el diagnostico a lo "
                           "accionable de verdad")
    p_sv.add_argument("--horas", type=int, default=48, metavar="N",
                      help="solo partidos que empiecen en las proximas N horas "
                           "(def. 48). Las lineas lejanas producen valor ficticio")
    p_sv.add_argument("--log", default=None, metavar="RUTA",
                      help="acumula el resultado en un CSV para ver la serie, "
                           "no solo la foto (ej: data/survey.csv)")
    p_sv.set_defaults(func=cmd_survey)

    p_col = sub.add_parser(
        "collect",
        help="archiva cuotas para construir el historico que nadie vende (no apuesta)",
    )
    p_col.add_argument("--sport", default="nfl")
    p_col.add_argument("--markets", default="h2h,spreads,totals",
                       help="lista separada por comas; vacio para solo props")
    p_col.add_argument("--props", action="store_true",
                       help="pide tambien props de jugador (coste por partido)")
    p_col.add_argument("--horas", type=int, default=48,
                       help="ventana de partidos para las props")
    p_col.add_argument("--max-eventos", type=int, default=6,
                       help="tope de partidos con props por corrida (control de cuota)")
    p_col.add_argument("--regiones", default=None,
                       help="regiones para esta llamada (p.ej. 'us'); por defecto las del .env")
    p_col.add_argument("--solo-apostadas", action="store_true",
                       help="props solo de partidos con apuestas en papel pendientes (cierre)")
    p_col.add_argument("--registro", default="data/papel.db")
    p_col.add_argument("--dry-run", action="store_true",
                       help="estima el coste de props sin gastar creditos")
    p_col.add_argument("--stats", action="store_true", help="estado del archivo")
    p_col.add_argument("--db", default="data/odds_archive.db")
    p_col.set_defaults(func=cmd_collect)

    p_ip = sub.add_parser("ingest-players",
                          help="estadisticas semanales de jugador de NFL (nflverse)")
    p_ip.add_argument("--sport", choices=("nfl", "nba"), default="nfl")
    p_ip.add_argument("--from", dest="desde", type=int, default=None,
                      help="primera temporada (NFL: 1999; NBA: 2002, ano en que termina)")
    p_ip.add_argument("--to", dest="hasta", type=int, default=None)
    p_ip.add_argument("--actual", action="store_true",
                      help="solo la temporada en curso (para el refresco del cron)")
    p_ip.add_argument("--stats", action="store_true", help="estado de la base")
    p_ip.add_argument("--db", default=None,
                      help="por defecto data/players.db (NFL) o data/nba_players.db (NBA)")
    p_ip.set_defaults(func=cmd_ingest_players)

    p_bp = sub.add_parser("backtest-props",
                          help="valida el modelo de props (NO mide si gana al mercado)")
    p_bp.add_argument("--sport", choices=("nfl", "nba"), default="nfl")
    p_bp.add_argument("--desde", type=int, default=2010,
                      help="primera temporada evaluada; las previas solo entrenan")
    p_bp.add_argument("--decay", type=float, default=0.90)
    p_bp.add_argument("--calibracion", action="store_true",
                      help="tabla de calibracion detallada")
    p_bp.add_argument("--db", default=None)
    p_bp.set_defaults(func=cmd_backtest_props)

    p_td = sub.add_parser("backtest-anytime-td",
                          help="valida el modelo de anytime TD (NO mide si gana al mercado)")
    p_td.add_argument("--peso", type=float, default=0.70,
                      help="peso del historial de TDs frente al uso (elegido: 0.70)")
    p_td.add_argument("--db", default="data/players.db")
    p_td.set_defaults(func=cmd_backtest_anytime_td)

    p_pp = sub.add_parser("papel",
                          help="apuestas EN PAPEL sobre props: apunta, califica, resume")
    p_pp.add_argument("--sport", choices=("nfl", "nba"), default="nfl")
    p_pp.add_argument("--min-ev", type=float, default=0.03)
    p_pp.add_argument("--max-atraso", type=int, default=1,
                      help="semanas (NFL) o dias (NBA) de datos que pueden faltar")
    p_pp.add_argument("--telegram", action="store_true",
                      help="manda lo apuntado y lo calificado por Telegram")
    p_pp.add_argument("--avisar-vacio", action="store_true",
                      help="con --telegram: avisa tambien si no hay lineas (fallo del barrido)")
    p_pp.add_argument("--resumen", action="store_true",
                      help="solo el resumen, sin entrenar ni apuntar")
    p_pp.add_argument("--archivo", default="data/odds_archive.db")
    p_pp.add_argument("--registro", default="data/papel.db")
    p_pp.add_argument("--db", default=None, help="estadisticas de jugador")
    p_pp.set_defaults(func=cmd_papel)

    p_bk = sub.add_parser("bankroll", help="muestra o fija el bankroll (1 unidad = 1%%)")
    p_bk.add_argument("monto", type=float, nargs="?", default=None)
    p_bk.add_argument("--env", default=".env")
    p_bk.set_defaults(func=cmd_bankroll)

    p_ct = sub.add_parser("configurar-telegram",
                          help="configura Telegram paso a paso (token y chat id)")
    p_ct.add_argument("--env", default=".env")
    p_ct.set_defaults(func=cmd_configurar_telegram)

    p_tg = sub.add_parser("test-telegram", help="comprueba las alertas de Telegram")
    p_tg.set_defaults(func=cmd_test_telegram)

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
