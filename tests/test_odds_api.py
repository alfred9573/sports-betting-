"""Tests de parseo del cliente de The Odds API — sin tocar la red."""

import pytest

from betbot.odds.the_odds_api import TheOddsAPI
from betbot.types import Market, Sport

SAMPLE = {
    "id": "abc123",
    "sport_key": "basketball_nba",
    "commence_time": "2026-01-15T00:10:00Z",
    "home_team": "Boston Celtics",
    "away_team": "Miami Heat",
    "bookmakers": [
        {
            "key": "pinnacle",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": "2026-01-14T22:00:00Z",
                    "outcomes": [
                        {"name": "Boston Celtics", "price": 1.80},
                        {"name": "Miami Heat", "price": 2.15},
                    ],
                }
            ],
        },
        {
            "key": "draftkings",
            "markets": [
                {
                    "key": "spreads",
                    "last_update": "2026-01-14T22:01:00Z",
                    "outcomes": [
                        {"name": "Boston Celtics", "price": 1.91, "point": -4.5},
                        {"name": "Miami Heat", "price": 1.91, "point": 4.5},
                    ],
                }
            ],
        },
    ],
}


@pytest.fixture
def client():
    return TheOddsAPI("fake-key")


def test_requires_api_key():
    with pytest.raises(ValueError):
        TheOddsAPI("")


def test_parse_event_basic_fields(client):
    e = client._parse_event(SAMPLE, Sport.NBA)
    assert e.event_id == "abc123"
    assert e.home_team == "Boston Celtics"
    assert e.commence_time.tzinfo is not None
    assert len(e.books) == 2


def test_parse_event_keeps_spread_points(client):
    e = client._parse_event(SAMPLE, Sport.NBA)
    spread = e.markets(Market.SPREAD)[0]
    assert spread.outcome("Boston Celtics").point == -4.5


def test_best_price_across_books(client):
    e = client._parse_event(SAMPLE, Sport.NBA)
    book, outcome = e.best_price(Market.MONEYLINE, "Miami Heat")
    assert book == "pinnacle"
    assert outcome.decimal_odds == 2.15


def test_unknown_market_is_skipped(client):
    payload = dict(SAMPLE)
    payload["bookmakers"] = [
        {"key": "x", "markets": [{"key": "player_props_weird", "outcomes": []}]}
    ]
    assert client._parse_event(payload, Sport.NBA).books == ()


def test_malformed_event_returns_none(client):
    assert client._parse_event({"id": "x"}, Sport.NBA) is None


def test_invalid_price_outcome_is_dropped(client):
    payload = dict(SAMPLE)
    payload["bookmakers"] = [
        {
            "key": "x",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "A", "price": 1.80},
                        {"name": "B", "price": "no-es-numero"},
                    ],
                }
            ],
        }
    ]
    # queda 1 solo outcome valido -> el mercado se descarta entero
    assert client._parse_event(payload, Sport.NBA).books == ()


def test_outcome_rejects_impossible_odds():
    from betbot.types import Outcome
    with pytest.raises(ValueError):
        Outcome("A", 0.95)
