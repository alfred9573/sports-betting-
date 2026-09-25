"""Apuestas en papel sobre props: el paso entre "el modelo esta calibrado" y
"el modelo le gana a la casa".

QUE HACE. Toma las lineas de props que `collect` archivo, calcula la
probabilidad del modelo para cada una, y apunta —sin apostar nada— las que
tendrian valor esperado positivo al precio de ese momento. Despues del partido
las califica con el resultado real y con el precio de CIERRE.

TRES DECISIONES PARA QUE EL RESULTADO NO SE ENGANE A SI MISMO:

1. El precio "tomado" es la MEDIANA entre casas, no el mejor. Con 28 casas en
   el feed, el mejor precio de cada linea sale casi siempre de una casa donde
   no hay cuenta (o de una linea vieja que nadie ha retirado). Apuntar al mejor
   precio fabricaria un rendimiento que no se puede cobrar. El mejor se guarda
   solo como referencia.

2. Se decide ANTES del cierre y se compara contra el cierre. El CLV (si el
   precio tomado era mejor que el de cierre) es la unica senal que aparece con
   ~200 apuestas; el ROI necesita miles. Una apuesta decidida con el precio de
   cierre tendria CLV cero por construccion y no mediria nada.

3. Si el jugador no juega, la apuesta es NULA, como en la casa. El modelo esta
   condicionado a que el jugador juegue; contar esas apuestas como perdidas o
   ganadas mezclaria dos preguntas distintas.

Y un limite que hay que conocer: el archivo registra cambios de precio, no
retiradas. Si una casa quita una linea, su ultimo precio sigue apareciendo como
vigente. La mediana y el minimo de dos casas por linea amortiguan eso, pero no
lo eliminan.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import statistics
import unicodedata
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
LADOS = ("Over", "Under", "Yes", "No")
SUFIJOS = {"jr", "sr", "ii", "iii", "iv", "v"}


# ---------------------------------------------------------------------------
# Nombres
# ---------------------------------------------------------------------------

def normalizar_nombre(nombre: str) -> str:
    """Forma comparable de un nombre de jugador entre la casa y las estadisticas.

    Las casas y nflverse/ESPN escriben distinto al mismo jugador: "D.J. Moore"
    y "DJ Moore", "Ja'Marr Chase" y "JaMarr Chase", "Kenneth Walker III" y
    "Kenneth Walker". Se quitan acentos, puntos, apostrofos y sufijos, y los
    guiones pasan a espacio. Lo que no cubre (apodos como "Hollywood Brown")
    queda sin emparejar y se cuenta, no se adivina.
    """
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = s.lower().replace("-", " ")
    s = re.sub(r"[.'`’]", "", s)
    s = re.sub(r"[^a-z ]", " ", s)
    partes = [p for p in s.split() if p not in SUFIJOS]
    return " ".join(partes)


def separar_seleccion(seleccion: str) -> tuple[str, str] | None:
    """'Patrick Mahomes Over' -> ('Patrick Mahomes', 'Over')."""
    partes = seleccion.rsplit(" ", 1)
    if len(partes) != 2 or partes[1] not in LADOS or not partes[0].strip():
        return None
    return partes[0].strip(), partes[1]


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------

def fecha_et(momento: datetime) -> date:
    """Fecha del partido en la costa este, que es la que usan las fuentes."""
    return momento.astimezone(ET).date()


def semana_nfl(momento: datetime) -> tuple[int, int]:
    """(temporada, semana) de NFL a partir de la hora de inicio.

    La semana 1 empieza el jueves siguiente al Labor Day (primer lunes de
    septiembre) y cada semana va de jueves a miercoles, lo que cubre los
    partidos de viernes en el extranjero, los de sabado de final de temporada y
    los de miercoles de Navidad.
    """
    d = fecha_et(momento)
    temporada = d.year if d.month >= 3 else d.year - 1
    septiembre = date(temporada, 9, 1)
    labor_day = septiembre + timedelta(days=(7 - septiembre.weekday()) % 7)
    jueves = labor_day + timedelta(days=3)
    return temporada, (d - jueves).days // 7 + 1


# ---------------------------------------------------------------------------
# Lineas archivadas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LineaProp:
    event_id: str
    commence: datetime
    home: str
    away: str
    market: str
    jugador: str
    lado: str
    point: float | None
    precio: float          # mediana entre casas
    precio_max: float
    n_casas: int

    @property
    def partido(self) -> str:
        return f"{self.away} @ {self.home}"


def _ultimos_precios(conn, hasta: str, filtro: str = "", params: tuple = ()) -> list:
    """Ultimo precio de cada (evento, casa, mercado, seleccion, linea) a `hasta`."""
    return conn.execute(
        f"""
        SELECT s.* FROM odds_snapshots s
        JOIN (SELECT MAX(id) AS id FROM odds_snapshots
               WHERE captured_at <= ? AND market LIKE 'player_%' {filtro}
               GROUP BY event_id, bookmaker, market, selection, point) u
          ON u.id = s.id
        """,
        (hasta, *params),
    ).fetchall()


def lineas_vigentes(
    archivo: str | Path, sport: str, ahora: datetime, min_casas: int = 2
) -> list[LineaProp]:
    """Lineas de props de partidos que aun no empiezan, al precio de `ahora`."""
    conn = sqlite3.connect(archivo)
    conn.row_factory = sqlite3.Row
    try:
        filas = _ultimos_precios(
            conn, ahora.isoformat(), "AND sport = ? AND commence_time > ?",
            (sport, ahora.isoformat()),
        )
    finally:
        conn.close()
    return _agrupar(filas, min_casas)


def precio_cierre(
    archivo: str | Path, event_id: str, market: str, seleccion: str,
    point: float | None, commence: datetime,
) -> float | None:
    """Mediana entre casas del ultimo precio antes del inicio del partido."""
    conn = sqlite3.connect(archivo)
    conn.row_factory = sqlite3.Row
    try:
        filtro = "AND event_id = ? AND market = ? AND selection = ? AND "
        filtro += "point IS NULL" if point is None else "point = ?"
        params = (event_id, market, seleccion) + (() if point is None else (point,))
        filas = _ultimos_precios(conn, commence.isoformat(), filtro, params)
    finally:
        conn.close()
    precios = [f["decimal_odds"] for f in filas]
    return statistics.median(precios) if precios else None


def _agrupar(filas, min_casas: int) -> list[LineaProp]:
    grupos: dict[tuple, list] = defaultdict(list)
    for f in filas:
        grupos[(f["event_id"], f["market"], f["selection"], f["point"])].append(f)
    salida = []
    for (event_id, market, seleccion, point), fs in grupos.items():
        partes = separar_seleccion(seleccion)
        if partes is None or len(fs) < min_casas:
            continue
        precios = [f["decimal_odds"] for f in fs]
        f0 = fs[0]
        salida.append(LineaProp(
            event_id=event_id,
            commence=datetime.fromisoformat(f0["commence_time"].replace("Z", "+00:00")),
            home=f0["home_team"], away=f0["away_team"], market=market,
            jugador=partes[0], lado=partes[1], point=point,
            precio=statistics.median(precios), precio_max=max(precios),
            n_casas=len(fs),
        ))
    return salida


# ---------------------------------------------------------------------------
# Jugadores: emparejar nombres y proyectar
# ---------------------------------------------------------------------------

@dataclass
class FichaJugador:
    player_id: str
    nombre: str
    posicion: str
    equipo: str                 # canonico, del ultimo partido registrado
    equipo_abrev: str           # tal como lo escribe la fuente de estadisticas
    ultimo_partido: date        # fecha (aproximada en NFL) del ultimo partido
    historial: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class Emparejamiento:
    ficha: FichaJugador | None
    motivo: str = ""


# Un jugador con el nombre correcto pero de otro equipo solo se acepta si jugo
# hace poco: cubre traspasos y datos atrasados, pero no a un homonimo retirado
# cuyo historial se usaria para proyectar a otra persona.
RECIENTE_DIAS = 300


def emparejar(
    fichas_por_nombre: dict[str, list[FichaJugador]], jugador: str,
    equipos: set[str], columna: str | None, fecha: date,
) -> Emparejamiento:
    """Encuentra al jugador de la linea entre los de las estadisticas.

    Con un nombre repetido (hay dos Josh Allen en la NFL: el QB y un defensivo)
    decide primero el equipo del partido y despues quien acumula la estadistica
    que se apuesta. Si aun asi no hay uno claro, NO se adivina: una prop del QB
    calculada con el historial del defensivo es un error que nadie veria.
    """
    candidatos = fichas_por_nombre.get(normalizar_nombre(jugador), [])
    if not candidatos:
        return Emparejamiento(None, "nombre no encontrado")

    del_partido = [c for c in candidatos if c.equipo in equipos]
    if len(del_partido) == 1:
        return Emparejamiento(del_partido[0])
    if len(del_partido) > 1:
        unico = _unico_por_uso(del_partido, columna)
        return Emparejamiento(unico, "" if unico else "nombre ambiguo en el partido")

    recientes = [c for c in candidatos if (fecha - c.ultimo_partido).days <= RECIENTE_DIAS]
    if not recientes:
        return Emparejamiento(None, "solo coincide alguien que no juega hace tiempo")
    unico = recientes[0] if len(recientes) == 1 else _unico_por_uso(recientes, columna)
    if unico is None:
        return Emparejamiento(None, "nombre ambiguo")
    return Emparejamiento(unico, "equipo distinto al del partido")


def _unico_por_uso(candidatos: list[FichaJugador], columna: str | None) -> FichaJugador | None:
    """El unico candidato con uso real de la estadistica, o None si hay duda."""
    if not columna:
        return None

    def uso(c: FichaJugador) -> float:
        h = c.historial.get(columna, [])[-10:]
        return sum(h) / len(h) if h else 0.0

    con_uso = [c for c in candidatos if uso(c) > 0]
    return con_uso[0] if len(con_uso) == 1 else None


# ---------------------------------------------------------------------------
# Registro de apuestas en papel
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS papel (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sport         TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    commence      TEXT NOT NULL,
    partido       TEXT NOT NULL,
    market        TEXT NOT NULL,
    jugador       TEXT NOT NULL,
    player_id     TEXT NOT NULL,
    lado          TEXT NOT NULL,
    point         REAL,
    precio        REAL NOT NULL,
    precio_max    REAL NOT NULL,
    n_casas       INTEGER NOT NULL,
    p_modelo      REAL NOT NULL,
    ev            REAL NOT NULL,
    decidido_en   TEXT NOT NULL,
    atraso        INTEGER NOT NULL DEFAULT 0,
    estado        TEXT NOT NULL DEFAULT 'pendiente',
    real          REAL,
    precio_cierre REAL,
    clv           REAL,
    calificado_en TEXT,
    unidades      REAL NOT NULL DEFAULT 1.0,
    cuota_minima  REAL,
    UNIQUE (event_id, market, player_id, lado, point)
);
CREATE INDEX IF NOT EXISTS idx_papel_estado ON papel (estado, commence);
"""

# Columnas anadidas despues de la primera version. Una base creada antes (la del
# servidor ya existe) se migra en sitio: borrarla perderia apuestas apuntadas.
MIGRACIONES = [
    ("unidades", "ALTER TABLE papel ADD COLUMN unidades REAL NOT NULL DEFAULT 1.0"),
    ("cuota_minima", "ALTER TABLE papel ADD COLUMN cuota_minima REAL"),
]


# ---------------------------------------------------------------------------
# Tamano de la apuesta y cuota minima
# ---------------------------------------------------------------------------

UNIDAD_PCT = 0.01   # 1 unidad = 1% del bankroll


def unidades_para(
    p: float, precio: float, fraccion_kelly: float = 0.25, tope: float = 2.0,
    paso: float = 0.25,
) -> float:
    """Unidades a apostar: Kelly fraccionado, con tope, redondeado hacia ABAJO.

    Un cuarto de Kelly porque la probabilidad del modelo no es la verdadera: con
    Kelly completo, un modelo que sobreestima un poco apuesta de mas justo donde
    mas se equivoca. El tope de 2 unidades limita el dano de una sola linea mal
    emparejada o vieja. Se redondea hacia abajo, al cuarto de unidad, para no
    apostar nunca por encima de lo que dice la formula; con EV positivo el
    minimo es un cuarto.
    """
    from betbot.ev.engine import kelly_fraction

    pct = kelly_fraction(p, precio, fraccion_kelly)
    if pct <= 0:
        return 0.0
    u = min(pct / UNIDAD_PCT, tope)
    return max(paso, math.floor(u / paso) * paso)


def cuota_minima(p: float, min_ev: float) -> float:
    """Cuota decimal por debajo de la cual la apuesta ya no tiene el EV minimo.

    Es lo que hace el aviso util en cualquier casa: el precio del mensaje es la
    mediana de las casas del feed, y la tuya casi nunca paga exactamente eso.
    Solo vale para la MISMA linea (4.5 no es 5.5).
    """
    return (1.0 + min_ev) / p


ESTADOS_FINALES = ("ganada", "perdida", "nula", "sin_calificar")


class RegistroPapel:
    def __init__(self, path: str | Path = "data/papel.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            existentes = {r[1] for r in c.execute("PRAGMA table_info(papel)")}
            for columna, ddl in MIGRACIONES:
                if columna not in existentes:
                    c.execute(ddl)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def apuntar(self, sport: str, linea: LineaProp, player_id: str, p: float,
                ev: float, ahora: datetime, atraso: int = 0, unidades: float = 1.0,
                cuota_min: float | None = None) -> bool:
        """Apunta una apuesta. False si ya estaba (se queda el PRIMER precio)."""
        with self._conn() as c:
            try:
                c.execute(
                    """INSERT INTO papel (sport, event_id, commence, partido, market,
                       jugador, player_id, lado, point, precio, precio_max, n_casas,
                       p_modelo, ev, decidido_en, atraso, unidades, cuota_minima)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sport, linea.event_id, linea.commence.isoformat(), linea.partido,
                     linea.market, linea.jugador, player_id, linea.lado, linea.point,
                     linea.precio, linea.precio_max, linea.n_casas, p, ev,
                     ahora.isoformat(), atraso, unidades, cuota_min),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def pendientes(self, sport: str, ahora: datetime) -> list[sqlite3.Row]:
        with self._conn() as c:
            return list(c.execute(
                "SELECT * FROM papel WHERE sport=? AND estado='pendiente' AND commence < ?",
                (sport, ahora.isoformat()),
            ))

    def cerrar(self, id_: int, estado: str, real: float | None,
               precio_cierre: float | None, precio: float, ahora: datetime) -> None:
        clv = (precio / precio_cierre - 1.0) if precio_cierre else None
        with self._conn() as c:
            c.execute(
                "UPDATE papel SET estado=?, real=?, precio_cierre=?, clv=?, "
                "calificado_en=? WHERE id=?",
                (estado, real, precio_cierre, clv, ahora.isoformat(), id_),
            )

    def todas(self, sport: str | None = None) -> list[sqlite3.Row]:
        with self._conn() as c:
            if sport:
                return list(c.execute("SELECT * FROM papel WHERE sport=? ORDER BY id", (sport,)))
            return list(c.execute("SELECT * FROM papel ORDER BY id"))


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

def unidades_de(f) -> float:
    """Unidades de una fila; las apuntadas antes de existir la columna valen 1."""
    try:
        u = f["unidades"]
    except (KeyError, IndexError):
        return 1.0
    return 1.0 if u is None else float(u)


def resultado_unidades(f) -> float:
    """Ganancia en unidades de una apuesta cerrada (0 si nula o pendiente)."""
    if f["estado"] == "ganada":
        return unidades_de(f) * (f["precio"] - 1.0)
    if f["estado"] == "perdida":
        return -unidades_de(f)
    return 0.0


def resumir(filas: list) -> dict:
    """ROI a stake plano, ROI en unidades y CLV. Incluye el tamano de muestra.

    Se dan los dos ROI porque miden cosas distintas: el plano dice si el modelo
    elige bien; el de unidades, si ademas el tamano de cada apuesta ayuda o
    estorba. Si el plano es positivo y el de unidades negativo, el modelo esta
    poniendo mas dinero justo donde se equivoca.
    """
    cerradas = [f for f in filas if f["estado"] in ("ganada", "perdida")]
    ganancia = sum((f["precio"] - 1.0) if f["estado"] == "ganada" else -1.0 for f in cerradas)
    arriesgadas = sum(unidades_de(f) for f in cerradas)
    ganancia_u = sum(resultado_unidades(f) for f in cerradas)
    con_clv = [f["clv"] for f in filas if f["clv"] is not None and f["estado"] != "nula"]
    return {
        "apuntadas": len(filas),
        "pendientes": sum(1 for f in filas if f["estado"] == "pendiente"),
        "calificadas": len(cerradas),
        "ganadas": sum(1 for f in cerradas if f["estado"] == "ganada"),
        "nulas": sum(1 for f in filas if f["estado"] == "nula"),
        "sin_calificar": sum(1 for f in filas if f["estado"] == "sin_calificar"),
        "ganancia": ganancia,
        "roi": ganancia / len(cerradas) if cerradas else None,
        "ganancia_u": ganancia_u,
        "unidades_arriesgadas": arriesgadas,
        "roi_u": ganancia_u / arriesgadas if arriesgadas else None,
        "ev_medio": (sum(f["ev"] for f in cerradas) / len(cerradas)) if cerradas else None,
        "clv_medio": (sum(con_clv) / len(con_clv)) if con_clv else None,
        "clv_positivo": (sum(1 for x in con_clv if x > 0) / len(con_clv)) if con_clv else None,
        "n_clv": len(con_clv),
    }


def resultado_de(lado: str, point: float | None, real: float) -> str:
    """ganada / perdida / nula (empate exacto con la linea)."""
    if lado in ("Yes", "No"):
        anoto = real > 0
        return "ganada" if anoto == (lado == "Yes") else "perdida"
    if point is None:
        return "nula"
    if real == point:
        return "nula"
    return "ganada" if (real > point) == (lado == "Over") else "perdida"


def ahora_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ApuestaNueva:
    linea: LineaProp
    p: float
    ev: float
    unidades: float
    cuota_minima: float


@dataclass
class InformeGeneracion:
    lineas: int = 0
    apuntadas: int = 0
    ya_apuntadas: int = 0
    sin_valor: int = 0
    descartes: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    nuevas: list[ApuestaNueva] = field(default_factory=list)

    def descartar(self, motivo: str) -> None:
        self.descartes[motivo] += 1


def generar(
    adaptador, archivo: str | Path, registro: RegistroPapel, ahora: datetime,
    min_ev: float = 0.03, max_ev: float = 0.25, max_atraso: int = 1,
    fraccion_kelly: float = 0.25, tope_unidades: float = 2.0,
) -> InformeGeneracion:
    """Apunta en papel las lineas vigentes con EV suficiente.

    `max_ev` no es un techo de ambicion sino un detector de errores: un EV del
    30% en una prop casi siempre significa que el nombre se emparejo con otro
    jugador, que la linea esta vieja o que el jugador esta lesionado y el modelo
    no lo sabe. Esas lineas se cuentan aparte en vez de apuntarse.
    """
    inf = InformeGeneracion()
    for linea in lineas_vigentes(archivo, adaptador.sport, ahora):
        inf.lineas += 1
        atraso = adaptador.atraso(linea)
        if atraso > max_atraso:
            inf.descartar(f"datos atrasados ({atraso})")
            continue
        equipos = {adaptador.canonico(linea.home), adaptador.canonico(linea.away)}
        emp = emparejar(adaptador.fichas_por_nombre, linea.jugador,
                        equipos, adaptador.columna(linea.market),
                        fecha_et(linea.commence))
        if emp.ficha is None:
            inf.descartar(emp.motivo)
            continue
        v = adaptador.prob(emp.ficha, linea)
        if v.p is None:
            inf.descartar(v.motivo)
            continue
        ev = v.p * linea.precio - 1.0
        if ev < min_ev:
            inf.sin_valor += 1
            continue
        objecion = adaptador.filtrar_por_sesgo(linea.market, linea.lado, v.p)
        if objecion:
            inf.descartar(objecion)
            continue
        if ev > max_ev:
            inf.descartar(f"EV sospechoso (>{max_ev:.0%})")
            continue
        unidades = unidades_para(v.p, linea.precio, fraccion_kelly, tope_unidades)
        minima = cuota_minima(v.p, min_ev)
        if registro.apuntar(adaptador.sport, linea, emp.ficha.player_id, v.p, ev,
                            ahora, atraso, unidades, minima):
            inf.apuntadas += 1
            inf.nuevas.append(ApuestaNueva(linea, v.p, ev, unidades, minima))
        else:
            inf.ya_apuntadas += 1
    return inf


def calificar(
    adaptador, archivo: str | Path, registro: RegistroPapel, ahora: datetime,
    caducidad_dias: int = 30, detalle: list | None = None,
) -> dict[str, int]:
    """Califica las apuestas cuyos partidos ya empezaron.

    Lo que siga sin estadisticas `caducidad_dias` despues del partido se marca
    `sin_calificar` y sale de las cuentas: dejarlo pendiente para siempre lo
    esconderia, y contarlo como perdido o nulo seria inventar el resultado.

    Si se pasa `detalle`, se le anade (fila, estado, real, clv) por cada apuesta
    cerrada, para poder contar que paso con cada una y no solo cuantas.
    """
    cuenta: dict[str, int] = defaultdict(int)
    for fila in registro.pendientes(adaptador.sport, ahora):
        commence = datetime.fromisoformat(fila["commence"])
        estado, real = adaptador.calificar(fila)
        if estado == "pendiente":
            if ahora - commence > timedelta(days=caducidad_dias):
                registro.cerrar(fila["id"], "sin_calificar", None, None, fila["precio"], ahora)
                cuenta["sin_calificar"] += 1
            else:
                cuenta["pendiente"] += 1
            continue
        seleccion = f"{fila['jugador']} {fila['lado']}"
        cierre = precio_cierre(archivo, fila["event_id"], fila["market"], seleccion,
                               fila["point"], commence)
        if estado == "real":
            estado = resultado_de(fila["lado"], fila["point"], real)
        registro.cerrar(fila["id"], estado, real, cierre, fila["precio"], ahora)
        cuenta[estado] += 1
        if detalle is not None:
            clv = (fila["precio"] / cierre - 1.0) if cierre else None
            detalle.append((fila, estado, real, clv))
    return cuenta
