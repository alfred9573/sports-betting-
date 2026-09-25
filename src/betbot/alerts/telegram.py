"""Alertas por Telegram via Bot API (stdlib, sin dependencias)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from betbot.net import ssl_context
from betbot.types import Signal

log = logging.getLogger(__name__)


class TelegramAlerter:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: int = 15) -> None:
        if not bot_token or not chat_id:
            raise ValueError("faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def _format(self, s: Signal) -> str:
        pt = f" {s.point:+g}" if s.point is not None else ""
        mercado = {
            "h2h": "Ganador", "spreads": "Hándicap", "totals": "Total",
        }.get(s.market.value, s.market.value)

        # En lineshop la referencia ES el precio sharp, asi que "modelo vs
        # mercado" no dice nada: lo informativo es de donde sale la ventaja.
        if s.model_name.startswith("lineshop"):
            origen = f"Precio sharp {s.fair_prob:.1%} vs este libro {1 / s.decimal_odds:.1%}"
        else:
            origen = f"Modelo {s.model_prob:.1%} vs mercado {s.fair_prob:.1%}"

        return (
            f"*{s.sport.name}* — {s.matchup}\n"
            f"{mercado}: `{s.selection}{pt}` @ *{s.decimal_odds:.2f}* "
            f"({s.bookmaker})\n"
            f"{origen}\n"
            f"EV *{s.ev:+.2%}* · stake {s.stake_units:.2f} u ({s.kelly_stake:.2%})\n"
            f"Inicio: {s.commence_time:%d/%m %H:%M} UTC"
        )

    def send_text(self, texto: str) -> bool:
        """Mensaje suelto, para comprobar la configuracion."""
        return self._post(texto)

    # Telegram corta en 4096 caracteres. Se deja margen y se parte por lineas
    # para no cortar una apuesta a la mitad.
    LIMITE = 3900

    def send_plain(self, texto: str) -> bool:
        """Texto SIN formato, partido en trozos si es largo.

        Sin Markdown a proposito: los nombres de mercado (`player_pass_yds`) y
        algunos nombres de jugador llevan guiones bajos o asteriscos, que el
        Markdown de Telegram interpreta como formato. Un mensaje mal formado se
        rechaza entero con un 400, y el aviso se pierde sin que nadie lo vea.
        """
        trozos, actual = [], ""
        for linea in texto.split("\n"):
            if len(actual) + len(linea) + 1 > self.LIMITE and actual:
                trozos.append(actual)
                actual = ""
            actual = f"{actual}\n{linea}" if actual else linea
        if actual:
            trozos.append(actual)
        return all([self._post(t, parse_mode=None) for t in trozos])

    def send(self, signals: list[Signal]) -> int:
        sent = 0
        for s in signals:
            if self._post(self._format(s)):
                sent += 1
        return sent

    def _post(self, text: str, parse_mode: str | None = "Markdown") -> bool:
        cuerpo = {"chat_id": self.chat_id, "text": text}
        if parse_mode:
            cuerpo["parse_mode"] = parse_mode
        payload = json.dumps(cuerpo).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=ssl_context()
            ) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            # Una alerta perdida no debe tumbar el escaneo entero.
            log.error("fallo enviando alerta a Telegram: %s", e)
            return False
