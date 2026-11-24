"""G2.25: estructura ``Beam2acBench.Geo.v1`` (Lua → Python) de la app ``beam2ac_g225_bench``.

Complementa el puente de G2.22 (:mod:`tools.acbench.bridge`, que no cambia: mismas
``Cmd.v1``/``Tel.v1``) con lo que hace falta para validar geometría, collider y motor:

* pose visual (``car.bodyTransform``) y física (``car.transform``) de la carrocería;
* ``car.graphicsOffset`` (lo que AC leyó de ``car.ini``);
* por rueda: posición física (``wheels[i].position``), punto de contacto, nulo ``WHEEL_xx`` de la
  escena (``ac.findNodes``) y las dos en el marco de la carrocería (``car.worldToLocal``);
* colisión (``collisionPosition`` en el marco del coche, profundidad, con quién);
* limitador, marcha engranada, aceleración, AABB del coche (``aabbCenter``/``aabbSize``);
* acciones propias (capturas con ``ac.GeometryShot``, volcado de geometría) con su propio
  ``act_done_seq`` (la app de G2.22 confirma las acciones desconocidas sin hacer nada).

Una sola definición: ``lua_cdef(GEO_FIELDS)`` para FFI y ``Geo`` para ctypes (mismas reglas de
alineación que :mod:`tools.acbench.bridge`).
"""

from __future__ import annotations

import ctypes

from tools.acbench import shm
from tools.acbench.bridge import _struct, lua_cdef  # noqa: F401  (lua_cdef se reexporta)

GEO_NAME = "Beam2acBench.Geo.v1"
MAGIC_GEO = 0x25AC2225
VERSION = 1

# acciones propias de G2.25 (Cmd.action); la app las confirma en Geo.act_done_seq/act_result
ACT_CAPTURE = 13  # capturas de la escena; Cmd.run_id = etiqueta; Cmd.run_no = juego de vistas
ACT_GEO_DUMP = 14  # volcado JSON (pose, ruedas, nulos, nodos del coche); Cmd.run_id = etiqueta
ACT_GEO_LOG_OPEN = 15  # CSV de geometría <car>_<run_id>_geo.csv
ACT_GEO_LOG_CLOSE = 16

VIEWS_FULL = 1  # 7 vistas: dos laterales, frontal, trasera, cenital (ortográficas) y dos 3/4
VIEWS_SIDE = 2  # solo las dos laterales ortográficas cercanas (holgura y ruedas)

GEO_FIELDS: list[tuple[str, str, int]] = [
    ("phys_ms", "d", 1), ("sim_ms", "d", 1),
    ("magic", "i", 1), ("version", "i", 1), ("struct_size", "i", 1), ("frame", "i", 1),
    ("act_done_seq", "i", 1), ("act_result", "i", 1), ("shots_done", "i", 1), ("limiter_on", "i", 1),
    ("engaged_gear", "i", 1), ("nodes_found", "i", 1), ("collided_with", "i", 1), ("geo_rows", "i", 1),
    ("graphics_offset", "f", 3), ("cg_height", "f", 1), ("rpm_limiter", "f", 1), ("rpm_minimum", "f", 1),
    ("body_pos", "f", 3), ("body_look", "f", 3), ("body_up", "f", 3),
    ("phys_pos", "f", 3), ("phys_look", "f", 3), ("phys_up", "f", 3),
    ("wheel_pos", "f", 12), ("wheel_contact", "f", 12), ("wheel_node", "f", 12),
    ("wheel_node_body", "f", 12), ("wheel_pos_body", "f", 12),
    ("susp_travel", "f", 4), ("tyre_radius", "f", 4), ("loaded_radius", "f", 4),
    ("collision_pos", "f", 3), ("collision_depth", "f", 1), ("aabb_center", "f", 3), ("aabb_size", "f", 3),
    ("accel", "f", 3), ("ground_distance", "f", 1),
    ("error", "c", 160), ("last_file", "c", 200),
]

Geo = _struct("Geo", GEO_FIELDS)


def geo_dict(g) -> dict:
    out: dict = {}
    for n, typ, k in GEO_FIELDS:
        v = getattr(g, n)
        if typ == "c":
            out[n] = (v if isinstance(v, bytes) else bytes(v)).split(b"\0", 1)[0].decode("utf-8", "replace")
        elif k > 1:
            out[n] = list(v)
        else:
            out[n] = v
    return out


class GeoReader:
    """Lectura de ``Geo.v1`` (se abre cuando la app la crea)."""

    def __init__(self, registry: shm.Registry) -> None:
        self.registry = registry
        self._region = None

    def read(self) -> dict | None:
        if self._region is None:
            self._region = self.registry.open_existing(GEO_NAME, ctypes.sizeof(Geo))
            if self._region is None:
                return None
        g = Geo.from_buffer_copy(self._region.read(0, ctypes.sizeof(Geo)))
        if g.magic != MAGIC_GEO or g.version != VERSION:
            return None
        if g.struct_size != ctypes.sizeof(Geo):
            raise RuntimeError(f"Geo: tamaño Lua {g.struct_size} ≠ Python {ctypes.sizeof(Geo)}")
        return geo_dict(g)
