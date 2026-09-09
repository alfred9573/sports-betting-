"""Tipos de dominio compartidos por todo el pipeline.

Todo el sistema habla en estos objetos: los proveedores de odds producen
`MarketSnapshot`, los modelos producen `ModelProbabilities`, y el motor de EV
los cruza para emitir `Signal`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Sport(StrEnum):
    NBA = "basketball_nba"
    MLB = "baseball_mlb"
    NFL = "americanfootball_nfl"
    SOCCER_LIGA_MX = "soccer_mexico_ligamx"
    SOCCER_EPL = "soccer_epl"
    SOCCER_LA_LIGA = "soccer_spain_la_liga"
    SOCCER_SERIE_A = "soccer_italy_serie_a"
    SOCCER_BUNDESLIGA = "soccer_germany_bundesliga"
    SOCCER_LIGUE_1 = "soccer_france_ligue_one"
    SOCCER_UCL = "soccer_uefa_champs_league"


class Market(StrEnum):
    MONEYLINE = "h2h"
    SPREAD = "spreads"
    TOTALS = "totals"


@dataclass(frozen=True)
class Outcome:
    """Un lado apostable de un mercado, con su precio decimal en un libro."""

    name: str                 # "Los Angeles Lakers", "Over", "Draw"
    decimal_odds: float
    point: float | None = None  # linea de spread/total, None en moneyline

    def __post_init__(self) -> None:
        if self.decimal_odds <= 1.0:
            raise ValueError(f"odds decimales invalidas: {self.decimal_odds}")


@dataclass(frozen=True)
class BookMarket:
    """El mercado tal como lo cotiza un libro concreto en un instante."""

    bookmaker: str
    market: Market
    outcomes: tuple[Outcome, ...]
    last_update: datetime

    def outcome(self, name: str) -> Outcome | None:
        for o in self.outcomes:
            if o.name == name:
                return o
        return None


@dataclass(frozen=True)
class Event:
    """Un partido con todas sus cotizaciones en un snapshot."""

    event_id: str
    sport: Sport
    commence_time: datetime
    home_team: str
    away_team: str
    books: tuple[BookMarket, ...] = ()

    def markets(self, market: Market) -> list[BookMarket]:
        return [b for b in self.books if b.market == market]

    def best_price(self, market: Market, outcome_name: str) -> tuple[str, Outcome] | None:
        """Line shopping: mejor precio disponible para un lado (capa complementaria)."""
        best: tuple[str, Outcome] | None = None
        for bm in self.markets(market):
            o = bm.outcome(outcome_name)
            if o is None:
                continue
            if best is None or o.decimal_odds > best[1].decimal_odds:
                best = (bm.bookmaker, o)
        return best


@dataclass(frozen=True)
class ModelProbabilities:
    """Salida de un modelo: probabilidades reales estimadas por resultado.

    Las claves deben coincidir con los `Outcome.name` del mercado que se evalua.
    """

    event_id: str
    market: Market
    probs: dict[str, float]
    model_name: str
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        total = sum(self.probs.values())
        if not 0.98 <= total <= 1.02:
            raise ValueError(f"probabilidades no suman ~1 (suma={total:.4f})")


@dataclass(frozen=True)
class Signal:
    """Alerta accionable: hay EV positivo suficiente en un lado concreto."""

    event_id: str
    sport: Sport
    commence_time: datetime
    matchup: str
    market: Market
    selection: str
    point: float | None
    bookmaker: str
    decimal_odds: float
    model_prob: float
    fair_prob: float          # prob implicita del mercado, ya sin vig
    ev: float                 # EV por unidad apostada
    edge: float               # model_prob - fair_prob
    kelly_stake: float        # fraccion del bankroll recomendada
    stake_units: float        # stake en unidades monetarias
    model_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __str__(self) -> str:
        pt = f" {self.point:+g}" if self.point is not None else ""
        return (
            f"[{self.sport.name}] {self.matchup} | {self.market.value} "
            f"{self.selection}{pt} @ {self.decimal_odds:.2f} ({self.bookmaker})\n"
            f"  modelo {self.model_prob:.1%} vs mercado {self.fair_prob:.1%} "
            f"| EV {self.ev:+.2%} | stake {self.stake_units:.2f} "
            f"({self.kelly_stake:.2%} bank)"
        )
