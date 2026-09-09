"""Salida a consola. El canal por defecto para desarrollo y backtesting."""

from __future__ import annotations

from betbot.types import Signal


class ConsoleAlerter:
    name = "console"

    def send(self, signals: list[Signal]) -> int:
        if not signals:
            print("Sin senales de valor en este escaneo.")
            return 0
        print(f"\n=== {len(signals)} SENAL(ES) DE VALOR ===\n")
        for i, s in enumerate(signals, 1):
            print(f"{i}. {s}\n")
        total = sum(s.stake_units for s in signals)
        print(f"Exposicion total sugerida: {total:.2f} unidades")
        print("Recordatorio: apuesta manual. Verifica que la linea siga viva antes de poner.")
        return len(signals)
