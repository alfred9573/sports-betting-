"""Caducidad de la cache HTTP.

El fallo que esto evita existio de verdad: la cache era eterna, y la reingesta
semanal del cron devolvia siempre la primera copia descargada. Ni `--force` lo
arreglaba. Los ratings dejaron de recibir partidos nuevos sin ningun aviso.
"""

from __future__ import annotations

import os
import time
import urllib.error
from datetime import date

import pytest

from betbot.ingest import http
from betbot.ingest.http import VIVO, CachedFetcher, FetchError, caducidad


class Respuesta:
    def __init__(self, cuerpo: bytes):
        self.cuerpo = cuerpo

    def read(self):
        return self.cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def red(monkeypatch):
    """Red falsa: devuelve lo que diga `estado['cuerpo']` y cuenta llamadas."""
    estado = {"cuerpo": b"v1", "llamadas": 0, "falla": False}

    def urlopen(req, timeout=None, context=None):
        estado["llamadas"] += 1
        if estado["falla"]:
            raise urllib.error.URLError("red caida")
        return Respuesta(estado["cuerpo"])

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    return estado


def fetcher(tmp_path):
    return CachedFetcher(cache_dir=tmp_path, min_interval=0.0, max_retries=2)


def envejecer(tmp_path, segundos):
    for f in tmp_path.iterdir():
        t = time.time() - segundos
        os.utime(f, (t, t))


def test_sin_max_age_la_copia_no_caduca(tmp_path, red):
    f = fetcher(tmp_path)
    assert f.get_bytes("https://x/a") == b"v1"
    red["cuerpo"] = b"v2"
    envejecer(tmp_path, 10 * 365 * 86400)
    assert f.get_bytes("https://x/a") == b"v1"      # temporada cerrada: para siempre
    assert red["llamadas"] == 1


def test_copia_reciente_se_sirve_de_cache(tmp_path, red):
    f = fetcher(tmp_path)
    f.get_bytes("https://x/a", max_age=VIVO)
    red["cuerpo"] = b"v2"
    assert f.get_bytes("https://x/a", max_age=VIVO) == b"v1"
    assert red["llamadas"] == 1


def test_copia_caducada_se_vuelve_a_bajar(tmp_path, red):
    """El caso del bug: la temporada en curso tiene que refrescarse."""
    f = fetcher(tmp_path)
    f.get_bytes("https://x/a", max_age=VIVO)
    red["cuerpo"] = b"v2"
    envejecer(tmp_path, VIVO + 60)
    assert f.get_bytes("https://x/a", max_age=VIVO) == b"v2"
    assert red["llamadas"] == 2
    # Y la copia nueva queda guardada.
    assert f.get_bytes("https://x/a", max_age=VIVO) == b"v2"
    assert red["llamadas"] == 2


def test_si_no_se_puede_refrescar_usa_la_vieja(tmp_path, red):
    f = fetcher(tmp_path)
    f.get_bytes("https://x/a", max_age=VIVO)
    envejecer(tmp_path, VIVO + 60)
    red["falla"] = True
    assert f.get_bytes("https://x/a", max_age=VIVO) == b"v1"
    assert f.stats["stale"] == 1


def test_sin_copia_y_sin_red_falla(tmp_path, red):
    red["falla"] = True
    with pytest.raises(FetchError):
        fetcher(tmp_path).get_bytes("https://x/a", max_age=VIVO)


def test_get_text_y_get_json_pasan_la_caducidad(tmp_path, red):
    f = fetcher(tmp_path)
    red["cuerpo"] = b'{"n": 1}'
    assert f.get_json("https://x/j", max_age=VIVO) == {"n": 1}
    red["cuerpo"] = b'{"n": 2}'
    envejecer(tmp_path, VIVO + 60)
    assert f.get_json("https://x/j", max_age=VIVO) == {"n": 2}


# --- politica por temporada ----------------------------------------------

def test_temporada_en_curso_esta_viva():
    assert caducidad(date(2027, 3, 1), hoy=date(2026, 9, 25)) == VIVO


def test_temporada_cerrada_no_caduca():
    assert caducidad(date(2025, 3, 1), hoy=date(2026, 9, 25)) is None


def test_margen_de_un_mes_tras_el_cierre():
    """Las fuentes corrigen marcadores dias despues de terminar la temporada."""
    fin = date(2026, 3, 1)
    assert caducidad(fin, hoy=date(2026, 3, 20)) == VIVO
    assert caducidad(fin, hoy=date(2026, 4, 15)) is None


def test_la_temporada_nfl_en_curso_se_refresca(tmp_path, red, monkeypatch):
    """De punta a punta: el archivo de la temporada NFL actual caduca."""
    from betbot.ingest.sources import nfl_player_stats as mod

    monkeypatch.setattr(mod, "caducidad", lambda hasta: VIVO)
    red["cuerpo"] = b"player_id,season,week\n"
    fuente = mod.NFLPlayerStats(fetcher=fetcher(tmp_path))
    fuente.fetch_season(2026)
    envejecer(tmp_path, VIVO + 60)
    fuente.fetch_season(2026)
    assert red["llamadas"] == 2
