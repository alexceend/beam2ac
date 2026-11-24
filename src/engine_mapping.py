"""
Capa aislada para el mapeo de parametros de motor BeamNG -> Assetto Corsa.
"""

from typing import Any


class EngineMapper:
    def __init__(self, beamng_data: dict[str, Any]):
        self.data = beamng_data

    def map_torque_curve(self) -> list[tuple[int, float]] | None:
        # Equivalencia segura: torque table -> POWER_CURVE
        return self.data.get("torque_table")

    def map_idle_rpm(self) -> int | None:
        # Equivalencia segura: idleRPM -> MINIMUM
        return self.data.get("idle_rpm")

    def map_max_rpm(self) -> int | None:
        # Equivalencia segura: maxRPM -> LIMITER
        return self.data.get("max_rpm")

    def map_inertia(self) -> None:
        # TODO: Conversion pendiente
        return None

    def map_limiter_hz(self) -> None:
        # TODO: Conversion pendiente
        return None

    def map_friction(self) -> None:
        # TODO: Conversion pendiente
        return None
