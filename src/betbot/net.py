"""Contexto TLS compartido por todos los clientes HTTP.

POR QUE ESTE MODULO EXISTE

Python instalado desde python.org en macOS NO usa el almacen de certificados del
sistema: trae el suyo propio, que queda vacio hasta que se ejecuta a mano el
script "Install Certificates.command" del instalador. Hasta entonces, cualquier
peticion HTTPS falla con:

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

El error no dice nada de macOS ni de certificados sin instalar, asi que parece un
problema de red, de la API o de la key. Le pasa a practicamente todo el mundo que
instala Python en un Mac y no es evidente de diagnosticar.

La solucion aqui es usar el bundle de `certifi` cuando esta disponible — es el
mismo que usa `requests` y por eso `requests` "simplemente funciona" donde
urllib falla. Si no esta, se cae al contexto por defecto y el mensaje de error
explica que hacer.

PERO CERTIFI NO PUEDE SER LO PRIMERO. Si alguien corre el bot detras de un
proxy corporativo o de un entorno que inspecciona TLS, la CA de ese proxy esta
instalada en el sistema y NO en certifi. Anteponer certifi a ciegas rompe esos
entornos con el mismo error que este modulo existe para evitar, y ademas ignora
en silencio una CA que el usuario instalo a proposito. Por eso se respeta
primero SSL_CERT_FILE / REQUESTS_CA_BUNDLE, que es la via estandar para
declarar un bundle propio, y solo despues se recurre a certifi.

NUNCA desactivar la verificacion de certificados como atajo: eso abre la puerta
a que un intermediario altere las odds que recibes, que es exactamente el dato
del que dependen todas las decisiones del bot.
"""

from __future__ import annotations

import functools
import os
import ssl
from pathlib import Path

CERT_HELP = """
Fallo de verificacion de certificados TLS.

Es el problema clasico de Python en macOS: el interprete no usa el almacen de
certificados del sistema. Arreglalo con UNA de estas opciones:

  1) Instala certifi en el entorno del bot (lo mas simple):

       .venv/bin/pip install certifi

  2) Si instalaste Python desde python.org, ejecuta su script de certificados:

       "/Applications/Python 3.12/Install Certificates.command"

     (cambia 3.12 por tu version; en Finder, mira dentro de Aplicaciones
      la carpeta Python 3.x. Las comillas son necesarias por el espacio.)

  3) Si estas detras de un proxy corporativo o de una red que inspecciona el
     trafico, el certificado correcto es el de esa red, no el de certifi.
     Apunta a su bundle:

       export SSL_CERT_FILE=/ruta/al/bundle-de-tu-red.crt

Despues vuelve a intentarlo. NO desactives la verificacion de certificados:
sin ella, un intermediario podria alterar las odds que recibe el bot.
"""


# Variables estandar para declarar un bundle de CAs propio. SSL_CERT_FILE es la
# de OpenSSL/Python; REQUESTS_CA_BUNDLE la de requests. Se miran las dos porque
# quien configura un proxy suele poner una u otra, no ambas.
VARS_BUNDLE = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def bundle_configurado() -> str | None:
    """Ruta al bundle declarado por entorno, si existe y es legible.

    Una ruta que apunta a un fichero inexistente se ignora en vez de reventar:
    `create_default_context` lanzaria un error de fichero que no se parece en
    nada al problema real, y dejar una variable vieja apuntando a un fichero
    borrado es de lo mas comun.
    """
    for var in VARS_BUNDLE:
        ruta = os.environ.get(var, "").strip()
        if ruta and Path(ruta).is_file():
            return ruta
    return None


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Contexto TLS con la verificacion SIEMPRE activada.

    Orden: bundle declarado por entorno > certifi > almacen por defecto.
    Ninguna de las tres ramas desactiva la verificacion, y no hay parametro
    para hacerlo: si hiciera falta un atajo, seria este el sitio donde alguien
    lo anadiria, y no debe existir.
    """
    ruta = bundle_configurado()
    if ruta:
        return ssl.create_default_context(cafile=ruta)
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def is_certificate_error(error: BaseException) -> bool:
    """Detecta el fallo de certificados en cualquier punto de la cadena de causas."""
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False
