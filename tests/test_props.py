"""Pruebas del modelo de props y su backtest.

Lo critico aqui son dos cosas. Primera: que `prob_over` traduzca bien entre el
espacio del cociente y el de la linea, porque un error de signo o de borde ahi
invierte las apuestas sin que nada falle a gritos. Segunda: que el backtest no
tenga fuga temporal — aprender del partido que se esta prediciendo daria
metricas preciosas y falsas, que es exactamente el fallo que este proyecto
lleva evitando desde el principio.
"""

from __future__ import annotations

import pytest

from betbot.backtest.props import (
    ResultadoProps,
    lineas_sinteticas,
    walk_forward_props,
)
from betbot.models.props import PropsModel, Proyeccion


def proy(media=100.0, cocientes=None):
    c = tuple(sorted(cocientes if cocientes is not None else
                     [i / 100 for i in range(1, 201)]))
    return Proyeccion(media=media, cocientes=c, n_partidos=10)


# --- Proyeccion.prob_over --------------------------------------------------

def test_prob_over_en_la_mediana_ronda_la_mitad():
    p = proy(media=100.0)  # cocientes uniformes en (0.01, 2.00)
    assert 0.45 < p.prob_over(100.0) < 0.55


def test_prob_over_decrece_con_la_linea():
    p = proy(media=100.0)
    valores = [p.prob_over(x) for x in (50.0, 100.0, 150.0, 190.0)]
    assert valores == sorted(valores, reverse=True)


def test_over_y_under_suman_uno():
    p = proy(media=100.0)
    for linea in (10.0, 100.0, 175.0):
        assert p.prob_over(linea) + p.prob_under(linea) == pytest.approx(1.0)


def test_nunca_devuelve_certeza():
    """Una probabilidad de 0 o 1 en una apuesta es siempre mentira."""
    p = proy(media=100.0)
    assert 0.0 < p.prob_over(1000.0) < 0.01
    assert 0.99 < p.prob_over(0.5) < 1.0


def test_empate_exacto_cuenta_como_no_superado():
    """Con linea entera, igualarla no es superarla."""
    p = proy(media=10.0, cocientes=[1.0] * 100)  # siempre exactamente 10
    assert p.prob_over(10.0) < 0.02


def test_media_cero_no_revienta():
    p = Proyeccion(media=0.0, cocientes=(1.0,), n_partidos=5)
    assert p.prob_over(5.0) == 0.0


# --- PIT -------------------------------------------------------------------

def test_pit_concuerda_con_el_over_salvo_suavizado():
    """PIT y 1-P(over) miden lo mismo; solo difieren en el suavizado.

    `prob_over` suaviza (una apuesta nunca puede tener probabilidad 0 o 1); el
    PIT no debe suavizar, porque su gracia es comparar contra la uniforme. La
    diferencia tiene que ser del orden de 1/n, no estructural.
    """
    p = proy(media=100.0)
    n = len(p.cocientes)
    assert p.cuantil_del_resultado(150.0) == pytest.approx(
        1.0 - p.prob_over(150.0), abs=2.0 / n
    )


def test_pit_aleatorizado_reparte_la_masa_del_empate():
    """Con soporte discreto, el empate exacto se reparte con el uniforme.

    Sin esto, los touchdowns de pase (7 valores posibles) darian un histograma
    deforme que acusa al modelo de un defecto que es de la metrica.
    """
    p = proy(media=10.0, cocientes=[0.5] * 50 + [1.0] * 50)   # empate en 10
    bajo = p.cuantil_del_resultado(10.0, u=0.0)
    alto = p.cuantil_del_resultado(10.0, u=1.0)
    medio = p.cuantil_del_resultado(10.0, u=0.5)
    assert bajo == pytest.approx(0.5)     # F(x-) : solo los 0.5
    assert alto == pytest.approx(1.0)     # F(x)  : todos
    assert bajo < medio < alto


def test_pit_uniforme_da_desviacion_baja():
    r = ResultadoProps(mercado="x", predicciones=1000)
    r.pit = [i / 1000 for i in range(1000)]
    assert r.desviacion_uniforme < 0.5


def test_pit_amontonado_da_desviacion_alta():
    r = ResultadoProps(mercado="x", predicciones=1000)
    r.pit = [0.05] * 500 + [0.95] * 500   # todo en las colas
    assert r.desviacion_uniforme > 5.0


# --- lineas sinteticas -----------------------------------------------------

def test_lineas_sinteticas_son_medios_puntos():
    for linea in lineas_sinteticas(63.2):
        assert linea % 1 == 0.5


def test_lineas_sinteticas_rodean_la_proyeccion():
    ls = lineas_sinteticas(100.0)
    assert min(ls) < 100.0 < max(ls)


def test_lineas_sinteticas_nunca_negativas():
    assert all(x > 0 for x in lineas_sinteticas(0.4))


# --- modelo ----------------------------------------------------------------

def test_no_proyecta_sin_historial_suficiente():
    m = PropsModel()
    assert m.proyectar([200.0] * 3, "QB", "player_pass_yds") is None


def test_no_proyecta_sin_distribucion_suficiente():
    """Sin cientos de cocientes las colas son ruido; mejor callarse."""
    m = PropsModel()
    assert m.proyectar([250.0] * 10, "QB", "player_pass_yds") is None


def test_no_proyecta_a_jugadores_sin_uso():
    """Ningun libro cotiza al tercer receptor, y el cociente se vuelve inestable."""
    m = PropsModel(min_cocientes=1)
    for _ in range(50):
        m.observar([250.0] * 10, "QB", "player_pass_yds", 250.0)
    assert m.proyectar([3.0] * 10, "QB", "player_pass_yds") is None


def test_la_media_movil_pesa_lo_reciente():
    m = PropsModel(decay=0.5)
    subiendo = m._media_movil([0.0, 0.0, 0.0, 100.0])
    bajando = m._media_movil([100.0, 0.0, 0.0, 0.0])
    assert subiendo > bajando


def test_encogimiento_acerca_a_la_media_posicional():
    """Con poco historial, el jugador debe parecerse mas a su posicion."""
    m = PropsModel(min_cocientes=1, fuerza_encogimiento=3.0)
    for _ in range(300):                       # la posicion promedia 200
        m.observar([200.0] * 10, "QB", "player_pass_yds", 200.0)
    corto = m.proyectar([400.0] * 4, "QB", "player_pass_yds")
    largo = m.proyectar([400.0] * 40, "QB", "player_pass_yds")
    assert corto is not None and largo is not None
    assert corto.media < largo.media           # el corto esta mas encogido
    assert corto.media > 200.0                 # pero no anula al jugador


def test_los_cocientes_quedan_ordenados():
    m = PropsModel(min_cocientes=1)
    for real in (400.0, 100.0, 250.0, 50.0, 300.0):
        m.observar([200.0] * 10, "QB", "player_pass_yds", real)
    m.ordenar()
    lista = m._cocientes[("QB", "player_pass_yds")]
    assert lista == sorted(lista)


# --- walk-forward: la fuga temporal ---------------------------------------

def fila(pid, season, week, yardas, pos="QB"):
    return {
        "player_id": pid, "position": pos, "season": season, "week": week,
        "passing_yards": yardas, "passing_tds": 0.0, "rushing_yards": 0.0,
        "receiving_yards": 0.0, "receptions": 0.0,
    }


def test_el_backtest_no_puede_ver_el_partido_que_predice():
    """Un jugador con historial plano y un ultimo partido extremo.

    Si el modelo hubiera visto ese partido al proyectarlo, el resultado caeria
    en mitad de la distribucion. Como no lo ve, tiene que caer en la cola.
    """
    m = PropsModel(min_cocientes=1, min_partidos=4)
    # Historial variado para que la distribucion de cocientes no sea degenerada.
    yardas = [150.0, 250.0, 180.0, 220.0, 160.0, 240.0, 200.0, 190.0,
              210.0, 170.0, 230.0]
    filas = [fila("p1", 2020, w, y) for w, y in enumerate(yardas, start=1)]
    filas.append(fila("p1", 2020, 12, 900.0))   # partido imposible al final
    res = walk_forward_props(filas, mercados=("player_pass_yds",), modelo=m)
    r = res["player_pass_yds"]
    assert r.predicciones > 1
    # El ultimo resultado es cuatro veces su media: tiene que quedar en el
    # extremo alto. Si el modelo lo hubiera visto antes de predecirlo, estaria
    # dentro del rango en vez de fuera.
    assert r.pit[-1] >= 0.95


def test_el_backtest_siempre_predice_antes_de_aprender():
    """La propiedad anti-fuga, comprobada directamente sobre el orden de llamadas."""
    orden: list[str] = []

    class Espia(PropsModel):
        def proyectar(self, historial, posicion, mercado):
            orden.append("predice")
            return super().proyectar(historial, posicion, mercado)

        def observar(self, historial, posicion, mercado, real):
            orden.append("aprende")
            return super().observar(historial, posicion, mercado, real)

    filas = [fila("p1", 2020, w, 200.0 + w) for w in range(1, 10)]
    walk_forward_props(filas, mercados=("player_pass_yds",),
                       modelo=Espia(min_cocientes=1, min_partidos=4))
    assert orden, "el espia no registro nada"
    # Cada partido genera exactamente un par, y siempre en este orden.
    assert orden == ["predice", "aprende"] * (len(orden) // 2)


def test_primera_temporada_puede_excluirse_de_la_evaluacion():
    m = PropsModel(min_cocientes=1, min_partidos=4)
    filas = [fila("p1", 2019, w, 200.0) for w in range(1, 15)]
    filas += [fila("p1", 2020, w, 200.0) for w in range(1, 15)]
    todo = walk_forward_props(filas, mercados=("player_pass_yds",), modelo=PropsModel(
        min_cocientes=1, min_partidos=4))
    recorte = walk_forward_props(filas, mercados=("player_pass_yds",), modelo=m,
                                 desde_temporada=2020)
    assert recorte["player_pass_yds"].predicciones < todo["player_pass_yds"].predicciones
    assert recorte["player_pass_yds"].predicciones > 0


def test_jugadores_distintos_no_comparten_historial():
    m = PropsModel(min_cocientes=1, min_partidos=4)
    filas = []
    for w in range(1, 10):
        filas.append(fila("alto", 2020, w, 400.0))
        filas.append(fila("bajo", 2020, w, 150.0))
    walk_forward_props(filas, mercados=("player_pass_yds",), modelo=m)
    alto = m.proyectar([400.0] * 9, "QB", "player_pass_yds")
    bajo = m.proyectar([150.0] * 9, "QB", "player_pass_yds")
    assert alto is not None and bajo is not None
    assert alto.media > bajo.media


def test_calibracion_agrupa_por_probabilidad():
    r = ResultadoProps(mercado="x", predicciones=0)
    r.prob_y_resultado = [(0.05, False)] * 100 + [(0.95, True)] * 100
    cal = r.calibracion()
    assert len(cal) == 2
    assert cal[0][1] == pytest.approx(0.0)
    assert cal[1][1] == pytest.approx(1.0)


def test_brier_y_logloss_premian_al_modelo_acertado():
    bueno = ResultadoProps(mercado="x", predicciones=0)
    bueno.prob_y_resultado = [(0.9, True)] * 100
    malo = ResultadoProps(mercado="x", predicciones=0)
    malo.prob_y_resultado = [(0.1, True)] * 100
    assert bueno.brier < malo.brier
    assert bueno.log_score_medio < malo.log_score_medio


# --- correccion de calibracion --------------------------------------------

def test_la_correccion_solo_toca_los_mercados_validados():
    """Los tres mercados de yardas no llevan correccion: no tenian defecto."""
    from betbot.models.props import CALIBRACION

    assert set(CALIBRACION) == {"player_pass_tds"}
    assert CALIBRACION["player_pass_tds"] < 1.0   # encoge hacia la base


def test_la_correccion_encoge_hacia_la_mitad():
    from betbot.models.props import _corrige

    assert _corrige(0.80, "player_pass_tds") < 0.80
    assert _corrige(0.20, "player_pass_tds") > 0.20
    assert _corrige(0.50, "player_pass_tds") == pytest.approx(0.50)


def test_sin_correccion_la_probabilidad_pasa_intacta():
    from betbot.models.props import _corrige

    for p in (0.1, 0.5, 0.9):
        assert _corrige(p, "player_rush_yds") == p
        assert _corrige(p, "mercado_inventado") == p


def test_la_correccion_llega_a_prob_over():
    cocientes = tuple(sorted(i / 100 for i in range(1, 201)))
    crudo = Proyeccion(media=100.0, cocientes=cocientes, n_partidos=10, mercado="")
    corregido = Proyeccion(
        media=100.0, cocientes=cocientes, n_partidos=10, mercado="player_pass_tds"
    )
    # En una linea baja la probabilidad de over es alta; la correccion la baja.
    assert corregido.prob_over(40.0) < crudo.prob_over(40.0)
    assert corregido.prob_over(40.0) > 0.5


def test_over_y_under_siguen_sumando_uno_con_correccion():
    cocientes = tuple(sorted(i / 100 for i in range(1, 201)))
    p = Proyeccion(media=100.0, cocientes=cocientes, n_partidos=10,
                   mercado="player_pass_tds")
    for linea in (30.0, 100.0, 170.0):
        assert p.prob_over(linea) + p.prob_under(linea) == pytest.approx(1.0)


def test_el_pit_no_lleva_correccion():
    """El PIT diagnostica la distribucion cruda; corregirlo ocultaria el defecto."""
    cocientes = tuple(sorted(i / 100 for i in range(1, 201)))
    crudo = Proyeccion(media=100.0, cocientes=cocientes, n_partidos=10, mercado="")
    corregido = Proyeccion(
        media=100.0, cocientes=cocientes, n_partidos=10, mercado="player_pass_tds"
    )
    assert crudo.cuantil_del_resultado(150.0) == corregido.cuantil_del_resultado(150.0)


def test_recepciones_marcado_como_cola_alta_dudosa():
    """Defecto real que un parametro global no arregla: se avisa, no se tapa."""
    p = Proyeccion(media=5.0, cocientes=(1.0,), n_partidos=10,
                   mercado="player_receptions")
    assert p.cola_alta_dudosa
    assert not Proyeccion(media=5.0, cocientes=(1.0,), n_partidos=10,
                          mercado="player_rush_yds").cola_alta_dudosa


def test_proyectar_etiqueta_el_mercado():
    m = PropsModel(min_cocientes=1)
    for _ in range(50):
        m.observar([250.0] * 10, "QB", "player_pass_yds", 250.0)
    proy = m.proyectar([250.0] * 10, "QB", "player_pass_yds")
    assert proy is not None and proy.mercado == "player_pass_yds"


def test_progreso_se_llama_una_vez_por_temporada():
    vistas: list[int] = []
    filas = [fila("p1", t, w, 200.0) for t in (2019, 2020) for w in range(1, 6)]
    walk_forward_props(filas, mercados=("player_pass_yds",),
                       modelo=PropsModel(min_cocientes=1), progreso=vistas.append)
    assert vistas == [2019, 2020]


# --- marca de calibracion -------------------------------------------------

def test_un_cubo_de_una_prediccion_no_se_marca_como_desviado():
    """El caso real: 70% -> 100% con n=1 salia como defecto del modelo."""
    from betbot.backtest.props import marca_calibracion

    assert "desviado" not in marca_calibracion(0.70, 1.0, 1)
    assert "muestra chica" in marca_calibracion(0.70, 1.0, 1)


def test_hueco_grande_con_muestra_grande_si_se_marca():
    from betbot.backtest.props import marca_calibracion

    # receptions en la cola alta: 63.2% -> 59.1% con n=7.903
    assert "desviado" in marca_calibracion(0.632, 0.591, 7903)


def test_hueco_dentro_del_ruido_no_se_marca():
    from betbot.backtest.props import marca_calibracion

    # rush_yds: 50.2% -> 45.4% con n=295. Dos errores estandar son ~5.8 pts.
    assert marca_calibracion(0.502, 0.454, 295) == ""


def test_diferencia_minima_en_cubo_enorme_no_se_marca():
    """Con n gigante, 1 punto es estadisticamente real pero irrelevante."""
    from betbot.backtest.props import marca_calibracion

    assert marca_calibracion(0.345, 0.354, 19959) == ""
