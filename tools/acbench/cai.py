"""Estructuras de CSP Custom AI (``AcTools.CSP.NewBehaviour.CustomAI.*``).

Fuente: documentación oficial "Custom AI" de CSP (https://docs.assetto.cn/en/custom-ai/,
copia MIT de AssettoCN/ac-docs), consultada el 2026-11-07. Versiones: Custom AI desde
0.1.78; ``teleport_dir`` corregido y ``wheels_valid_surface`` en 0.1.79; ``SimState.v1`` con
``time_scale`` en 0.2.8. CSP instalado: 0.3.0-preview622 (build 4157, ``custom_shaders_patch.log``).

Reglas de la documentación: "all structures are aligned by 4 bytes"; ``bool`` = 1 byte (1/0);
``byte`` = ``unsigned char``; ``float3`` = 3 ``float`` (12 B); ``uint`` = 32 bits sin signo.
``ctypes`` con alineación natural da exactamente eso (ningún campo pide más de 4 bytes).
Los tamaños resultantes se fijan en ``SIZES`` y una prueba los vigila; la comprobación EN EL
JUEGO es la fase F0 (``Car<N>.v0`` debe aparecer y su ``packet_id`` crecer).

No documentado (se determina en F0, nunca se supone): numeración de ``gear`` en
``cai_car_data`` y el signo de ``steer``.
"""

from __future__ import annotations

import ctypes
from ctypes import c_bool, c_float, c_int32, c_uint8, c_uint32

PREFIX = "AcTools.CSP.NewBehaviour.CustomAI."


def controls_name(car_index: int) -> str:
    return f"{PREFIX}CarControls{car_index}.v0"


def car_name(car_index: int) -> str:
    return f"{PREFIX}Car{car_index}.v0"


def public_name(car_index: int) -> str:
    return f"{PREFIX}CarPublic{car_index}.v0"


SIM_STATE_V0 = PREFIX + "SimState.v0"
SIM_STATE_V1 = PREFIX + "SimState.v1"
DEBUG_LINES = PREFIX + "DebugLines.v0"

float3 = c_float * 3


class CarControls(ctypes.Structure):
    """``cai_car_controls`` (orden y tipos de la documentación)."""

    _fields_ = [
        ("gas", c_float),
        ("brake", c_float),
        ("clutch", c_float),
        ("steer", c_float),  # normalizado −1…1
        ("handbrake", c_float),
        ("gear_up", c_bool),
        ("gear_dn", c_bool),
        ("drs", c_bool),
        ("kers", c_bool),
        ("brake_balance_up", c_bool),
        ("brake_balance_dn", c_bool),
        ("abs_up", c_bool),
        ("abs_dn", c_bool),
        ("tc_up", c_bool),
        ("tc_dn", c_bool),
        ("turbo_up", c_bool),
        ("turbo_dn", c_bool),
        ("engine_brake_up", c_bool),
        ("engine_brake_dn", c_bool),
        ("mguk_delivery_up", c_bool),
        ("mguk_delivery_dn", c_bool),
        ("mguk_recovery_up", c_bool),
        ("mguk_recovery_dn", c_bool),
        ("mguh_mode", c_uint8),
        ("headlights", c_bool),
        ("teleport_to", c_uint8),  # 1 = boxes, 2 = teleport_pos/teleport_dir
        ("autoclutch_on_start", c_bool),
        ("autoclutch_on_change", c_bool),
        ("autoblip_active", c_bool),
        ("teleport_pos", float3),
        ("teleport_dir", float3),
        ("autoshift_active", c_bool),
    ]


class WheelData(ctypes.Structure):
    """``cai_wheel_data``."""

    _fields_ = [
        ("position", float3),
        ("contact_point", float3),
        ("contact_normal", float3),
        ("look", float3),
        ("side", float3),
        ("velocity", float3),
        ("slip_ratio", c_float),
        ("load", c_float),
        ("pressure", c_float),
        ("angular_velocity", c_float),
        ("wear", c_float),
        ("dirty_level", c_float),
        ("core_temperature", c_float),
        ("camber_rad", c_float),
        ("disc_temperature", c_float),
        ("slip", c_float),
        ("slip_angle_deg", c_float),
        ("nd_slip", c_float),
    ]


class CarData(ctypes.Structure):
    """``cai_car_data``."""

    _fields_ = [
        ("packet_id", c_int32),
        ("gas", c_float),
        ("brake", c_float),
        ("clutch", c_float),
        ("steer", c_float),  # grados de volante
        ("handbrake", c_float),
        ("fuel", c_float),
        ("gear", c_int32),
        ("rpm", c_float),
        ("speed_kmh", c_float),
        ("velocity", float3),
        ("acc_g", float3),
        ("look", float3),
        ("up", float3),
        ("position", float3),
        ("local_velocity", float3),
        ("local_angular_velocity", float3),
        ("cg_height", c_float),
        ("car_damage", c_float * 5),
        ("wheels", WheelData * 4),
        ("turbo_boost", c_float),
        ("final_ff", c_float),
        ("final_pure_ff", c_float),
        ("pit_limiter", c_bool),
        ("abs_in_action", c_bool),
        ("traction_control_in_action", c_bool),
        ("lap_time_ms", c_uint32),
        ("best_lap_time_ms", c_uint32),
        ("drivetrain_torque", c_float),
        ("spline_position", c_float),
        ("collision_depth", c_float),
        ("collision_counter", c_uint32),
        ("wheels_valid_surface", c_uint32),
    ]


class SimStateV0(ctypes.Structure):
    _fields_ = [
        ("pause", c_bool),
        ("restart_session", c_bool),
        ("disable_collisions", c_bool),
        ("extra_sleep_ms", c_uint8),
    ]


class SimStateV1(ctypes.Structure):
    _fields_ = SimStateV0._fields_ + [("time_scale", c_float)]


SIZES = {
    "CarControls": ctypes.sizeof(CarControls),
    "WheelData": ctypes.sizeof(WheelData),
    "CarData": ctypes.sizeof(CarData),
    "SimStateV0": ctypes.sizeof(SimStateV0),
    "SimStateV1": ctypes.sizeof(SimStateV1),
}
# Valores esperados a mano a partir de la documentación (prueba tests/test_g222_bench.py):
# controles 5·4 + 24·1 = 44 → teleport_pos 44…56, teleport_dir 56…68, autoshift 68 → 69 → 72;
# rueda 6·12 + 12·4 = 120; coche 148 + 4·120 + 12 + 4 (3 bool + relleno) + 28 = 672;
# SimState v0 4, v1 8.
EXPECTED_SIZES = {"CarControls": 72, "WheelData": 120, "CarData": 672, "SimStateV0": 4, "SimStateV1": 8}


def initial_controls() -> CarControls:
    """Estado inicial recomendado por la documentación: ceros y freno a 1."""
    c = CarControls()
    c.brake = 1.0
    c.clutch = 1.0  # convención AC: 1 = embrague cerrado (comprobado en los CSV de G2.21)
    return c


def to_bytes(s: ctypes.Structure) -> bytes:
    return bytes(memoryview(s).cast("B"))  # type: ignore[arg-type]


def from_bytes(cls, data: bytes):
    return cls.from_buffer_copy(data[: ctypes.sizeof(cls)])
