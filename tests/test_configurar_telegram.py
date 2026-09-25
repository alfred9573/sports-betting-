"""Configuracion guiada de Telegram."""

from __future__ import annotations

import stat

from betbot.alerts.configurar import (
    ErrorTelegram,
    chats_privados,
    configurar,
    escribir_variable,
    leer_variable,
    limpiar_token,
)

TOKEN = "123456789:AAFabcdefghijklmnopqrstuvwxyz012345"


def test_escribir_reemplaza_y_quita_repetidas(tmp_path):
    """El cargador se queda con la PRIMERA aparicion: una vieja arriba ganaria."""
    env = tmp_path / ".env"
    env.write_text("ODDS_API_KEY=abc\nTELEGRAM_BOT_TOKEN=\nX=1\nTELEGRAM_BOT_TOKEN=viejo\n")
    escribir_variable(env, "TELEGRAM_BOT_TOKEN", TOKEN)
    assert env.read_text() == f"ODDS_API_KEY=abc\nTELEGRAM_BOT_TOKEN={TOKEN}\nX=1\n"


def test_escribir_anade_si_no_existe_y_protege_el_archivo(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ODDS_API_KEY=abc\n")
    escribir_variable(env, "TELEGRAM_CHAT_ID", "42")
    assert leer_variable(env, "TELEGRAM_CHAT_ID") == "42"
    assert leer_variable(env, "ODDS_API_KEY") == "abc"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600   # el token es un secreto


def test_limpiar_token_quita_lo_que_se_cuela_al_copiar():
    assert limpiar_token(f"  '{TOKEN}'  ") == TOKEN
    assert limpiar_token(f"TELEGRAM_BOT_TOKEN={TOKEN}") == TOKEN


def test_chats_privados_el_mas_reciente_primero_y_sin_grupos():
    updates = {"result": [
        {"message": {"chat": {"id": 1, "type": "private", "first_name": "Viejo"}}},
        {"message": {"chat": {"id": -5, "type": "group", "title": "Grupo"}}},
        {"message": {"chat": {"id": 2, "type": "private", "first_name": "Alfred"}}},
        {"message": {"chat": {"id": 1, "type": "private", "first_name": "Viejo"}}},
    ]}
    assert chats_privados(updates) == [(1, "Viejo"), (2, "Alfred")]


class Telegram:
    """API falsa: sin mensajes hasta que el usuario 'manda hola'."""

    def __init__(self, token_valido=True):
        self.token_valido = token_valido
        self.hola_enviado = False
        self.enviados = []

    def __call__(self, token, metodo, params=None):
        if not self.token_valido:
            raise ErrorTelegram("Telegram dice que el token no es valido.")
        if metodo == "getMe":
            return {"ok": True, "result": {"username": "alfy_betbot"}}
        if metodo == "getUpdates":
            if not self.hola_enviado:
                return {"ok": True, "result": []}
            return {"ok": True, "result": [
                {"message": {"chat": {"id": 987, "type": "private", "first_name": "Alfred"}}}]}
        if metodo == "sendMessage":
            self.enviados.append(params)
            return {"ok": True, "result": {}}
        raise AssertionError(metodo)


def test_flujo_completo(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("ODDS_API_KEY=abc\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\n")
    tg = Telegram()

    def entrada(_prompt):
        tg.hola_enviado = True   # el usuario va a Telegram, manda hola y presiona Enter
        return ""

    assert configurar(env=env, entrada=entrada, secreto=lambda _: f" {TOKEN} ",
                      llamar=tg) == 0
    assert leer_variable(env, "TELEGRAM_BOT_TOKEN") == TOKEN
    assert leer_variable(env, "TELEGRAM_CHAT_ID") == "987"
    assert leer_variable(env, "ODDS_API_KEY") == "abc"   # lo demas intacto
    assert tg.enviados and tg.enviados[0]["chat_id"] == 987
    salida = capsys.readouterr().out
    assert TOKEN not in salida                          # el token nunca se imprime
    assert "@alfy_betbot" in salida


def test_token_con_forma_incorrecta_no_toca_nada(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ODDS_API_KEY=abc\n")
    assert configurar(env=env, entrada=lambda _: "", secreto=lambda _: "hola",
                      llamar=Telegram()) == 2
    assert leer_variable(env, "TELEGRAM_BOT_TOKEN") == ""


def test_token_rechazado_por_telegram_no_se_guarda(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    assert configurar(env=env, entrada=lambda _: "", secreto=lambda _: TOKEN,
                      llamar=Telegram(token_valido=False)) == 2
    assert leer_variable(env, "TELEGRAM_BOT_TOKEN") == ""


def test_sin_hola_tras_los_intentos_lo_explica(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("")
    assert configurar(env=env, entrada=lambda _: "", secreto=lambda _: TOKEN,
                      llamar=Telegram(), intentos=2) == 2
    assert "hola" in capsys.readouterr().out
    assert leer_variable(env, "TELEGRAM_BOT_TOKEN") == TOKEN   # el token si queda


def test_reusa_el_token_guardado(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\n")
    tg = Telegram()
    tg.hola_enviado = True

    def secreto(_):
        raise AssertionError("no deberia volver a pedir el token")

    assert configurar(env=env, entrada=lambda _: "", secreto=secreto, llamar=tg) == 0
