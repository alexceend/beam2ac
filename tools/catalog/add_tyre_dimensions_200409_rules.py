"""Dimensiones del neumático del 200409 (G2.19, 2026-11-06).

    python tools/catalog/add_tyre_dimensions_200409_rules.py [--dry-run]

Encargada por el usuario en G2.19 (desbloqueo integral del 200409). Solo añade; nunca
sobrescribe. Los exportadores 38022/40340 no reciben nada (lista cerrada ``["200409"]``).

Reglas (``provisional``), una por eje:

* ``tyre.{front,rear}.radius_200409@1``          → ``[FRONT|REAR] RADIUS``
  (``radius`` de ``pressureWheels``: radio de los nodos del neumático sin carga);
* ``tyre.{front,rear}.rim_radius_200409@1``      → ``RIM_RADIUS`` (``hubRadius``);
* ``tyre.{front,rear}.width_200409@1``           → ``WIDTH`` (``tireWidth``);
* ``tyre.{front,rear}.pressure_static_200409@1`` → ``PRESSURE_STATIC`` (``pressurePSI``).

Misma semántica que las reglas del Ibiza (``tyre.*@2``, ámbito de vehículo, usadas en la
base que se condujo en AC). El 200409 escribe el MISMO bloque ``pressureWheels`` (mismas
claves, comentarios del exportador incluidos; ``wheels_front.jbeam``/``wheels_rear.jbeam``)
y el lector es el mismo (``src/extract/brakes_tyres.py``). La presión se resuelve con la
variable ``$frontPSI``/``$rearPSI`` de la configuración.

NO resuelve el modelo de agarre (``DX0``, ``DY0``, ``LS_EXP*``, ``FZ0``, …): en BeamNG el
rozamiento de los nodos del neumático lo calcula el C++ del juego (``stage2.lua`` pasa
``frictionCoef``, ``noLoadCoef``, ``fullLoadCoef``, ``loadSensitivitySlope``,
``softnessCoef``, ``stribeck*`` a ``obj:setNode``) y la rigidez del neumático sale de las
vigas generadas; sin medir Fx/Fy frente al deslizamiento en el juego no hay equivalencia
(informe G2.19 §3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-06"
AUTHOR = "claude (G2.19: desbloqueo integral del 200409, encargada por el usuario)"
DOC = "docs/research/g219_desbloqueo_integral_200409.md"
COND = [S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"])]


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


# (sufijo de la regla, clave AC, campo del IR, unidad, clase, supuestos)
FIELDS = (
    ("radius", "RADIUS", "radius", "m", "measured", ["radio de los nodos sin carga"]),
    ("rim_radius", "RIM_RADIUS", "hub_radius", "m", "direct", []),
    ("width", "WIDTH", "width", "m", "direct", []),
    ("pressure_static", "PRESSURE_STATIC", "pressure", "psi", "direct", []),
)
SOURCE_KEY = {
    "radius": "radius",
    "hub_radius": "hubRadius",
    "width": "tireWidth",
    "pressure": "pressurePSI",
}


def rule(axle: str, suffix: str, key: str, field: str, unit: str, kind: str, assum):
    section = axle.upper()
    return S.Rule(
        id=f"tyre.{axle}.{suffix}_200409",
        version=1,
        target=f"ac:tyres.ini/{section}/{key}",
        kind=kind,
        transform=S.Transform(
            id="identity",
            params={},
            input_units={"x": unit},
            output_unit=unit,
            formula="",
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs={"x": f"ir.tyres.axles.{axle}.{field}"},
        conditions=list(COND),
        assumptions=list(assum)
        + [
            "mismo bloque pressureWheels y misma semántica que la regla del Ibiza "
            f"(tyre.{axle}.{suffix}@2), usada en la base conducida en AC; ningún 200409 "
            "se ha probado en AC"
        ],
        open_issues=[],
        source_doc=[
            ev("code", "src/extract/brakes_tyres.py", "WheelTyre"),
            ev(
                "file",
                "samples_beamng/2020_rpm_legion_grand_touring/vehicles/"
                "2020_rpm_legion_grand_touring/wheels_front.jbeam",
                f"pressureWheels {SOURCE_KEY[field]} (comentarios del exportador)",
            ),
        ],
        target_doc=[
            ev("doc", "sample_assetto/data/tyres.ini", ""),
            ev("doc", "docs/research/suspension_frenos_neumaticos_aero.md", "§4"),
        ],
        evidence=[
            ev("report", DOC, "§3 neumáticos"),
            ev("report", "docs/research/suspension_frenos_neumaticos_aero.md", "§4"),
        ],
        validations=[],
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason="G2.19: dimensiones del neumático del 200409 (mismo formato "
                "pressureWheels que el Ibiza); el modelo de agarre sigue sin regla",
                status_from="",
                status_to="provisional",
            )
        ],
        notes="G2.19: solo exportador 200409; solo dimensiones y presión",
    )


def build() -> list[S.Rule]:
    return [
        rule(axle, *f) for axle in ("front", "rear") for f in FIELDS  # type: ignore[arg-type]
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
