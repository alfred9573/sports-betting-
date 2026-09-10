"""Configuracion desde variables de entorno / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from betbot.ev.engine import EVConfig


def load_dotenv(path: str | Path = ".env") -> None:
    """Carga .env sin dependencias. No pisa variables ya definidas en el entorno."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class Settings:
    odds_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    bankroll: float = 1000.0
    kelly_fraction: float = 0.25
    min_ev: float = 0.03
    max_stake_pct: float = 0.02
    db_path: str = "data/betbot.db"
    regions: str = "us"
    quota_reserve: int = 60
    """Creditos de The Odds API que se reservan para `close`.

    Cuando quedan menos que esto, `scan` se abstiene y deja pasar solo la
    captura de cierres. La prioridad es deliberada: una senal que no encuentras
    es una oportunidad perdida, pero un cierre que no capturas es una apuesta
    que NUNCA podras evaluar por CLV — y el CLV es lo unico que dice si el bot
    tiene ventaja antes de que pasen varias temporadas. Quedarse sin cuota a
    mitad de mes con senales abiertas y sin cierres es el peor resultado
    posible."""

    @classmethod
    def from_env(cls, dotenv: str | Path = ".env") -> Settings:
        load_dotenv(dotenv)
        return cls(
            odds_api_key=os.getenv("ODDS_API_KEY", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            bankroll=float(os.getenv("BANKROLL", "1000")),
            kelly_fraction=float(os.getenv("KELLY_FRACTION", "0.25")),
            min_ev=float(os.getenv("MIN_EV", "0.03")),
            max_stake_pct=float(os.getenv("MAX_STAKE_PCT", "0.02")),
            db_path=os.getenv("DB_PATH", "data/betbot.db"),
            regions=os.getenv("ODDS_REGIONS", "us"),
            quota_reserve=int(os.getenv("QUOTA_RESERVE", "60")),
        )

    def ev_config(self) -> EVConfig:
        return EVConfig(
            min_ev=self.min_ev,
            kelly_fraction=self.kelly_fraction,
            max_stake_pct=self.max_stake_pct,
            bankroll=self.bankroll,
        )

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)
