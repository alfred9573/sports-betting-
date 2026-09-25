"""Pruebas de anytime TD.

Tres propiedades que no se pueden perder: que los TDs de pase no cuenten (un QB
que lanza 3 TDs no "anoto"), que no haya fuga temporal, y que la linea base se
mida sobre el MISMO universo que el modelo. La primera version comparaba contra
la tasa de toda la plantilla, suplentes incluidos, e inflaba la mejora.
"""

from __future__ import annotations

import math

import pytest

from betbot.backtest.anytime_td import (
    PrediccionTD,
    brier,
    calibracion,
    skill,
    walk_forward_anytime_td,
)
from betbot.models.anytime_td import AnytimeTDModel, tds_de, toques_de


def fila(pid, season, week, pos="RB", carries=15.0, targets=3.0,
         rush_td=0.0, rec_td=0.0, pass_td=0.0):
    return {
        "player_id": pid, "position": pos, "season": season, "week": week,
        "carries": carries, "targets": targets, "rushing_tds": rush_td,
        "receiving_tds": rec_td, "passing_tds": pass_td,
    }


def modelo_calentado(pos="RB", tds_por_partido=0.3, toques=18.0, n=200):
    m = AnytimeTDModel()
    for i in range(n):
        m.observar(pos, 1.0 if i % 10 < tds_por_partido * 10 else 0.0, toques)
    return m


# --- definicion de TD -----------------------------------------------------

def test_los_tds_de_pase_no_cuentan():
    assert tds_de(fila("q", 2020, 1, pos="QB", pass_td=3.0)) == 0.0
    assert tds_de(fila("q", 2020, 1, pos="QB", rush_td=1.0, pass_td=3.0)) == 1.0


def test_carrera_y_recepcion_suman():
    assert tds_de(fila("r", 2020, 1, rush_td=1.0, rec_td=1.0)) == 2.0


def test_toques_son_acarreos_mas_targets():
    assert toques_de(fila("r", 2020, 1, carries=12.0, targets=4.0)) == 16.0


# --- modelo ---------------------------------------------------------------

def test_no_predice_sin_historial():
    m = modelo_calentado()
    assert m.predecir([0.0] * 3, [18.0] * 3, "RB") is None


def test_no_predice_a_jugadores_sin_uso():
    m = modelo_calentado()
    assert m.predecir([0.0] * 10, [0.5] * 10, "RB") is None


def test_no_predice_posiciones_fuera_del_mercado():
    m = modelo_calentado(pos="K")
    assert m.predecir([0.0] * 10, [18.0] * 10, "K") is None


def test_mas_uso_mas_probabilidad():
    m = modelo_calentado()
    poco = m.predecir([0.0] * 10, [5.0] * 10, "RB")
    mucho = m.predecir([0.0] * 10, [25.0] * 10, "RB")
    assert poco is not None and mucho is not None
    assert mucho > poco


def test_mas_tds_recientes_mas_probabilidad():
    m = modelo_calentado()
    seco = m.predecir([0.0] * 10, [18.0] * 10, "RB")
    goleador = m.predecir([1.0] * 10, [18.0] * 10, "RB")
    assert seco is not None and goleador is not None
    assert goleador > seco


def test_la_probabilidad_es_poisson_de_al_menos_uno():
    """Con w=0 la probabilidad solo depende del uso: 1 - exp(-toques * tasa)."""
    m = AnytimeTDModel(peso_historial=0.0)
    for _ in range(100):
        m.observar("RB", 1.0, 20.0)   # 1 TD cada 20 toques
    p = m.predecir([0.0] * 10, [20.0] * 10, "RB")
    assert p == pytest.approx(1.0 - math.exp(-1.0))


def test_racha_corta_no_dispara_la_probabilidad():
    """3 TDs en 5 partidos es suerte en una moneda cargada al ~25%, no un 60%."""
    m = modelo_calentado()
    p = m.predecir([0.0, 0.0, 1.0, 1.0, 1.0], [18.0] * 5, "RB")
    assert p is not None and p < 0.55


# --- backtest -------------------------------------------------------------

def test_siempre_predice_antes_de_aprender():
    orden: list[str] = []

    class Espia(AnytimeTDModel):
        def predecir(self, *a, **k):
            orden.append("predice")
            return super().predecir(*a, **k)

        def observar(self, *a, **k):
            orden.append("aprende")
            return super().observar(*a, **k)

    filas = [fila("r", 2020, w) for w in range(1, 10)]
    walk_forward_anytime_td(filas, Espia())
    assert orden == ["predice", "aprende"] * (len(orden) // 2)


def test_la_base_es_la_del_universo_elegible():
    """Los suplentes que nunca anotan no deben rebajar la base del titular."""
    filas = []
    for w in range(1, 18):
        for t in range(2018, 2022):
            # Titulares con uso: anotan la mitad de las veces.
            for j in range(6):
                filas.append(fila(f"tit{j}", t, w, carries=18.0,
                                  rush_td=1.0 if (w + j) % 2 else 0.0))
            # Suplentes sin uso: nunca anotan y el modelo no los predice.
            for j in range(20):
                filas.append(fila(f"sup{j}", t, w, carries=0.0, targets=0.0))
    filas.sort(key=lambda f: (f["season"], f["week"]))
    preds = walk_forward_anytime_td(filas)
    assert preds
    # Si la base contara a los suplentes, rondaria 6/26 ~ 11%. Debe rondar 50%.
    assert all(0.35 < x.p_base < 0.65 for x in preds[-50:])


def test_skill_cero_si_el_modelo_iguala_a_la_base():
    preds = [PrediccionTD(2020, "RB", 0.3, 0.3, i % 3 == 0) for i in range(300)]
    assert skill(preds) == pytest.approx(0.0)


def test_skill_positivo_si_el_modelo_separa_mejor():
    preds = [PrediccionTD(2020, "RB", 0.9 if i % 2 else 0.1, 0.5, bool(i % 2))
             for i in range(300)]
    assert skill(preds) > 0.5


def test_calibracion_agrupa():
    preds = [PrediccionTD(2020, "RB", 0.05, 0.3, False)] * 100 + \
            [PrediccionTD(2020, "RB", 0.95, 0.3, True)] * 100
    cal = calibracion(preds)
    assert [round(o, 2) for _, o, _ in cal] == [0.0, 1.0]
    assert brier(preds) < brier(preds, base=True)


def test_peso_por_defecto_es_el_validado():
    assert AnytimeTDModel().peso_historial == 0.70
