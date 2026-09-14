"""Pruebas del archivador de lineas.

Lo que de verdad hay que proteger aqui son dos cosas: que la deduplicacion no
pierda un cambio de precio real (perderlo arruina el historico en silencio, y
no hay forma de recuperarlo despues), y que "Over 45.5" y "Over 46.5" nunca se
confundan (ese fue un bug real en `lineshop`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from betbot.collect import PROPS_POR_DEPORTE, OddsArchive, PriceRow, parse_evento


def evento(precio_home=1.90, precio_away=2.00, punto=None, market="h2h"):
    salida = {
        "id": "ev1",
        "commence_time": "2026-09-20T17:00:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Denver Broncos",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": market,
                        "last_update": "2026-09-20T15:00:00Z",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": precio_home},
                            {"name": "Denver Broncos", "price": precio_away},
                        ],
                    }
                ],
            }
        ],
    }
    if punto is not None:
        for o in salida["bookmakers"][0]["markets"][0]["outcomes"]:
            o["point"] = punto
    return salida


def test_parse_aplana_precios():
    filas = parse_evento(evento(), "americanfootball_nfl")
    assert len(filas) == 2
    assert {f.selection for f in filas} == {"Kansas City Chiefs", "Denver Broncos"}
    assert all(f.bookmaker == "pinnacle" for f in filas)
    assert all(f.sport == "americanfootball_nfl" for f in filas)


def test_parse_descarta_precios_imposibles():
    item = evento()
    item["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 0.5
    filas = parse_evento(item, "americanfootball_nfl")
    assert len(filas) == 1


def test_parse_props_une_jugador_y_lado():
    """Sin el `description`, todas las props del partido serian "Over"/"Under"."""
    item = {
        "id": "ev1",
        "commence_time": "2026-09-20T17:00:00Z",
        "home_team": "A", "away_team": "B",
        "bookmakers": [{
            "key": "draftkings",
            "markets": [{
                "key": "player_pass_yds",
                "outcomes": [
                    {"name": "Over", "description": "Patrick Mahomes",
                     "price": 1.90, "point": 275.5},
                    {"name": "Under", "description": "Patrick Mahomes",
                     "price": 1.90, "point": 275.5},
                    {"name": "Over", "description": "Bo Nix",
                     "price": 1.87, "point": 210.5},
                ],
            }],
        }],
    }
    filas = parse_evento(item, "americanfootball_nfl")
    assert len(filas) == 3
    assert {f.selection for f in filas} == {
        "Patrick Mahomes Over", "Patrick Mahomes Under", "Bo Nix Over",
    }
    # Y las claves de linea son todas distintas, que es el punto.
    assert len({f.clave for f in filas}) == 3


def test_archivar_deduplica_precio_repetido(tmp_path):
    arch = OddsArchive(tmp_path / "a.db")
    filas = parse_evento(evento(), "americanfootball_nfl")
    primero = arch.archivar(filas)
    assert primero.nuevas == 2
    segundo = arch.archivar(filas)
    assert segundo.vistas == 2
    assert segundo.nuevas == 0  # nada cambio: no se escribe nada


def test_archivar_captura_el_movimiento(tmp_path):
    """El caso que no se puede fallar: un precio que se mueve DEBE guardarse."""
    arch = OddsArchive(tmp_path / "a.db")
    arch.archivar(parse_evento(evento(precio_home=1.90), "americanfootball_nfl"))
    res = arch.archivar(parse_evento(evento(precio_home=1.95), "americanfootball_nfl"))
    assert res.nuevas == 1
    r = arch.resumen()
    assert r["filas"] == 3  # dos iniciales + el movimiento


def test_vuelta_al_precio_anterior_tambien_se_guarda(tmp_path):
    """1.90 -> 1.95 -> 1.90 son tres observaciones, no dos.

    La deduplicacion compara con el ULTIMO precio, no con todos los vistos: si
    comparara con el historico entero, un vaiven se registraria como un solo
    movimiento y la serie temporal quedaria mal.
    """
    arch = OddsArchive(tmp_path / "a.db")
    for p in (1.90, 1.95, 1.90):
        arch.archivar(parse_evento(evento(precio_home=p), "americanfootball_nfl"))
    assert arch.resumen()["filas"] == 4  # 2 del primer barrido + 2 movimientos


def test_la_linea_forma_parte_de_la_clave(tmp_path):
    """Over 45.5 y Over 46.5 son mercados distintos, no un cambio de precio."""
    arch = OddsArchive(tmp_path / "a.db")
    arch.archivar(parse_evento(
        evento(precio_home=1.90, punto=45.5, market="totals"), "americanfootball_nfl"))
    res = arch.archivar(parse_evento(
        evento(precio_home=1.90, punto=46.5, market="totals"), "americanfootball_nfl"))
    assert res.nuevas == 2  # linea nueva entera, aunque el precio sea el mismo


def test_duplicado_dentro_de_la_misma_tanda(tmp_path):
    arch = OddsArchive(tmp_path / "a.db")
    filas = parse_evento(evento(), "americanfootball_nfl")
    res = arch.archivar(filas + filas)
    assert res.vistas == 4
    assert res.nuevas == 2


def test_resumen_de_archivo_vacio(tmp_path):
    assert OddsArchive(tmp_path / "a.db").resumen()["filas"] == 0


def test_archivar_sin_filas_no_falla(tmp_path):
    arch = OddsArchive(tmp_path / "a.db")
    res = arch.archivar([])
    assert (res.vistas, res.nuevas) == (0, 0)


def test_lote_grande_supera_el_limite_de_parametros(tmp_path):
    """El troceado de la consulta de ultimos precios tiene que funcionar de verdad."""
    arch = OddsArchive(tmp_path / "a.db")
    filas = [
        PriceRow(
            event_id=f"ev{i}", sport="americanfootball_nfl",
            commence_time="2026-09-20T17:00:00Z", home_team="A", away_team="B",
            bookmaker="pinnacle", market="h2h", selection="A",
            point=None, decimal_odds=1.90,
        )
        for i in range(1200)
    ]
    assert arch.archivar(filas).nuevas == 1200
    assert arch.archivar(filas).nuevas == 0
    movidas = [
        PriceRow(**{**f.__dict__, "decimal_odds": 1.95}) for f in filas[:5]
    ]
    assert arch.archivar(movidas).nuevas == 5


@pytest.mark.parametrize("deporte", [
    "americanfootball_nfl", "basketball_nba", "baseball_mlb", "soccer_epl",
])
def test_catalogo_de_props_cubre_los_deportes_del_bot(deporte):
    assert PROPS_POR_DEPORTE[deporte]


def test_resumen_agrupa(tmp_path):
    arch = OddsArchive(tmp_path / "a.db")
    arch.archivar(parse_evento(evento(), "americanfootball_nfl"))
    arch.archivar(parse_evento(evento(market="totals", punto=45.5), "americanfootball_nfl"))
    r = arch.resumen()
    assert {m["market"] for m in r["por_mercado"]} == {"h2h", "totals"}
    assert r["por_deporte"][0]["sport"] == "americanfootball_nfl"
    assert r["casas"] == 1


def test_captured_at_explicito_permite_series_reproducibles(tmp_path):
    arch = OddsArchive(tmp_path / "a.db")
    t0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    arch.archivar(parse_evento(evento(precio_home=1.90), "americanfootball_nfl"), captured_at=t0)
    arch.archivar(
        parse_evento(evento(precio_home=1.95), "americanfootball_nfl"),
        captured_at=t0 + timedelta(hours=1),
    )
    r = arch.resumen()
    assert r["desde"] == t0.isoformat()
    assert r["hasta"] == (t0 + timedelta(hours=1)).isoformat()


# --- CLI -------------------------------------------------------------------

def test_cli_stats_en_archivo_vacio(tmp_path, capsys):
    from betbot.cli import main
    assert main(["collect", "--stats", "--db", str(tmp_path / "a.db")]) == 0
    assert "Archivo vacio" in capsys.readouterr().out


def test_cli_stats_muestra_lo_acumulado(tmp_path, capsys):
    from betbot.cli import main
    db = tmp_path / "a.db"
    OddsArchive(db).archivar(parse_evento(evento(), "americanfootball_nfl"))
    assert main(["collect", "--stats", "--db", str(db)]) == 0
    salida = capsys.readouterr().out
    assert "pinnacle" not in salida  # el resumen agrupa, no vuelca precios
    assert "americanfootball_nfl" in salida
    assert "h2h" in salida


def test_cli_deporte_desconocido(tmp_path, monkeypatch, capsys):
    from betbot.cli import main
    monkeypatch.setenv("ODDS_API_KEY", "x")
    assert main(["collect", "--sport", "cricket", "--db", str(tmp_path / "a.db")]) == 2
