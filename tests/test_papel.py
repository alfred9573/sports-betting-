"""Pruebas de las apuestas en papel.

Lo que protegen: que un nombre no se empareje con OTRO jugador (el error mas
caro y el mas silencioso), que el precio tomado sea la mediana y no el mejor,
que se decida con el precio de ese momento y se compare con el de cierre, y que
un jugador que no juega anule la apuesta en vez de contarla.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from betbot.collect import OddsArchive, PriceRow
from betbot.papel import (
    FichaJugador,
    LineaProp,
    RegistroPapel,
    calificar,
    emparejar,
    generar,
    lineas_vigentes,
    normalizar_nombre,
    precio_cierre,
    resultado_de,
    resumir,
    semana_nfl,
    separar_seleccion,
)
from betbot.papel_deportes import Adaptador, Veredicto

KO = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
NFL = "americanfootball_nfl"


# --- nombres ----------------------------------------------------------------

@pytest.mark.parametrize("casa,estadisticas", [
    ("D.J. Moore", "DJ Moore"),
    ("Ja'Marr Chase", "JaMarr Chase"),
    ("Kenneth Walker III", "Kenneth Walker"),
    ("Nikola Jokić", "Nikola Jokic"),
    ("Amon-Ra St. Brown", "Amon Ra St Brown"),
    ("Marvin Harrison Jr.", "Marvin Harrison"),
])
def test_grafias_distintas_del_mismo_jugador_coinciden(casa, estadisticas):
    assert normalizar_nombre(casa) == normalizar_nombre(estadisticas)


def test_nombres_distintos_no_coinciden():
    assert normalizar_nombre("Josh Allen") != normalizar_nombre("Josh Allan")


def test_separar_seleccion():
    assert separar_seleccion("Patrick Mahomes Over") == ("Patrick Mahomes", "Over")
    assert separar_seleccion("Kenneth Walker III Under") == ("Kenneth Walker III", "Under")
    assert separar_seleccion("Travis Kelce Yes") == ("Travis Kelce", "Yes")
    assert separar_seleccion("Kansas City Chiefs") is None
    assert separar_seleccion("Over") is None


# --- calendario ---------------------------------------------------------------

def test_semana_nfl():
    # Labor Day 2026 = 7 sep; la semana 1 empieza el jueves 10.
    assert semana_nfl(datetime(2026, 9, 11, 0, 20, tzinfo=UTC)) == (2026, 1)   # jue noche ET
    assert semana_nfl(datetime(2026, 9, 13, 17, 0, tzinfo=UTC)) == (2026, 1)   # domingo
    # Lunes 20:15 ET = martes 00:15 UTC: sigue siendo la semana 1.
    assert semana_nfl(datetime(2026, 9, 15, 0, 15, tzinfo=UTC)) == (2026, 1)
    assert semana_nfl(datetime(2026, 9, 17, 23, 0, tzinfo=UTC)) == (2026, 2)
    # Enero pertenece a la temporada que empezo en septiembre.
    assert semana_nfl(datetime(2027, 1, 3, 18, 0, tzinfo=UTC))[0] == 2026


# --- resultado ------------------------------------------------------------------

def test_resultado_de():
    assert resultado_de("Over", 4.5, 5) == "ganada"
    assert resultado_de("Over", 4.5, 4) == "perdida"
    assert resultado_de("Under", 4.5, 4) == "ganada"
    assert resultado_de("Over", 5.0, 5) == "nula"          # empate exacto: push
    assert resultado_de("Yes", None, 1) == "ganada"
    assert resultado_de("Yes", None, 0) == "perdida"
    assert resultado_de("No", None, 0) == "ganada"


# --- emparejar ----------------------------------------------------------------

def ficha(pid, nombre, equipo, pase=0.0, ultimo=date(2026, 9, 7)):
    return FichaJugador(pid, nombre, "QB", equipo, equipo[:3].upper(), ultimo,
                        historial={"passing_yards": [pase] * 10})


def indice(*fichas):
    idx: dict = {}
    for f in fichas:
        idx.setdefault(normalizar_nombre(f.nombre), []).append(f)
    return idx


HOY = date(2026, 9, 13)


def test_homonimos_se_resuelven_por_equipo():
    idx = indice(ficha("qb", "Josh Allen", "Buffalo Bills", 250),
                 ficha("lb", "Josh Allen", "Jacksonville Jaguars"))
    e = emparejar(idx, "Josh Allen", {"Buffalo Bills", "Miami Dolphins"}, "passing_yards", HOY)
    assert e.ficha.player_id == "qb"
    e = emparejar(idx, "Josh Allen", {"Jacksonville Jaguars", "Houston Texans"},
                  "passing_yards", HOY)
    assert e.ficha.player_id == "lb"


def test_homonimos_fuera_del_partido_se_resuelven_por_uso():
    idx = indice(ficha("qb", "Josh Allen", "Buffalo Bills", 250),
                 ficha("lb", "Josh Allen", "Jacksonville Jaguars", 0))
    e = emparejar(idx, "Josh Allen", {"Otro", "Equipo"}, "passing_yards", HOY)
    assert e.ficha.player_id == "qb" and "equipo distinto" in e.motivo


def test_homonimos_sin_forma_de_distinguir_no_se_adivinan():
    idx = indice(ficha("a", "Mike Williams", "Equipo A", 50),
                 ficha("b", "Mike Williams", "Equipo B", 60))
    assert emparejar(idx, "Mike Williams", {"Otro", "Mas"}, "passing_yards", HOY).ficha is None


def test_un_homonimo_retirado_no_se_usa():
    """Su historial proyectaria a otra persona."""
    idx = indice(ficha("viejo", "Adrian Peterson", "Equipo A", 80, ultimo=date(2021, 12, 1)))
    e = emparejar(idx, "Adrian Peterson", {"Otro", "Mas"}, "passing_yards", HOY)
    assert e.ficha is None


def test_nombre_desconocido():
    assert emparejar({}, "Nadie", set(), None, HOY).motivo == "nombre no encontrado"


# --- archivo de lineas ----------------------------------------------------------

def precio(casa, jugador, lado, valor, point=4.5, market="player_receptions",
           commence="2026-09-13T17:00:00Z", event="ev1"):
    return PriceRow(event, NFL, commence, "Kansas City Chiefs", "Denver Broncos",
                    casa, market, f"{jugador} {lado}", point, valor)


@pytest.fixture
def archivo(tmp_path):
    a = OddsArchive(tmp_path / "a.db")
    a.archivar([precio("dk", "Travis Kelce", "Over", 1.80),
                precio("fd", "Travis Kelce", "Over", 1.90),
                precio("mgm", "Travis Kelce", "Over", 2.40),       # casa fuera de mercado
                precio("dk", "Solo Una", "Over", 2.00)],            # una sola casa
               captured_at=KO - timedelta(days=1))
    a.archivar([precio("dk", "Travis Kelce", "Over", 1.70),
                precio("fd", "Travis Kelce", "Over", 1.72)],
               captured_at=KO - timedelta(minutes=30))
    a.archivar([precio("dk", "Travis Kelce", "Over", 1.50)],        # ya empezado
               captured_at=KO + timedelta(minutes=30))
    return tmp_path / "a.db"


def test_el_precio_tomado_es_la_mediana_no_el_mejor(archivo):
    (linea,) = lineas_vigentes(archivo, NFL, KO - timedelta(hours=20))
    assert linea.precio == pytest.approx(1.90)     # mediana de 1.80 / 1.90 / 2.40
    assert linea.precio_max == pytest.approx(2.40)
    assert linea.n_casas == 3


def test_lineas_de_una_sola_casa_no_cuentan(archivo):
    lineas = lineas_vigentes(archivo, NFL, KO - timedelta(hours=20))
    assert all(ln.jugador != "Solo Una" for ln in lineas)


def test_se_usa_el_precio_de_ese_momento(archivo):
    """Lo que paso despues de `ahora` no puede influir en la decision."""
    (antes,) = lineas_vigentes(archivo, NFL, KO - timedelta(hours=20))
    (despues,) = lineas_vigentes(archivo, NFL, KO - timedelta(minutes=10))
    assert antes.precio == pytest.approx(1.90)
    assert despues.precio == pytest.approx(1.72)   # mediana de 1.70 / 1.72 / 2.40


def test_partidos_empezados_no_se_ofrecen(archivo):
    assert lineas_vigentes(archivo, NFL, KO + timedelta(minutes=5)) == []


def test_precio_de_cierre_ignora_lo_posterior_al_inicio(archivo):
    cierre = precio_cierre(archivo, "ev1", "player_receptions", "Travis Kelce Over", 4.5, KO)
    assert cierre == pytest.approx(1.72)


# --- registro y orquestacion ----------------------------------------------------

class Falso(Adaptador):
    """Adaptador controlado: la probabilidad y el resultado los fija el test."""

    def __init__(self, p=0.60, estado=("real", 6.0), atraso_=0, ficha_=None):
        super().__init__(sport=NFL)
        self.p, self.estado, self.atraso_ = p, estado, atraso_
        f = ficha_ or FichaJugador("kelce", "Travis Kelce", "TE", "Kansas City Chiefs",
                                   "KC", date(2026, 9, 7))
        self.fichas_por_id = {f.player_id: f}
        self.indexar()

    def canonico(self, equipo):
        return equipo

    def columna(self, market):
        return None

    def prob(self, ficha, linea):
        return Veredicto(self.p)

    def atraso(self, linea):
        return self.atraso_

    def calificar(self, fila):
        return self.estado


def test_generar_apunta_con_ev_y_no_duplica(archivo, tmp_path):
    reg = RegistroPapel(tmp_path / "p.db")
    ahora = KO - timedelta(hours=20)
    inf = generar(Falso(p=0.60), archivo, reg, ahora)       # 0.60 * 1.90 - 1 = +14%
    assert inf.apuntadas == 1
    assert inf.nuevas[0][2] == pytest.approx(0.14)
    assert generar(Falso(p=0.60), archivo, reg, ahora).ya_apuntadas == 1
    (fila,) = reg.todas()
    assert fila["precio"] == pytest.approx(1.90)


def test_sin_ev_no_se_apunta(archivo, tmp_path):
    inf = generar(Falso(p=0.50), archivo, RegistroPapel(tmp_path / "p.db"),
                  KO - timedelta(hours=20))
    assert inf.apuntadas == 0 and inf.sin_valor == 1


def test_ev_absurdo_se_trata_como_error(tmp_path):
    """Un EV del 50% en una prop es casi siempre un emparejamiento o una linea mal."""
    a = OddsArchive(tmp_path / "a.db")
    a.archivar([precio(c, "Travis Kelce", "Over", 1.90, point=40.5,
                       market="player_reception_yds") for c in ("dk", "fd")],
               captured_at=KO - timedelta(days=1))
    inf = generar(Falso(p=0.80), tmp_path / "a.db", RegistroPapel(tmp_path / "p.db"),
                  KO - timedelta(hours=20))
    assert inf.apuntadas == 0
    assert any("sospechoso" in m for m in inf.descartes)


def test_datos_atrasados_bloquean(archivo, tmp_path):
    inf = generar(Falso(p=0.60, atraso_=2), archivo, RegistroPapel(tmp_path / "p.db"),
                  KO - timedelta(hours=20))
    assert inf.apuntadas == 0 and any("atrasados" in m for m in inf.descartes)


def test_cola_alta_dudosa_no_se_apuesta():
    assert Adaptador.filtrar_por_sesgo("player_receptions", "Over", 0.65)
    assert not Adaptador.filtrar_por_sesgo("player_receptions", "Over", 0.55)
    assert Adaptador.filtrar_por_sesgo("player_anytime_td", "Yes", 0.45)
    assert not Adaptador.filtrar_por_sesgo("player_anytime_td", "No", 0.70)


def test_calificar_con_cierre_y_clv(archivo, tmp_path):
    reg = RegistroPapel(tmp_path / "p.db")
    generar(Falso(p=0.60), archivo, reg, KO - timedelta(hours=20))
    assert calificar(Falso(), archivo, reg, KO - timedelta(hours=1)) == {}   # aun no empieza
    assert calificar(Falso(estado=("real", 6.0)), archivo, reg, KO + timedelta(days=2)) == {
        "ganada": 1}
    (f,) = reg.todas()
    assert f["precio_cierre"] == pytest.approx(1.72)
    assert f["clv"] == pytest.approx(1.90 / 1.72 - 1)


def test_jugador_que_no_juega_anula(archivo, tmp_path):
    reg = RegistroPapel(tmp_path / "p.db")
    generar(Falso(p=0.60), archivo, reg, KO - timedelta(hours=20))
    calificar(Falso(estado=("nula", None)), archivo, reg, KO + timedelta(days=2))
    assert reg.todas()[0]["estado"] == "nula"


def test_sin_estadisticas_queda_pendiente_y_luego_sin_calificar(archivo, tmp_path):
    reg = RegistroPapel(tmp_path / "p.db")
    generar(Falso(p=0.60), archivo, reg, KO - timedelta(hours=20))
    pendiente = Falso(estado=("pendiente", None))
    assert calificar(pendiente, archivo, reg, KO + timedelta(days=3)) == {"pendiente": 1}
    assert calificar(pendiente, archivo, reg, KO + timedelta(days=40)) == {"sin_calificar": 1}


def test_resumen():
    filas = [
        {"estado": "ganada", "precio": 2.0, "ev": 0.05, "clv": 0.04},
        {"estado": "perdida", "precio": 1.9, "ev": 0.05, "clv": -0.02},
        {"estado": "nula", "precio": 1.9, "ev": 0.05, "clv": 0.01},
        {"estado": "pendiente", "precio": 1.9, "ev": 0.05, "clv": None},
    ]
    r = resumir(filas)
    assert r["calificadas"] == 2 and r["ganancia"] == pytest.approx(0.0)
    assert r["roi"] == pytest.approx(0.0)
    assert r["clv_medio"] == pytest.approx(0.01)       # las nulas no cuentan
    assert r["clv_positivo"] == pytest.approx(0.5)


def test_linea_prop_partido():
    ln = LineaProp("e", KO, "Kansas City Chiefs", "Denver Broncos", "m", "j", "Over",
                   1.5, 1.9, 1.9, 2)
    assert ln.partido == "Denver Broncos @ Kansas City Chiefs"
