"""Tracción, ejes motrices y diferenciales de la configuración activa (G2.5c).

Lógica de ORIGEN que antes vivía en ``src/ac/drivetrain.py`` (``analyse`` y
``awd_axles``). Interpreta el grafo ya leído por :mod:`src.extract.drivetrain` junto
con los ejes medidos en los nodos (:mod:`src.extract.mass_geometry`). No sabe nada de
Assetto Corsa: no decide qué se escribe ni cómo.

Reglas (sin cambios respecto a la versión anterior; ver docs/research/transmision.md y
docs/research/catalogo_motor_awd_vmax_2026-10-30.md):

* Ejes: exactamente 2 (fase 2); el primero es el delantero.
* Tracción ``FWD``/``RWD``/``AWD``: qué ejes tienen ruedas motrices
  (``src.extract.drivetrain.traction_type``, reexportada aquí; derivado). Una rueda
  motriz sin eje conocido es un error.
* Radio de la rueda motriz: único entre las ruedas motrices (si difieren, error).
* Papel de cada diferencial:
  - 2WD (``Drivetrain.differential``): el del eje motriz;
  - tracción total con diferencial central (``Drivetrain.awd``): cada diferencial de
    eje va al eje que contiene TODAS sus ruedas; un diferencial con ruedas de los dos
    ejes, o dos en el mismo eje, es un error. Reparto: salida 1 = ``diffTorqueSplit``
    (por defecto 0,5, ``differential.lua`` l. 315-317), salida 2 = el resto
    (l. 64-65).
  - Ruedas motrices en los dos ejes SIN diferencial central reconocido (p. ej. un único
    diferencial que reparte entre ejes): tracción ``AWD`` sin papeles asignados; no se
    supone ninguna estructura (``axles`` vacío, ``center`` ``None``).
* ``diffType`` se conserva tal cual (``open``, ``lsd``, ``torqueVectoring``…): no se
  agrupan tipos ni se deduce nada de él. En particular, ``torqueVectoring`` es un
  diferencial de UN eje con un controlador externo (``differential.lua`` l. 126-134);
  no implica tracción total.

G2.7 (G2.6d): :func:`analyse_source_drivetrain` reúne lo que es del ORIGEN y no
necesita el modelo de motor: el grafo (``build_drivetrain``), la clasificación de la
tracción (:func:`classify_traction`) y el ``vehicleController`` en bruto. El modelo de
motor solo hace falta para las velocidades por marcha (destino/informe).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.extract.drivetrain import (  # noqa: F401  (traction_type: API de este módulo)
    Device,
    Drivetrain,
    DrivetrainDataError,
    build_drivetrain,
    traction_type,
)
from src.extract.automatic_transmission import read_transmission
from src.jbeam import ResolvedConfig
from src.jbeam.merge import merge_section

# Opciones de ``vehicleController`` que se conservan en bruto (sin interpretar).
SHIFT_CONTROLLER_KEYS = (
    "transmissionShiftDelay",
    "clutchInRate",
    "clutchOutRate",
    "revMatchThrottle",
    "topSpeedLimit",
)

# Campos del diferencial que se conservan con nombre neutro ← clave de BeamNG
# (``differential.lua``: diffType l. 126-134; lsdPreload/lsdLockCoef/lsdRevLockCoef
# l. 57; friction). Solo se copian las claves presentes: ausente ≠ None.
DIFF_FIELDS: tuple[tuple[str, str], ...] = (
    ("kind", "diffType"),
    ("preload", "lsdPreload"),
    ("lock_coef", "lsdLockCoef"),
    ("rev_lock_coef", "lsdRevLockCoef"),
    ("friction", "friction"),
)
SOURCE_KEY = dict(DIFF_FIELDS)

# G2.12: valores que ``differential.lua`` (sha 1b53a791…) usa en un diferencial ``lsd``
# cuando el JBeam no declara la clave (``new``, l. 333-335). ``rev_lock_coef`` vale el
# ``lsdLockCoef`` efectivo (declarado o por defecto). Solo para el modo ``lsd``: en
# ``open``/``locked``/… esas claves no se usan (l. 126-137).
DIFF_LUA = "lua/vehicle/powertrain/differential.lua"
DIFF_LUA_SHA256 = "1b53a79101bdd31cda16b7b00e86ee1494b909149aca6d42d236e964c6e0aa0a"
LSD_LUA_DEFAULTS: dict[str, tuple[float, int]] = {
    "preload": (50.0, 333),
    "lock_coef": (0.2, 334),
}
LSD_REV_LOCK_LINE = 335


@dataclass(frozen=True)
class DiffSetup:
    """Parámetros de un diferencial del origen con nombres neutros."""

    device: str  # nombre del dispositivo en el origen (procedencia)
    values: dict[str, Any]  # solo los campos presentes de DIFF_FIELDS
    params: dict[str, Any]  # todas las opciones del dispositivo, tal cual

    @classmethod
    def from_device(cls, dev: Device) -> "DiffSetup":
        p = dev.params
        return cls(
            dev.name,
            {name: p[key] for name, key in DIFF_FIELDS if key in p},
            dict(p),
        )

    def get(self, name: str) -> Any:
        return self.values.get(name)

    def has(self, name: str) -> bool:
        return name in self.values

    def source_key(self, name: str) -> str:
        return f"{self.device}.{SOURCE_KEY[name]}"

    def lsd_effective(self, name: str) -> tuple[Any, bool, str] | None:
        """Valor que usa BeamNG para ``preload``/``lock_coef``/``rev_lock_coef`` de un
        ``lsd`` (G2.12): ``(valor, es_por_defecto, localizador)``. El localizador es
        ``""`` si el valor se lee del JBeam y ``<Lua>:<línea>`` si es el valor por
        defecto. ``None`` si el modo no es ``lsd`` (``diffType`` literal; una lista de
        modos no se interpreta aquí)."""
        if self.get("kind") != "lsd":
            return None
        if self.has(name):
            return self.get(name), False, ""
        if name == "rev_lock_coef":
            lock = self.lsd_effective("lock_coef")
            return lock[0], True, f"{DIFF_LUA}:{LSD_REV_LOCK_LINE}"
        value, line = LSD_LUA_DEFAULTS[name]
        return value, True, f"{DIFF_LUA}:{line}"


@dataclass(frozen=True)
class AxleDrive:
    """Diferencial de un eje motriz."""

    role: str  # front | rear
    diff: DiffSetup
    wheels: tuple[str, ...]  # ruedas motrices (connectedWheel) de ese eje
    final: float  # gearRatio del diferencial del eje
    torque_share: float | None  # fracción del par del central; None sin central
    center_output: int | None  # salida del central (1 o 2); None sin central


@dataclass(frozen=True)
class CenterDrive:
    """Diferencial central (tracción total)."""

    diff: DiffSetup
    split_a: float  # fracción de la salida 1
    split_source: str


@dataclass(frozen=True)
class TractionLayout:
    layout: str  # FWD | RWD | AWD
    driven_wheels: tuple[str, ...]
    front_wheels: frozenset[str]
    rear_wheels: frozenset[str]
    driven_radius: float  # m, radio de nodos de pressureWheels (hipótesis sin carga)
    axles: dict[str, AxleDrive] = field(default_factory=dict)  # solo ejes motrices
    center: CenterDrive | None = None
    # G2.8: tracción total SIN central reconocido: diferenciales del camino motriz
    # cuyo papel (eje o central) no se ha interpretado; se conservan en bruto
    unassigned_diffs: tuple[DiffSetup, ...] = ()

    @property
    def driven_axle(self) -> str | None:
        """Eje motriz ÚNICO (``front``/``rear``): sin diferencial central y con
        exactamente un eje con papel conocido (FWD, RWD; también un diferencial
        ``torqueVectoring`` de un eje). ``None`` en AWD (con o sin central reconocido)
        y cuando no hay diferencial en el eje motriz: no se supone ninguno."""
        if self.center is not None or len(self.axles) != 1:
            return None
        return next(iter(self.axles))

    @property
    def front_torque_share(self) -> float | None:
        """Fracción del par al eje delantero; ``None`` sin diferencial central."""
        if self.center is None or "front" not in self.axles:
            return None
        return self.axles["front"].torque_share

    def awd_summary(self) -> dict[str, Any] | None:
        """Resumen de la tracción total con el formato histórico de
        ``src.ac.drivetrain.awd_axles`` (compatibilidad)."""
        if self.center is None:
            return None
        out: dict[str, Any] = {
            "center": self.center.diff.device,
            "split_a": self.center.split_a,
            "split_source": self.center.split_source,
            "center_params": dict(self.center.diff.params),
        }
        for axle in sorted(self.axles.values(), key=lambda a: a.center_output or 0):
            out[axle.role] = {
                "differential": axle.diff.device,
                "center_output": axle.center_output,
                "torque_share": axle.torque_share,
                "final": axle.final,
                "params": dict(axle.diff.params),
            }
        out["front_share_percent"] = 100.0 * out["front"]["torque_share"]
        return out


def axle_wheel_sets(geometry: Any) -> tuple[frozenset[str], frozenset[str]]:
    """Ruedas del eje delantero y del trasero (exactamente 2 ejes, fase 2)."""
    if len(geometry.axles) != 2:
        raise DrivetrainDataError("tracción: se necesitan exactamente 2 ejes (fase 2)")
    return frozenset(geometry.axles[0].wheels), frozenset(geometry.axles[1].wheels)


# Neumáticos escalonados (agus_1_nrt_1: 0,320 delante y 0,325 detrás, 1,6 %): el radio
# solo se usa para estimar velocidades por marcha (informes; nada de la física de AC
# depende de él: cada rueda lleva su radio en tyres.ini), así que se toma la media
# si la diferencia relativa es pequeña; mayor diferencia sigue rechazándose.
DRIVEN_RADIUS_SPREAD_MAX = 0.05


def driven_wheel_radius(dt: Drivetrain, geometry: Any) -> float:
    radii = {w.radius for w in geometry.wheels if w.name in dt.driven_wheels}
    if len(radii) == 1:
        return radii.pop()
    lo, hi = min(radii, default=0.0), max(radii, default=0.0)
    if (
        not all(math.isfinite(r) for r in radii)
        or lo <= 0
        or (hi - lo) / hi > DRIVEN_RADIUS_SPREAD_MAX
    ):
        raise DrivetrainDataError(f"ruedas motrices con radios distintos: {radii}")
    return sum(radii) / len(radii)


def awd_axle_roles(
    dt: Drivetrain, front: frozenset[str] | set[str], rear: frozenset[str] | set[str]
) -> tuple[CenterDrive, dict[str, AxleDrive]] | None:
    """Eje delantero y trasero de cada diferencial bajo el central, con el reparto de
    BeamNG. ``None`` si la transmisión no tiene diferencial central reconocido."""
    if dt.awd is None:
        return None
    center = CenterDrive(
        DiffSetup.from_device(dt.awd.center), dt.awd.split_a, dt.awd.split_source
    )
    axles: dict[str, AxleDrive] = {}
    for axle in dt.awd.axles:
        if set(axle.wheels) <= set(front):
            role = "front"
        elif set(axle.wheels) <= set(rear):
            role = "rear"
        else:
            raise DrivetrainDataError(
                f"diferencial '{axle.differential.name}' con ruedas {axle.wheels} de "
                "ejes distintos"
            )
        if role in axles:
            raise DrivetrainDataError(f"dos diferenciales en el eje {role}")
        axles[role] = AxleDrive(
            role,
            DiffSetup.from_device(axle.differential),
            tuple(axle.wheels),
            axle.final,
            axle.torque_share,
            axle.output,
        )
    return center, axles


def classify_traction(dt: Drivetrain, geometry: Any) -> TractionLayout:
    """Tracción, radio de la rueda motriz y papel de cada diferencial.

    Errores (``DrivetrainDataError``), en este orden, como antes: ejes ≠ 2, rueda
    motriz sin eje, radios distintos, estructura de tracción total no reconocible."""
    front, rear = axle_wheel_sets(geometry)
    layout = traction_type(dt.driven_wheels, set(front), set(rear))
    radius = driven_wheel_radius(dt, geometry)
    roles = awd_axle_roles(dt, front, rear)
    if roles is not None:
        center, axles = roles
        return TractionLayout(
            layout, tuple(dt.driven_wheels), front, rear, radius, axles, center
        )
    axles = {}
    if layout == "AWD" and dt.differential is not None:
        return TractionLayout(
            layout,
            tuple(dt.driven_wheels),
            front,
            rear,
            radius,
            axles,
            unassigned_diffs=(DiffSetup.from_device(dt.differential),),
        )
    role = {"FWD": "front", "RWD": "rear"}.get(layout)
    if role is not None and dt.differential is not None:
        wheels = front if role == "front" else rear
        axles[role] = AxleDrive(
            role,
            DiffSetup.from_device(dt.differential),
            tuple(w for w in dt.driven_wheels if w in wheels),
            dt.final_drive,
            None,
            None,
        )
    return TractionLayout(layout, tuple(dt.driven_wheels), front, rear, radius, axles)


@dataclass(frozen=True)
class SourceDrivetrain:
    """Transmisión del origen sin el modelo de motor (G2.7, G2.6d)."""

    drivetrain: Drivetrain
    layout: TractionLayout
    shift_controller_raw: dict[str, Any]
    # G2.11: parámetros de una caja DCT o automática con convertidor
    # (src.extract.automatic_transmission.AutomaticTransmission); None en el resto
    transmission: Any = None


def analyse_source_drivetrain(
    resolved: ResolvedConfig, geometry: Any, dt: Drivetrain | None = None
) -> SourceDrivetrain:
    """Grafo, tracción y ``vehicleController`` en bruto; sin motor ni destino.

    Primero el grafo (``build_drivetrain``, o ``dt`` si ya se construyó) y después la
    tracción (``classify_traction``): los mismos errores, en el mismo orden, que antes
    dentro de ``src.ac.drivetrain.analyse``.
    """
    dt = dt if dt is not None else build_drivetrain(resolved)
    layout = classify_traction(dt, geometry)
    controller = merge_section(resolved, "vehicleController")
    shift_raw = {
        k: v for k, v in controller.values.items() if k in SHIFT_CONTROLLER_KEYS
    }
    transmission = read_transmission(dt, controller.values)
    return SourceDrivetrain(dt, layout, shift_raw, transmission)
