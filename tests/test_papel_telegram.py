"""Mensajes de Telegram de las apuestas en papel."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from betbot.alerts.telegram import TelegramAlerter
from betbot.papel import InformeGeneracion, LineaProp
from betbot.papel_mensajes import AVISO, mensaje_calificacion, mensaje_generacion

KO = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)   # 11:00 en Ciudad de Mexico
RESUMEN = {"apuntadas": 3, "calificadas": 0, "ganadas": 0, "pendientes": 3, "roi": None,
           "n_clv": 0, "clv_medio": None, "clv_positivo": None}


def linea(jugador="Travis Kelce", market="player_receptions", lado="Over", point=4.5):
    return LineaProp("e", KO, "Kansas City Chiefs", "Denver Broncos", market, jugador,
                     lado, point, 1.91, 1.95, 4)


# --- envio sin formato ------------------------------------------------------

class Capturador(TelegramAlerter):
    def __init__(self):
        super().__init__("token", "chat")
        self.enviados: list[dict] = []

    def _post(self, text, parse_mode="Markdown"):
        self.enviados.append({"text": text, "parse_mode": parse_mode})
        return True


def test_papel_se_manda_sin_markdown():
    """`player_pass_yds` con Markdown haria que Telegram rechazara el mensaje."""
    t = Capturador()
    t.send_plain("Josh_Allen player_pass_yds *raro*")
    assert t.enviados == [{"text": "Josh_Allen player_pass_yds *raro*", "parse_mode": None}]


def test_mensajes_largos_se_parten_por_lineas():
    t = Capturador()
    texto = "\n".join(f"apuesta numero {i:04d} " + "x" * 60 for i in range(200))
    assert t.send_plain(texto)
    assert len(t.enviados) > 1
    assert all(len(e["text"]) <= t.LIMITE for e in t.enviados)
    assert "\n".join(e["text"] for e in t.enviados) == texto   # nada se pierde


def test_el_payload_sin_formato_no_lleva_parse_mode(monkeypatch):
    enviado = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None, context=None):
        enviado.update(json.loads(req.data))
        return Resp()

    monkeypatch.setattr("betbot.alerts.telegram.urllib.request.urlopen", urlopen)
    TelegramAlerter("t", "c").send_plain("hola")
    assert "parse_mode" not in enviado


# --- contenido ----------------------------------------------------------------

def test_mensaje_de_apuestas_lleva_el_aviso_y_habla_claro():
    inf = InformeGeneracion(lineas=120, apuntadas=1, sin_valor=100)
    inf.nuevas = [(linea(), 0.60, 0.146)]
    texto = mensaje_generacion(inf, "nfl", RESUMEN)
    assert AVISO in texto
    assert "Travis Kelce recepciones mas de 4.5 @ 1.91" in texto
    assert "11:00" in texto                    # hora de Mexico, no UTC
    assert "player_" not in texto              # nada de nombres internos
    assert "ruido" in texto                    # con pocas apuestas, se dice


def test_anytime_td_sin_linea():
    inf = InformeGeneracion(lineas=5, apuntadas=1)
    inf.nuevas = [(linea("Travis Kelce", "player_anytime_td", "Yes", None), 0.35, 0.05)]
    assert "Travis Kelce anota TD: si" in mensaje_generacion(inf, "nfl", RESUMEN)


def test_sin_lineas_el_mensaje_dice_donde_mirar():
    texto = mensaje_generacion(InformeGeneracion(lineas=0), "nfl", RESUMEN)
    assert "No encontre lineas" in texto and "collect.log" in texto


def test_sin_apuestas_tambien_se_informa():
    texto = mensaje_generacion(InformeGeneracion(lineas=80, sin_valor=80), "nfl", RESUMEN)
    assert "Ninguna tenia valor" in texto


def test_resultados():
    fila = {"jugador": "Travis Kelce", "market": "player_receptions", "lado": "Over",
            "point": 4.5}
    detalle = [(fila, "ganada", 6.0, 0.03), (fila, "perdida", 3.0, -0.01),
               (fila, "nula", None, None)]
    r = dict(RESUMEN, calificadas=2, ganadas=1, roi=-0.045, n_clv=2, clv_medio=0.01,
             clv_positivo=0.5)
    texto = mensaje_calificacion(detalle, "nfl", r)
    assert "✅ Travis Kelce recepciones mas de 4.5 -> 6 | CLV +3.0%" in texto
    assert "❌" in texto
    assert "no jugo" in texto
    assert "ROI -4.5%" in texto and "CLV medio +1.0%" in texto


# --- comando ---------------------------------------------------------------------

@pytest.fixture
def entorno(tmp_path, monkeypatch):
    from betbot.collect import OddsArchive
    from betbot.ingest.player_store import PlayerStore

    PlayerStore(tmp_path / "players.db")          # base vacia pero valida
    OddsArchive(tmp_path / "a.db")                # sin lineas
    enviados: list[str] = []

    class Falso:
        def send_plain(self, texto):
            enviados.append(texto)
            return True

    monkeypatch.setattr("betbot.cli._telegram_para_papel", lambda: Falso())
    base = ["papel", "--sport", "nfl", "--db", str(tmp_path / "players.db"),
            "--archivo", str(tmp_path / "a.db"), "--registro", str(tmp_path / "p.db"),
            "--telegram"]
    return base, enviados


def test_sin_lineas_no_manda_nada_salvo_que_se_pida(entorno):
    from betbot.cli import main

    base, enviados = entorno
    assert main(base) == 0
    assert enviados == []
    assert main(base + ["--avisar-vacio"]) == 0
    assert len(enviados) == 1 and "No encontre lineas" in enviados[0]


def test_sin_telegram_configurado_sigue_funcionando(tmp_path, monkeypatch):
    from betbot.cli import _telegram_para_papel

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.chdir(tmp_path)                    # sin .env
    assert _telegram_para_papel() is None
