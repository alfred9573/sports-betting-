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

NUNCA desactivar la verificacion de certificados como atajo: eso abre la puerta
a que un intermediario altere las odds que recibes, que es exactamente el dato
del que dependen todas las decisiones del bot.
"""

from __future__ import annotations

import functools
import ssl

CERT_HELP = """
Fallo de verificacion de certificados TLS.

Es el problema clasico de Python en macOS: el interprete no usa el almacen de
certificados del sistema. Arreglalo con UNA de estas dos opciones:

  1) Instala certifi en el entorno del bot (lo mas simple):

       .venv/bin/pip install certifi

  2) Si instalaste Python desde python.org, ejecuta su script de certificados:

       "/Applications/Python 3.12/Install Certificates.command"

     (cambia 3.12 por tu version; en Finder, mira dentro de Aplicaciones
      la carpeta Python 3.x. Las comillas son necesarias por el espacio.)

Despues vuelve a intentarlo. NO desactives la verificacion de certificados:
sin ella, un intermediario podria alterar las odds que recibe el bot.
"""


@functools.lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """Contexto TLS con verificacion activada, usando certifi si esta instalado."""
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
