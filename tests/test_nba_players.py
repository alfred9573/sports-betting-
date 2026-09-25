"""Pruebas de la ingesta de jugadores de NBA y de su uso en el modelo de props.

Lo critico: que "jugo" signifique minutos > 0 (el criterio con el que las casas
anulan una prop), que las posiciones de distintas epocas acaben en el mismo
grupo, y que la temporada se nombre por el ano en que TERMINA.
"""

from __future__ import annotations

from datetime import date

import pytest

from betbot.cli import temporada_en_curso
from betbot.ingest.nba_player_store import NBAPlayerStore
from betbot.ingest.sources.nba_player_box import NBAPlayerBox, normalizar_posicion
from betbot.models.props import MERCADOS_NBA, PROYECCION_MINIMA


def cruda(aid=1, gid=100, fecha="2025-01-10", minutos=30.0, dnp=False,
          pos="G", season=2025, stype=2, pts=20):
    return {
        "athlete_id": aid, "game_id": gid, "season": season, "season_type": stype,
        "game_date": fecha, "athlete_display_name": f"Jugador {aid}",
        "athlete_position_abbreviation": pos, "team_abbreviation": "BOS",
        "opponent_team_abbreviation": "NYK", "minutes": minutos,
        "did_not_play": dnp, "starter": True, "points": pts, "rebounds": 5,
        "assists": 4, "three_point_field_goals_made": 2,
    }


# --- posiciones -------------------------------------------------------------

@pytest.mark.parametrize("cruda_pos,esperada", [
    ("PG", "G"), ("SG", "G"), ("G", "G"),
    ("SF", "F"), ("PF", "F"), ("F", "F"),
    ("C", "C"),
    ("G-F", "G"), ("F-C", "F"),
    ("NA", "?"), (None, "?"), ("", "?"),
])
def test_posiciones_de_todas_las_epocas_se_unifican(cruda_pos, esperada):
    """Un 'PG' de 2005 y un 'G' de 2025 tienen que caer en el mismo grupo."""
    assert normalizar_posicion(cruda_pos) == esperada


# --- parseo -----------------------------------------------------------------

def test_jugo_es_minutos_positivos():
    fuente = NBAPlayerBox()
    jugo, dnp, sin_min = fuente.parse([
        cruda(aid=1, minutos=30.0),
        cruda(aid=2, minutos=None, dnp=True),
        # El caso raro del dato: no marcado como DNP pero sin minutos.
        cruda(aid=3, minutos=0.0, dnp=False),
    ])
    assert jugo.jugo
    assert not dnp.jugo
    assert not sin_min.jugo   # la casa anularia esta apuesta: fuera del modelo


def test_triples_salen_de_su_columna():
    (f,) = NBAPlayerBox().parse([cruda()])
    assert f.stats == {"points": 20.0, "rebounds": 5.0, "assists": 4.0, "threes": 2.0}


def test_filas_incompletas_se_descartan_con_registro():
    fuente = NBAPlayerBox()
    malas = cruda()
    malas["athlete_id"] = None
    assert fuente.parse([malas]) == []
    assert fuente.descartados


def test_sin_pyarrow_el_error_explica_que_instalar(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def sin_pyarrow(name, *a, **k):
        if name.startswith("pyarrow"):
            raise ImportError("no pyarrow")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", sin_pyarrow)
    with pytest.raises(RuntimeError, match="pip install pyarrow"):
        NBAPlayerBox().fetch_season(2025)


# --- almacen ----------------------------------------------------------------

def test_el_modelo_solo_ve_partidos_jugados_y_en_orden(tmp_path):
    store = NBAPlayerStore(tmp_path / "n.db")
    fuente = NBAPlayerBox()
    store.upsert_many(fuente.parse([
        cruda(aid=1, gid=2, fecha="2025-01-12"),
        cruda(aid=1, gid=1, fecha="2025-01-10"),
        cruda(aid=2, gid=1, fecha="2025-01-10", minutos=None, dnp=True),
        cruda(aid=1, gid=9, fecha="2025-04-30", stype=3),   # playoffs
    ]))
    filas = store.filas_para_modelo()
    assert [f["game_date"] for f in filas] == ["2025-01-10", "2025-01-12"]
    assert all(f["minutes"] > 0 for f in filas)
    assert set(MERCADOS_NBA.values()) <= set(filas[0])


def test_los_dnp_se_guardan_aunque_el_modelo_no_los_use(tmp_path):
    """Son el unico dato para estimar algun dia la probabilidad de jugar."""
    store = NBAPlayerStore(tmp_path / "n.db")
    store.upsert_many(NBAPlayerBox().parse([cruda(aid=2, minutos=None, dnp=True)]))
    r = store.resumen()
    assert r["filas"] == 1 and r["jugados"] == 0


def test_reingesta_idempotente(tmp_path):
    store = NBAPlayerStore(tmp_path / "n.db")
    filas = NBAPlayerBox().parse([cruda()])
    store.upsert_many(filas)
    store.upsert_many(filas)
    assert store.resumen()["filas"] == 1


# --- temporadas -------------------------------------------------------------

def test_nba_nombra_la_temporada_por_su_ano_de_fin():
    assert temporada_en_curso("nba", date(2026, 10, 25)) == 2027
    assert temporada_en_curso("nba", date(2027, 3, 1)) == 2027
    assert temporada_en_curso("nba", date(2026, 9, 25)) == 2026


def test_nfl_nombra_la_temporada_por_su_ano_de_inicio():
    assert temporada_en_curso("nfl", date(2026, 9, 25)) == 2026
    assert temporada_en_curso("nfl", date(2027, 1, 20)) == 2026


# --- mercados ---------------------------------------------------------------

def test_cada_mercado_nba_tiene_uso_minimo():
    assert all(m in PROYECCION_MINIMA for m in MERCADOS_NBA)
