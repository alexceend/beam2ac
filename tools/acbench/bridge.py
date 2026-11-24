"""Puente propio entre el controlador Python y la app Lua ``beam2ac_lsd_bench``.

Dos objetos de memoria compartida (definición ÚNICA aquí; ``lua_cdef()`` genera el cuerpo de
la estructura para ``ac.writeMemoryMappedFile``/``ac.readMemoryMappedFile`` de CSP y
``ctypes`` la misma estructura en Python):

* ``Beam2acBench.Cmd.v1`` (Python → Lua; lo crea Python): latido ``seq``, segmento activo
  (``seg_uid`` y ``seg_meas`` = ventana de medida), mandos para el modo ``lua_override``
  (``ac.overrideCarControls``), acciones puntuales (teletransporte, velocidad, colisiones,
  cierre del CSV) con ``action_seq``;
* ``Beam2acBench.Tel.v1`` (Lua → Python; lo crea la app): telemetría de cada fotograma
  (incluido ``feedbackTorque``, que Custom AI no publica), estado del registrador y errores.

El registrador sigue escribiendo el CSV de G2.21 (mismas columnas) con columnas nuevas:
``phys_ms`` (``ac.StateCar.timestamp``), ``seg_uid``, ``seg_meas``, ``cmd_seq``, ``ctl_state``.
Así cada fila lleva el segmento que el controlador tenía activo: la sincronización no depende
de relojes distintos.

Alineación: los ``double`` van primero y el resto son de 4 bytes o ``char``: la disposición
natural de ``ctypes`` y la de LuaJIT FFI coinciden (sin ``#pragma pack``).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

from tools.acbench import shm

CMD_NAME = "Beam2acBench.Cmd.v1"
TEL_NAME = "Beam2acBench.Tel.v1"
MAGIC_CMD = 0x22AC0222
MAGIC_TEL = 0x22AC1222
VERSION = 1

MODE_NONE, MODE_LUA, MODE_CAI = 0, 1, 2
# acciones puntuales (Cmd.action); la app responde en Tel.action_done_seq / action_result
ACT_NONE = 0
ACT_TELEPORT = 1  # physics.setCarPosition(0, pos, dir) + physics.setCarVelocity(0, vel)
ACT_STOP_CAR = 2  # physics.setCarVelocity(0, 0)
ACT_NO_COLLISIONS = 3  # physics.disableCarCollisions(0, true)
ACT_RESET_STATE = 4  # physics.resetCarState(0)
ACT_CLOSE_LOG = 5  # vuelca y cierra el CSV actual
ACT_OPEN_LOG = 6  # abre un CSV nuevo <car_id>_<run_id>.csv
ACT_PROBE = 7  # comprueba qué funciones de physics.* existen (bits en phys_api)
ACT_TELEPORT_START = 8  # physics.teleportCarTo(0, ac.SpawnSet.Start)
ACT_TELEPORT_PITS = 9  # physics.teleportCarTo(0, ac.SpawnSet.Pits)
ACT_START_SESSION = 10  # ac.simulateCustomHotkeyPress('__CM_START_SESSION') (CSP «Start race»)
ACT_START_STOP = 11  # ac.simulateCustomHotkeyPress('__CM_START_STOP_SESSION') (CSP «Start/setup»)
ACT_ENGAGE_GEAR = 12  # physics.engageGear(0, gear_req): -1 R, 0 N, 1… (F1: gear_dn no pasa de 1.ª a N)

# bits de Tel.phys_api (resultado de ACT_PROBE: la función existe Y no dio error al llamarla
# con argumentos inocuos)
API_SET_POSITION, API_SET_VELOCITY, API_NO_COLLISIONS, API_OVERRIDE, API_RESET = 1, 2, 4, 8, 16

# (nombre, tipo, n) — tipo: d double, i int32, f float, c char
CMD_FIELDS: list[tuple[str, str, int]] = [
    ("magic", "i", 1), ("version", "i", 1), ("struct_size", "i", 1), ("seq", "i", 1), ("seg_uid", "i", 1), ("seg_meas", "i", 1),
    ("state", "i", 1), ("mode", "i", 1), ("log_enable", "i", 1), ("gear_req", "i", 1), ("action", "i", 1),
    ("action_seq", "i", 1), ("run_no", "i", 1),
    ("gas", "f", 1), ("brake", "f", 1), ("clutch", "f", 1), ("steer", "f", 1), ("handbrake", "f", 1),
    ("pos", "f", 3), ("dir", "f", 3), ("vel", "f", 3),
    ("run_id", "c", 32),
]

TEL_FIELDS: list[tuple[str, str, int]] = [
    ("phys_ms", "d", 1), ("sim_ms", "d", 1),
    ("magic", "i", 1), ("version", "i", 1), ("struct_size", "i", 1), ("frame", "i", 1), ("cmd_seq_seen", "i", 1), ("gear", "i", 1),
    ("collided_with", "i", 1), ("wheels_outside", "i", 1), ("rows", "i", 1), ("override_active", "i", 1),
    ("phys_api", "i", 1), ("action_done_seq", "i", 1), ("action_result", "i", 1), ("is_live", "i", 1),
    ("paused", "i", 1), ("cars_count", "i", 1), ("seg_uid_seen", "i", 1), ("phys_available", "i", 1), ("in_menu", "i", 1), ("watchdog", "i", 1),
    ("rpm", "f", 1), ("speed_kmh", "f", 1), ("gas", "f", 1), ("brake", "f", 1), ("clutch", "f", 1),
    ("steer", "f", 1), ("handbrake", "f", 1), ("drivetrain_torque", "f", 1), ("collision_depth", "f", 1),
    ("fps", "f", 1), ("sim_dt", "f", 1), ("gear_ratio", "f", 1), ("final_ratio", "f", 1),
    ("pos", "f", 3), ("vel", "f", 3), ("look", "f", 3), ("up", "f", 3), ("local_vel", "f", 3),
    ("local_ang_vel", "f", 3),
    ("shaftv", "f", 4), ("ang", "f", 4), ("load", "f", 4), ("fbt", "f", 4), ("damage", "f", 5),
    ("car_id", "c", 64), ("file", "c", 200), ("error", "c", 128),
]

_CT = {"d": ctypes.c_double, "i": ctypes.c_int32, "f": ctypes.c_float, "c": ctypes.c_char}
_LUA = {"d": "double", "i": "int", "f": "float", "c": "char"}


def _struct(name: str, fields: list[tuple[str, str, int]]):
    return type(name, (ctypes.Structure,), {"_fields_": [
        (n, _CT[t] * k if k > 1 else _CT[t]) for n, t, k in fields]})


Cmd = _struct("Cmd", CMD_FIELDS)
Tel = _struct("Tel", TEL_FIELDS)


def lua_cdef(fields: list[tuple[str, str, int]]) -> str:
    """Cuerpo de la estructura para la API de CSP (cadena C de LuaJIT FFI)."""
    return " ".join(f"{_LUA[t]} {n}{f'[{k}]' if k > 1 else ''};" for n, t, k in fields)


def offsets(cls) -> dict[str, int]:
    return {n: getattr(cls, n).offset for n, *_ in cls._fields_}


def tel_dict(t) -> dict:
    out: dict = {}
    for n, typ, k in TEL_FIELDS:
        v = getattr(t, n)
        if typ == "c":
            out[n] = bytes(v).split(b"\0", 1)[0].decode("utf-8", "replace") if not isinstance(v, bytes) \
                else v.decode("utf-8", "replace")
        elif k > 1:
            out[n] = list(v)
        else:
            out[n] = v
    return out


@dataclass
class Bridge:
    """Lado Python del puente. ``registry`` = :class:`shm.WinRegistry` o el doble en memoria."""

    registry: shm.Registry

    def __post_init__(self) -> None:
        init = Cmd()
        init.magic, init.version, init.brake, init.clutch = MAGIC_CMD, VERSION, 1.0, 1.0
        init.gear_req = -99
        init.struct_size = ctypes.sizeof(Cmd)
        self.cmd = init
        self._cmd_region = self.registry.create(CMD_NAME, ctypes.sizeof(Cmd), bytes(memoryview(init).cast("B")))
        self._tel_region = None

    def publish(self) -> None:
        self.cmd.seq = (self.cmd.seq + 1) % 2_000_000_000
        self._cmd_region.write(bytes(memoryview(self.cmd).cast("B")))

    def request(self, action: int, **values) -> int:
        for k, v in values.items():
            if k in ("pos", "dir", "vel"):
                getattr(self.cmd, k)[:] = list(v)
            elif k == "run_id":
                self.cmd.run_id = v.encode("ascii")[:31]
            else:
                setattr(self.cmd, k, v)
        self.cmd.action = action
        self.cmd.action_seq += 1
        self.publish()
        return self.cmd.action_seq

    def telemetry(self) -> dict | None:
        if self._tel_region is None:
            self._tel_region = self.registry.open_existing(TEL_NAME, ctypes.sizeof(Tel))
            if self._tel_region is None:
                return None
        t = Tel.from_buffer_copy(self._tel_region.read(0, ctypes.sizeof(Tel)))
        if t.magic != MAGIC_TEL or t.version != VERSION:
            return None
        if t.struct_size != ctypes.sizeof(Tel):
            raise RuntimeError(f"Tel: tamaño Lua {t.struct_size} ≠ Python {ctypes.sizeof(Tel)}")
        return tel_dict(t)

    def safe_stop(self) -> None:
        """Mandos a reposo (modo Lua): gas 0, freno 1, volante 0, fuera de ventana de medida."""
        c = self.cmd
        c.gas, c.brake, c.steer, c.handbrake, c.seg_meas = 0.0, 1.0, 0.0, 0.0, 0
        self.publish()
