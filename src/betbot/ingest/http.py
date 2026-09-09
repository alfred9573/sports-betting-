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
from pathlib import Path

log = logging.getLogger(__name__)

USER_AGENT = "betbot/0.1 (analisis deportivo personal)"


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
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.enabled = enabled
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

    def get_bytes(self, url: str, suffix: str = ".bin") -> bytes:
        path = self._cache_path(url, suffix)
        if self.enabled and path.exists():
            self.stats["hits"] += 1
            log.debug("cache hit: %s", url)
            return path.read_bytes()

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
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                if self.enabled:
                    path.write_bytes(data)
                return data
            except urllib.error.HTTPError as e:
                # 4xx (salvo 429) no se arregla reintentando.
                if e.code != 429 and 400 <= e.code < 500:
                    raise FetchError(f"HTTP {e.code} en {url}") from e
                last_error = e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = e

        raise FetchError(f"fallo tras {self.max_retries} intentos: {url} ({last_error})")

    def get_text(self, url: str, encoding: str = "utf-8", suffix: str = ".txt") -> str:
        return self.get_bytes(url, suffix).decode(encoding, errors="replace")

    def get_json(self, url: str) -> dict | list:
        raw = self.get_text(url, suffix=".json")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            # No dejar en cache una respuesta corrupta: envenenaria las corridas siguientes.
            self._cache_path(url, ".json").unlink(missing_ok=True)
            raise FetchError(f"JSON invalido en {url}: {e}") from e
