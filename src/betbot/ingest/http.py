"""Cliente HTTP para ingesta: cache en disco, reintentos y limite de tasa.

Las tres cosas no son opcionales cuando bajas anos de historico:

  - CACHE EN DISCO: un backfill de 10 temporadas son cientos de requests. Sin
    cache, cada vez que corriges un bug de parseo vuelves a bajarlo todo, y en
    fuentes con cuota (o que banean por volumen) eso te cuesta el acceso. Con
    cache, reparsear es instantaneo y gratis.
  - LIMITE DE TASA: bajar sin pausa una fuente publica gratuita es la forma mas
    rapida de que te bloqueen la IP.
  - REINTENTOS con espera exponencial: en una descarga de 500 requests, que
    falle alguna no es una posibilidad, es una certeza.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from betbot.net import CERT_HELP, is_certificate_error, ssl_context

log = logging.getLogger(__name__)

# Varias APIs publicas no documentadas (ESPN entre ellas) devuelven 403 a
# cualquier User-Agent que no parezca un navegador. No es autenticacion ni un
# muro de pago: es un filtro basico contra scrapers, y con un UA identificable
# como "betbot/0.1" la peticion se rechaza sin llegar a los datos.
#
# Se envian cabeceras de navegador para uso PERSONAL Y DE BAJO VOLUMEN, con el
# limite de tasa puesto (1 req/s por defecto). Si una fuente publica sus
# condiciones de uso o expone una API documentada con key, usa esa en su lugar.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# Edad maxima de la copia en cache para archivos que todavia cambian. Seis horas
# da como mucho cuatro descargas al dia por archivo vivo: nada para GitHub, y
# suficiente para que un partido de anoche este dentro por la manana.
VIVO = 6 * 3600.0


def caducidad(hasta: date, hoy: date | None = None) -> float | None:
    """Politica de cache para un archivo que deja de cambiar en `hasta`.

    Antes de esa fecha (mas un mes de margen para correcciones tardias de la
    fuente) el archivo sigue vivo y caduca a las 6 horas. Despues, esta cerrado
    y la copia sirve para siempre.
    """
    hoy = hoy or date.today()
    return VIVO if hoy <= hasta + timedelta(days=30) else None


class FetchError(RuntimeError):
    pass


class CachedFetcher:
    def __init__(
        self,
        cache_dir: str | Path = "data/cache",
        min_interval: float = 1.0,
        max_retries: int = 4,
        timeout: int = 60,
        enabled: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.enabled = enabled
        self.headers = dict(headers or BROWSER_HEADERS)
        self._last_request = 0.0
        self.stats = {"hits": 0, "misses": 0, "retries": 0}

    def _cache_path(self, url: str, suffix: str) -> Path:
        digest = hashlib.sha256(url.encode()).hexdigest()[:20]
        return self.cache_dir / f"{digest}{suffix}"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def get_bytes(
        self, url: str, suffix: str = ".bin", max_age: float | None = None
    ) -> bytes:
        """Descarga con cache. `max_age` en segundos; None = la copia no caduca.

        POR QUE EXISTE `max_age`. La cache era para siempre, y eso esta bien
        para una temporada cerrada, que no va a cambiar. Pero varios archivos
        cambian cada semana (el games.csv de nflverse, el calendario de hoopR,
        la temporada en curso de cualquier fuente), y con cache eterna la
        reingesta devolvia SIEMPRE la primera copia descargada. Ni siquiera
        `ingest --force` lo arreglaba: --force salta el registro de "temporada
        ya ingerida", no la cache HTTP. Resultado: los ratings dejaban de
        recibir partidos nuevos en silencio desde la primera descarga.

        Si la copia caducada no se puede refrescar (red caida, 5xx), se usa la
        vieja con un aviso: un dato de ayer es mejor que abortar la ingesta, y
        el aviso deja claro que no es de hoy.
        """
        path = self._cache_path(url, suffix)
        caducada = False
        if self.enabled and path.exists():
            edad = time.time() - path.stat().st_mtime
            if max_age is None or edad < max_age:
                self.stats["hits"] += 1
                log.debug("cache hit: %s", url)
                return path.read_bytes()
            caducada = True
            log.debug("cache caducada (%.1fh): %s", edad / 3600, url)

        self.stats["misses"] += 1
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            if attempt:
                wait = 2.0**attempt
                self.stats["retries"] += 1
                log.warning("reintento %d en %.0fs: %s", attempt, wait, url)
                time.sleep(wait)
            self._throttle()
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(
                    req, timeout=self.timeout, context=ssl_context()
                ) as resp:
                    data = resp.read()
                if self.enabled:
                    path.write_bytes(data)
                return data
            except urllib.error.HTTPError as e:
                # 4xx (salvo 429) no se arregla reintentando.
                if e.code != 429 and 400 <= e.code < 500:
                    raise FetchError(f"HTTP {e.code} en {url}{_http_hint(e.code)}") from e
                last_error = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Un fallo de certificados no se arregla reintentando: es de
                # configuracion. Abortar de inmediato con la explicacion.
                if is_certificate_error(e):
                    raise FetchError(f"{e}\n{CERT_HELP}") from e
                last_error = e

        if caducada:
            self.stats["stale"] = self.stats.get("stale", 0) + 1
            log.warning(
                "no se pudo refrescar %s (%s); uso la copia en cache, que puede "
                "no traer los ultimos partidos", url, last_error,
            )
            return path.read_bytes()
        raise FetchError(f"fallo tras {self.max_retries} intentos: {url} ({last_error})")

    def get_text(
        self, url: str, encoding: str = "utf-8", suffix: str = ".txt",
        max_age: float | None = None,
    ) -> str:
        return self.get_bytes(url, suffix, max_age=max_age).decode(encoding, errors="replace")

    def get_json(self, url: str, max_age: float | None = None) -> dict | list:
        raw = self.get_text(url, suffix=".json", max_age=max_age)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # No dejar en cache una respuesta corrupta: envenenaria las corridas siguientes.
            self._cache_path(url, ".json").unlink(missing_ok=True)
            raise FetchError(f"JSON invalido en {url}: {e}") from e


def _http_hint(code: int) -> str:
    """Pista accionable segun el codigo, en vez de un numero a secas."""
    if code == 403:
        return (
            "\n  403 = la peticion llego y fue RECHAZADA (no es un fallo de red).\n"
            "  Causa habitual: la fuente filtra por User-Agent. El fetcher ya\n"
            "  envia cabeceras de navegador; si aun asi falla, puede ser bloqueo\n"
            "  por region o por volumen. Prueba desde el navegador la misma URL."
        )
    if code == 404:
        return (
            "\n  404 = esa ruta no existe. Si es un dataset por temporada, puede\n"
            "  que esa temporada aun no este publicada."
        )
    return ""
