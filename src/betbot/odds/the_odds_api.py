"""Cliente de The Odds API (https://the-odds-api.com).

Presupuesto de cuota: el plan gratuito son 500 requests/mes. Cada llamada cuesta
`n_markets * n_regions` creditos, no 1. Pedir h2h+spreads+totals en us+eu son 9
creditos por llamada: a 3 deportes cada 30 minutos se agota el mes en dia y
medio. Por eso el default aqui es un solo mercado y una sola region, y el
cliente expone `credits_remaining` para vigilarlo.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

from betbot.types import BookMarket, Event, Market, Outcome, Sport

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4"


class OddsAPIError(RuntimeError):
    pass


class TheOddsAPI:
    name = "the_odds_api"

    def __init__(
        self,
        api_key: str,
        regions: str = "us",
        odds_format: str = "decimal",
        timeout: int = 20,
    ) -> None:
        if not api_key:
            raise ValueError("falta ODDS_API_KEY")
        self.api_key = api_key
        self.regions = regions
        self.odds_format = odds_format
        self.timeout = timeout
        self.credits_remaining: int | None = None
        self.credits_used: int | None = None

    def _get(self, path: str, params: dict) -> list | dict:
        query = urllib.parse.urlencode({**params, "apiKey": self.api_key})
        url = f"{BASE_URL}{path}?{query}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Estos headers son la unica forma fiable de saber cuanta cuota queda.
                remaining = resp.headers.get("x-requests-remaining")
                used = resp.headers.get("x-requests-used")
                if remaining is not None:
                    self.credits_remaining = int(float(remaining))
                if used is not None:
                    self.credits_used = int(float(used))
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:400]
            if e.code == 401:
                raise OddsAPIError("API key invalida o sin plan activo") from e
            if e.code == 429:
                raise OddsAPIError("cuota agotada o rate limit alcanzado") from e
            raise OddsAPIError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise OddsAPIError(f"error de red: {e.reason}") from e

        if self.credits_remaining is not None and self.credits_remaining < 50:
            log.warning("cuota baja en The Odds API: %d requests restantes", self.credits_remaining)
        return body

    def fetch_events(
        self, sport: Sport, markets: list[Market] | None = None
    ) -> list[Event]:
        markets = markets or [Market.MONEYLINE]
        raw = self._get(
            f"/sports/{sport.value}/odds",
            {
                "regions": self.regions,
                "markets": ",".join(m.value for m in markets),
                "oddsFormat": self.odds_format,
            },
        )
        if not isinstance(raw, list):
            raise OddsAPIError(f"respuesta inesperada: {type(raw).__name__}")
        return [e for e in (self._parse_event(item, sport) for item in raw) if e]

    def _parse_event(self, item: dict, sport: Sport) -> Event | None:
        try:
            books: list[BookMarket] = []
            for bk in item.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    try:
                        market = Market(mk["key"])
                    except ValueError:
                        continue  # mercado que no modelamos
                    outcomes = []
                    for o in mk.get("outcomes", []):
                        try:
                            outcomes.append(
                                Outcome(
                                    name=o["name"],
                                    decimal_odds=float(o["price"]),
                                    point=float(o["point"]) if o.get("point") is not None else None,
                                )
                            )
                        except (KeyError, ValueError) as err:
                            log.debug("outcome descartado en %s: %s", bk.get("key"), err)
                    if len(outcomes) >= 2:
                        books.append(
                            BookMarket(
                                bookmaker=bk["key"],
                                market=market,
                                outcomes=tuple(outcomes),
                                last_update=_parse_ts(mk.get("last_update")),
                            )
                        )
            return Event(
                event_id=item["id"],
                sport=sport,
                commence_time=_parse_ts(item["commence_time"]),
                home_team=item["home_team"],
                away_team=item["away_team"],
                books=tuple(books),
            )
        except KeyError as e:
            log.warning("evento descartado, falta campo %s", e)
            return None


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
