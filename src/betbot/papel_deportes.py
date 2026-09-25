"""Adaptadores de NFL y NBA para las apuestas en papel.

Cada adaptador hace cuatro cosas con las estadisticas de su deporte: entrenar
los modelos con TODO el historial disponible, reconocer a los jugadores por
nombre, dar la probabilidad del modelo para una linea concreta, y calificar la
apuesta cuando el partido ya tiene estadisticas publicadas.

EL ATRASO DE LOS DATOS ES PARTE DE LA DECISION. nflverse publica con dias de
retraso (el 25 de septiembre la semana 3 aun no estaba). Decidir la semana 4 sin
la 3 es decidir con el ultimo partido de cada jugador ausente, que es el que mas
pesa en la media movil. Por eso cada apuesta guarda cuanto atraso tenia al
decidirse, y con demasiado atraso no se apunta nada: se pueden estudiar luego
las apuestas con atraso 1 aparte y ver si rinden peor.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from betbot.ingest.teams import TeamRegistry
from betbot.models.anytime_td import AnytimeTDModel, tds_de, toques_de
from betbot.models.props import COLA_ALTA_DUDOSA, MERCADOS, MERCADOS_NBA, PropsModel
from betbot.papel import FichaJugador, LineaProp, fecha_et, normalizar_nombre, semana_nfl
from betbot.types import Sport

ANYTIME_TD = "player_anytime_td"
# Por encima de esto el modelo de anytime TD sobreestima varios puntos, de forma
# estable en dos eras (RESEARCH.md §12). Una apuesta ahi es comprar el sesgo.
TECHO_ANYTIME = 0.40
# Idem para los mercados cuya cola alta se sabe inflada (props.COLA_ALTA_DUDOSA).
TECHO_COLA_DUDOSA = 0.60


@dataclass
class Veredicto:
    p: float | None
    motivo: str = ""


@dataclass
class Adaptador:
    sport: str
    fichas_por_nombre: dict[str, list[FichaJugador]] = field(default_factory=dict)
    fichas_por_id: dict[str, FichaJugador] = field(default_factory=dict)

    # -- a implementar por deporte ------------------------------------------
    def columna(self, market: str) -> str | None:
        raise NotImplementedError

    def prob(self, ficha: FichaJugador, linea: LineaProp) -> Veredicto:
        raise NotImplementedError

    def atraso(self, linea: LineaProp) -> int:
        raise NotImplementedError

    def calificar(self, fila) -> tuple[str, float | None]:
        raise NotImplementedError

    # -- comun ---------------------------------------------------------------
    def canonico(self, equipo: str) -> str:
        """Nombre canonico de un equipo tal como lo escribe la casa."""
        reg = getattr(self, "_registro", None)
        if reg is None:
            reg = self._registro = TeamRegistry(Sport(self.sport), strict=False)
        return reg.resolve(equipo) or equipo

    def _registrar_ficha(self, ficha: FichaJugador) -> None:
        self.fichas_por_id[ficha.player_id] = ficha

    def indexar(self) -> None:
        self.fichas_por_nombre = defaultdict(list)
        for f in self.fichas_por_id.values():
            if f.nombre:
                self.fichas_por_nombre[normalizar_nombre(f.nombre)].append(f)

    @staticmethod
    def filtrar_por_sesgo(market: str, lado: str, p: float) -> str:
        """Motivo para NO apostar aunque haya EV, o "" si no hay objecion."""
        if market == ANYTIME_TD and lado == "Yes" and p > TECHO_ANYTIME:
            return f"anytime TD sobre {TECHO_ANYTIME:.0%}: el modelo se pasa ahi"
        if market in COLA_ALTA_DUDOSA and p > TECHO_COLA_DUDOSA:
            return f"cola alta dudosa sobre {TECHO_COLA_DUDOSA:.0%}"
        return ""


def _prob_props(modelo: PropsModel, historial: list[float], ficha: FichaJugador,
                linea: LineaProp) -> Veredicto:
    if linea.lado not in ("Over", "Under") or linea.point is None:
        return Veredicto(None, "lado no valido para este mercado")
    proy = modelo.proyectar(historial, ficha.posicion, linea.market)
    if proy is None:
        return Veredicto(None, "sin proyeccion (pocos partidos o poco uso)")
    p = proy.prob_over(linea.point)
    return Veredicto(p if linea.lado == "Over" else 1.0 - p)


# ---------------------------------------------------------------------------
# NFL
# ---------------------------------------------------------------------------

def _fecha_semana_nfl(season: int, week: int) -> date:
    septiembre = date(season, 9, 1)
    labor_day = septiembre + timedelta(days=(7 - septiembre.weekday()) % 7)
    return labor_day + timedelta(days=3 + 7 * (week - 1) + 3)   # domingo de esa semana


@dataclass
class AdaptadorNFL(Adaptador):
    db: str = "data/players.db"
    sport: str = Sport.NFL.value
    modelo: PropsModel = field(default_factory=PropsModel)
    modelo_td: AnytimeTDModel = field(default_factory=AnytimeTDModel)
    hist: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    hist_td: dict[str, list[float]] = field(default_factory=dict)
    hist_toques: dict[str, list[float]] = field(default_factory=dict)
    semanas_cargadas: dict[int, int] = field(default_factory=dict)   # temporada -> ultima

    def entrenar(self) -> AdaptadorNFL:
        registro = TeamRegistry(Sport.NFL, strict=False)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            filas = [dict(r) for r in conn.execute(
                "SELECT * FROM player_weeks WHERE season_type='REG' ORDER BY season, week"
            )]
            # Semanas completas: una semana con solo el partido del jueves (~70
            # filas) no cuenta como cargada.
            for season, week, n in conn.execute(
                "SELECT season, week, COUNT(*) FROM player_weeks "
                "WHERE season_type='REG' GROUP BY season, week"
            ):
                if n >= 500:
                    self.semanas_cargadas[season] = max(self.semanas_cargadas.get(season, 0), week)
        finally:
            conn.close()

        for f in filas:
            pid, pos = f["player_id"], f["position"] or "?"
            for market, col in MERCADOS.items():
                h = self.hist.setdefault((pid, market), [])
                self.modelo.observar(h, pos, market, float(f[col]))
                h.append(float(f[col]))
            tds, toques = tds_de(f), toques_de(f)
            self.modelo_td.observar(pos, tds, toques)
            self.hist_td.setdefault(pid, []).append(tds)
            self.hist_toques.setdefault(pid, []).append(toques)

            ficha = self.fichas_por_id.get(pid) or FichaJugador(
                pid, "", pos, "", "", date.min)
            ficha.nombre = f["player_name"] or ficha.nombre
            ficha.posicion = pos
            ficha.equipo_abrev = f["team"]
            ficha.equipo = registro.resolve(f["team"]) or f["team"]
            ficha.ultimo_partido = _fecha_semana_nfl(f["season"], f["week"])
            self._registrar_ficha(ficha)

        self.modelo.ordenar()
        for ficha in self.fichas_por_id.values():
            ficha.historial = {col: self.hist.get((ficha.player_id, m), [])
                               for m, col in MERCADOS.items()}
            ficha.historial["tds"] = self.hist_td.get(ficha.player_id, [])
        self.indexar()
        return self

    def columna(self, market: str) -> str | None:
        return "tds" if market == ANYTIME_TD else MERCADOS.get(market)

    def prob(self, ficha: FichaJugador, linea: LineaProp) -> Veredicto:
        if linea.market == ANYTIME_TD:
            if linea.lado not in ("Yes", "No"):
                return Veredicto(None, "lado no valido para anytime TD")
            p = self.modelo_td.predecir(
                self.hist_td.get(ficha.player_id, []),
                self.hist_toques.get(ficha.player_id, []), ficha.posicion)
            if p is None:
                return Veredicto(None, "sin proyeccion (pocos partidos o poco uso)")
            return Veredicto(p if linea.lado == "Yes" else 1.0 - p)
        if linea.market not in MERCADOS:
            return Veredicto(None, "mercado sin modelo")
        return _prob_props(self.modelo, self.hist.get((ficha.player_id, linea.market), []),
                           ficha, linea)

    def atraso(self, linea: LineaProp) -> int:
        """Semanas completas que faltan entre los datos y el partido."""
        temporada, semana = semana_nfl(linea.commence)
        ultima = self.semanas_cargadas.get(temporada, 0)
        return max(0, semana - 1 - ultima)

    def calificar(self, fila) -> tuple[str, float | None]:
        commence = datetime.fromisoformat(fila["commence"])
        temporada, semana = semana_nfl(commence)
        ficha = self.fichas_por_id.get(fila["player_id"])
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute(
                "SELECT * FROM player_weeks WHERE player_id=? AND season=? AND week=? "
                "AND season_type='REG'", (fila["player_id"], temporada, semana),
            ).fetchone()
            if r is not None:
                if fila["market"] == ANYTIME_TD:
                    return "real", tds_de(dict(r))
                col = MERCADOS.get(fila["market"])
                return ("real", float(r[col])) if col else ("nula", None)
            equipo = ficha.equipo_abrev if ficha else ""
            publicado = conn.execute(
                "SELECT 1 FROM player_weeks WHERE season=? AND week=? AND "
                "(team=? OR opponent=?) LIMIT 1", (temporada, semana, equipo, equipo),
            ).fetchone()
        finally:
            conn.close()
        # El partido ya esta publicado y el jugador no aparece: no registro
        # nada. En la casa eso suele ser una apuesta anulada.
        return ("nula", None) if publicado else ("pendiente", None)


# ---------------------------------------------------------------------------
# NBA
# ---------------------------------------------------------------------------

@dataclass
class AdaptadorNBA(Adaptador):
    db: str = "data/nba_players.db"
    sport: str = Sport.NBA.value
    modelo: PropsModel = field(default_factory=PropsModel)
    hist: dict[tuple[str, str], list[float]] = field(default_factory=dict)
    ultimo_dato: date = date.min

    def entrenar(self) -> AdaptadorNBA:
        registro = TeamRegistry(Sport.NBA, strict=False)
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            filas = [dict(r) for r in conn.execute(
                "SELECT * FROM nba_player_games WHERE season_type=2 AND minutes > 0 "
                "ORDER BY game_date, game_id, athlete_id"
            )]
            (ultimo,) = conn.execute("SELECT MAX(game_date) FROM nba_player_games").fetchone()
        finally:
            conn.close()
        if ultimo:
            self.ultimo_dato = date.fromisoformat(ultimo[:10])

        for f in filas:
            pid, pos = f["athlete_id"], f["position"] or "?"
            for market, col in MERCADOS_NBA.items():
                h = self.hist.setdefault((pid, market), [])
                self.modelo.observar(h, pos, market, float(f[col]))
                h.append(float(f[col]))
            ficha = self.fichas_por_id.get(pid) or FichaJugador(
                pid, "", pos, "", "", date.min)
            ficha.nombre = f["player_name"] or ficha.nombre
            ficha.posicion = pos
            ficha.equipo_abrev = f["team"]
            ficha.equipo = registro.resolve(f["team"]) or f["team"]
            ficha.ultimo_partido = date.fromisoformat(f["game_date"][:10])
            self._registrar_ficha(ficha)

        self.modelo.ordenar()
        for ficha in self.fichas_por_id.values():
            ficha.historial = {col: self.hist.get((ficha.player_id, m), [])
                               for m, col in MERCADOS_NBA.items()}
        self.indexar()
        return self

    def columna(self, market: str) -> str | None:
        return MERCADOS_NBA.get(market)

    def prob(self, ficha: FichaJugador, linea: LineaProp) -> Veredicto:
        if linea.market not in MERCADOS_NBA:
            return Veredicto(None, "mercado sin modelo")
        return _prob_props(self.modelo, self.hist.get((ficha.player_id, linea.market), []),
                           ficha, linea)

    def atraso(self, linea: LineaProp) -> int:
        """Dias sin datos antes del partido (0 = los de ayer ya estan)."""
        return max(0, (fecha_et(linea.commence) - self.ultimo_dato).days - 1)

    def calificar(self, fila) -> tuple[str, float | None]:
        dia = fecha_et(datetime.fromisoformat(fila["commence"]))
        rango = ((dia - timedelta(days=1)).isoformat(), (dia + timedelta(days=1)).isoformat())
        ficha = self.fichas_por_id.get(fila["player_id"])
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute(
                "SELECT * FROM nba_player_games WHERE athlete_id=? AND "
                "substr(game_date,1,10) BETWEEN ? AND ? ORDER BY game_date LIMIT 1",
                (fila["player_id"], *rango),
            ).fetchone()
            if r is not None:
                if r["minutes"] <= 0:
                    return "nula", None      # no jugo: la casa anula
                col = MERCADOS_NBA.get(fila["market"])
                return ("real", float(r[col])) if col else ("nula", None)
            equipo = ficha.equipo_abrev if ficha else ""
            publicado = conn.execute(
                "SELECT 1 FROM nba_player_games WHERE team=? AND "
                "substr(game_date,1,10) BETWEEN ? AND ? LIMIT 1", (equipo, *rango),
            ).fetchone()
        finally:
            conn.close()
        return ("nula", None) if publicado else ("pendiente", None)
