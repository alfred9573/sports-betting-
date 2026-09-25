"""Configuracion guiada de Telegram: `betbot configurar-telegram`.

POR QUE EXISTE. La receta manual (crear el bot, editar el .env, sacar el chat
id de un JSON con curl y grep) fallo en la practica por lo de siempre en una
terminal remota: un caracter de mas al copiar, estar en la carpeta equivocada,
comillas que se comen. Cada fallo cuesta un ida y vuelta. Este comando pide el
token, lo comprueba contra Telegram, encuentra solo el chat id y manda la
prueba. Lo unico que queda a mano es hablar con @BotFather.

El token se pide sin eco (no se ve al pegarlo), se guarda en el .env con
permisos 600 y nunca se imprime: con el, cualquiera puede escribir como el bot.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from betbot.net import ssl_context

TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")


class ErrorTelegram(RuntimeError):
    pass


def escribir_variable(path: str | Path, clave: str, valor: str) -> None:
    """Deja UNA sola linea `clave=valor` en el .env, sin tocar las demas.

    El cargador de .env se queda con la PRIMERA aparicion de cada clave, asi
    que anadir una linea nueva al final no serviria si ya habia otra (vacia o
    vieja) mas arriba. Se reemplaza la primera y se borran las repetidas.
    """
    p = Path(path)
    lineas = p.read_text().splitlines() if p.exists() else []
    nuevas, puesta = [], False
    for linea in lineas:
        if linea.strip().startswith(f"{clave}="):
            if not puesta:
                nuevas.append(f"{clave}={valor}")
                puesta = True
            continue
        nuevas.append(linea)
    if not puesta:
        nuevas.append(f"{clave}={valor}")
    p.write_text("\n".join(nuevas) + "\n")
    os.chmod(p, 0o600)


def leer_variable(path: str | Path, clave: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    for linea in p.read_text().splitlines():
        if linea.strip().startswith(f"{clave}="):
            return linea.split("=", 1)[1].strip().strip("\"'")
    return ""


def limpiar_token(texto: str) -> str:
    """Quita lo que suele colarse al copiar: espacios, comillas, el prefijo."""
    t = texto.strip().strip("\"'").strip()
    if t.startswith("TELEGRAM_BOT_TOKEN="):
        t = t.split("=", 1)[1].strip().strip("\"'")
    return t


def api(token: str, metodo: str, params: dict | None = None, timeout: int = 20) -> dict:
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    datos = urllib.parse.urlencode(params).encode() if params else None
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=datos), timeout=timeout, context=ssl_context()
        ) as resp:
            cuerpo = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise ErrorTelegram("Telegram dice que el token no es valido.") from e
        raise ErrorTelegram(f"Telegram respondio HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        raise ErrorTelegram(f"No pude conectar con Telegram: {e.reason}") from e
    if not cuerpo.get("ok"):
        raise ErrorTelegram(cuerpo.get("description", "respuesta no valida"))
    return cuerpo


def chats_privados(updates: dict) -> list[tuple[int, str]]:
    """Chats privados que le escribieron al bot, del mas reciente al mas viejo."""
    vistos: dict[int, str] = {}
    for u in reversed(updates.get("result", [])):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id") not in vistos:
            nombre = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            vistos[chat["id"]] = nombre or chat.get("username") or "sin nombre"
    return list(vistos.items())


def configurar(
    env: str | Path = ".env",
    entrada: Callable[[str], str] = input,
    secreto: Callable[[str], str] = getpass.getpass,
    llamar: Callable[..., dict] = api,
    intentos: int = 3,
) -> int:
    print("Configuracion de Telegram para betbot.\n")

    token = leer_variable(env, "TELEGRAM_BOT_TOKEN")
    if token:
        r = entrada("Ya hay un token guardado. ¿Lo uso? [S/n] ").strip().lower()
        if r in ("n", "no"):
            token = ""
    if not token:
        print("Pega el token que te dio @BotFather y presiona Enter.")
        print("(Por seguridad NO se ve nada mientras pegas. Es normal.)")
        token = limpiar_token(secreto("Token: "))
    if not TOKEN_RE.match(token):
        print("\nEso no parece un token de BotFather. Tiene esta forma:")
        print("  123456789:AAF-letras_y_numeros...   (numeros, dos puntos, ~35 caracteres)")
        print("Copialo otra vez del mensaje de @BotFather y vuelve a correr este comando.")
        return 2

    try:
        bot = llamar(token, "getMe")["result"]
    except ErrorTelegram as e:
        print(f"\n{e}")
        print("Copialo otra vez de @BotFather (sin espacios) y vuelve a intentarlo.")
        return 2
    usuario = bot.get("username", "tu bot")
    escribir_variable(env, "TELEGRAM_BOT_TOKEN", token)
    print(f"\nToken correcto: es el bot @{usuario}. Guardado en {env}.")

    chats: list[tuple[int, str]] = []
    for intento in range(intentos):
        try:
            chats = chats_privados(llamar(token, "getUpdates"))
        except ErrorTelegram as e:
            print(f"\n{e}")
            return 1
        if chats:
            break
        if intento < intentos - 1:
            entrada(f"\nAbre Telegram, busca @{usuario}, mandale 'hola', "
                    f"y luego presiona Enter aqui... ")
    if not chats:
        print(f"\nTelegram no tiene ningun mensaje para @{usuario}.")
        print("Mandale 'hola' desde tu Telegram y vuelve a correr este comando.")
        return 2

    chat_id, nombre = chats[0]
    escribir_variable(env, "TELEGRAM_CHAT_ID", str(chat_id))
    print(f"Tu chat: {nombre}. Guardado en {env}.")

    try:
        llamar(token, "sendMessage", {
            "chat_id": chat_id,
            "text": "betbot conectado.\n\nAqui llegaran las apuestas EN PAPEL: "
                    "no son picks, solo miden si el modelo le gana a la casa.",
        })
    except ErrorTelegram as e:
        print(f"\nNo pude mandar la prueba: {e}")
        return 1
    print("\nListo: te mande un mensaje de prueba. Revisa Telegram.")
    return 0
