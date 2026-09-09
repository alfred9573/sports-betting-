"""Contrato de proveedor de odds."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from betbot.types import Event, Market, Sport


@runtime_checkable
class OddsProvider(Protocol):
    name: str

    def fetch_events(self, sport: Sport, markets: list[Market]) -> list[Event]:
        ...
