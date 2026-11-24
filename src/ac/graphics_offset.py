"""``car.ini [BASIC] GRAPHICS_OFFSET``: colocación del KN5 respecto al cuerpo físico.

Convención de AC (G2.24, docs/research/g224_diagnostico_exports_ac_2026-11-09.md §2):

* El cuerpo físico tiene su origen en el CdG. La base de cada rueda en ese marco es
  ``(±TRACK/2, BASEY, (1 − CG_LOCATION)·WHEELBASE)`` delante y
  ``(±TRACK/2, BASEY, −CG_LOCATION·WHEELBASE)`` detrás (suspensión de AC [C],
  ac_suspension_coordinates.md §2.7; mismo criterio que ``export.ZERO_IS_WRONG_KEYS``).
* El modelo se dibuja en ``punto_KN5 + GRAPHICS_OFFSET`` (con
  ``GRAPHICS_PITCH_ROTATION = 0``; ``src/ac/cameras.py``: KN5 = cuerpo − OFFSET).
* Las ruedas se dibujan en la posición de la física: el nulo ``WHEEL_xx`` del KN5 más
  el OFFSET debe coincidir con la base de su rueda. Si no, la carrocería se ve
  desplazada respecto a las ruedas (prueba del usuario de los exports G2.23: carrocería
  flotando sobre las ruedas, sin GRAPHICS_OFFSET ⇒ AC usa 0).

Comprobado con el paquete del Ibiza que funcionó en el juego (``pkg_v18bu`` y
``exports/004``): ``WHEEL_xx + (0, −0,4619, −0,0914)`` = base física de las 4 ruedas con
una diferencia ≤ 0,1 mm (``output/g224_2026-11-09/diagnostico/ruedas_kn5.py``).

Derivación: con un KN5 cuyo modelo y física salen de la misma geometría (nodos y
flexbodies del mismo JBeam), la diferencia ``base_física − WHEEL_xx`` es la MISMA
traslación para las 4 ruedas (= −CdG del cuerpo en el marco del KN5, la fórmula de
``tools/analysis/derive_physics_completion.py`` para el Ibiza). Se calcula para cada
rueda y se exige que las 4 coincidan (residuo ≤ ``RIGID_TOLERANCE_M``); si no coinciden,
no hay traslación que coloque el modelo y se da error: nunca se promedia una
incoherencia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from src.ac.car_validate import ACIni, ac_get_float

Vec3 = tuple[float, float, float]

# Residuo admitido entre las 4 ruedas (m). El Ibiza aprobado da 0,0001 m; la tolerancia
# de posición de la fase 6 (export.WHEEL_TOLERANCE_M) es 0,01 m. 5 mm deja margen al
# redondeo de las claves (TRACK a µm, BASEY y CG_LOCATION con 4-12 decimales) sin
# admitir un desplazamiento visible.
RIGID_TOLERANCE_M = 0.005
WHEELS = (
    ("WHEEL_LF", "FRONT", 1.0),
    ("WHEEL_RF", "FRONT", -1.0),
    ("WHEEL_LR", "REAR", 1.0),
    ("WHEEL_RR", "REAR", -1.0),
)


class GraphicsOffsetError(ValueError):
    """No se puede derivar o verificar GRAPHICS_OFFSET."""


@dataclass(frozen=True)
class OffsetFit:
    offset: Vec3  # media de las 4 traslaciones (m, ejes AC)
    per_wheel: dict[str, Vec3]
    residual: float  # máx. distancia de una traslación a la media (m)


def physical_wheel_bases(susp: ACIni) -> dict[str, Vec3]:
    """Base de cada rueda en el marco del cuerpo físico (origen = CdG), leída como
    AC."""
    need = [("BASIC", "WHEELBASE"), ("BASIC", "CG_LOCATION")] + [
        (a, k) for a in ("FRONT", "REAR") for k in ("TRACK", "BASEY")
    ]
    vals = {}
    for sec, key in need:
        got = ac_get_float(susp, sec, key)
        if got.raw is None or got.value is None or not math.isfinite(got.value):
            raise GraphicsOffsetError(
                f"suspensions.ini [{sec}] {key} ausente o inválido"
            )
        vals[(sec, key)] = got.value
    wb, cg = vals[("BASIC", "WHEELBASE")], vals[("BASIC", "CG_LOCATION")]
    out = {}
    for wheel, axle, side in WHEELS:
        z = (1.0 - cg) * wb if axle == "FRONT" else -cg * wb
        out[wheel] = (side * vals[(axle, "TRACK")] / 2, vals[(axle, "BASEY")], z)
    return out


def kn5_wheel_nulls(kn5: Path) -> dict[str, Vec3]:
    from src.ac.kn5_inspect import read_kn5

    found = [
        n
        for n in read_kn5(kn5, keep_vertices=False).nodes
        if n.name in {w for w, _a, _s in WHEELS}
    ]
    pos = {n.name: n.translation for n in found if n.type == 1}
    if len(found) != 4 or len(pos) != 4:
        raise GraphicsOffsetError(f"nulos de rueda del KN5: {sorted(pos)}")
    return pos


def fit_offset(nulls: dict[str, Vec3], bases: dict[str, Vec3]) -> OffsetFit:
    per = {
        w: tuple(bases[w][i] - nulls[w][i] for i in range(3)) for w, _a, _s in WHEELS
    }
    mean = tuple(sum(per[w][i] for w in per) / 4 for i in range(3))
    residual = max(math.dist(per[w], mean) for w in per)
    return OffsetFit(mean, per, residual)  # type: ignore[arg-type]


def derive(kn5: Path, susp: ACIni) -> OffsetFit:
    """Traslación rígida del KN5 al cuerpo físico; error si las 4 ruedas no
    coinciden."""
    fit = fit_offset(kn5_wheel_nulls(kn5), physical_wheel_bases(susp))
    if fit.residual > RIGID_TOLERANCE_M:
        raise GraphicsOffsetError(
            f"las 4 ruedas piden traslaciones distintas "
            f"(residuo {fit.residual:.4f} m > "
            f"{RIGID_TOLERANCE_M} m): el KN5 y suspensions.ini no son coherentes"
        )
    return fit


def check(kn5: Path, susp: ACIni, offset: Vec3) -> float:
    """Distancia máxima (m) entre ``WHEEL_xx + offset`` y la base física de cada
    rueda."""
    nulls, bases = kn5_wheel_nulls(kn5), physical_wheel_bases(susp)
    return max(
        math.dist(tuple(nulls[w][i] + offset[i] for i in range(3)), bases[w])
        for w, _a, _s in WHEELS
    )


def render(offset: Vec3) -> str:
    """Valor tal como se escribe (4 decimales, 0,1 mm, como el del Ibiza)."""
    return ", ".join(f"{(round(c, 4) + 0.0):g}" for c in offset)
