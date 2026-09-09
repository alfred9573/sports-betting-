"""Contrato comun a todos los modelos por deporte.

Cada deporte es un modulo independiente, pero todos exponen la misma interfaz
para que el motor de EV no sepa nada de basket, beisbol ni futbol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from betbot.types import Event, ModelProbabilities


@runtime_checkable
class ProbabilityModel(Protocol):
    name: str

    def predict(self, event: Event) -> ModelProbabilities | None:
        """Probabilidades reales estimadas, o None si falta informacion.

        Devolver None es la respuesta correcta ante un equipo sin historial
        suficiente: mejor no apostar que apostar con un rating prior.
        """
        ...
