"""Capacidad del embrague de la caja de doble embrague del 200409 (G2.16, D-G211-CL).

    python tools/catalog/add_dct_clutch_200409_rule.py [--dry-run]

Encargada por el usuario en G2.16. Solo añade (reglas inmutables); no toca
``dt.clutch_max_torque@2`` (Ibiza, ``frictionClutch``).

* BeamNG ``dctGearbox.lua`` (0.38.6) [C]: cada embrague transmite
  ``clamp(ángulo·lockSpring + lockDamp·Δω·clutchRatio, ±lockTorque)`` (l. 73-75) con
  ``lockTorque`` = declarado o ``maxTorque·1,25 + maxRPM·inercia·π/30`` (l. 180): la
  MISMA forma y el mismo valor por defecto que ``frictionClutch.lua`` (l. 104, 174), sin
  eficiencia térmica.
* AC el cálculo 2WD de AC [C] (``docs/research/frenos_diferencial_transmision_2026-10-26``
  §2.4): con el embrague abierto transmite ``Δω/(|Δω|+4)·embrague·MAX_TORQUE``; cerrado,
  rígido; ``MAX_TORQUE`` = 0 ⇒ 450 N·m (G2.11).
* Equivalencia: capacidad máxima del embrague (misma magnitud, N·m); la curva de
  deslizamiento es distinta (AC: 50 % de MAX_TORQUE a 4 rad/s). Es el precedente del
  Ibiza (``dt.clutch_max_torque@2``, base aprobada y conducida en AC). Sin la clave, AC
  usaría 450 N·m, por debajo del par de los 4 motores DCT (G2.11 §7).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-04"
AUTHOR = "claude (G2.16: decisiones de transmisión, encargada por el usuario)"
LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain"


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


def build() -> S.Rule:
    return S.Rule(
        id="dt.clutch_max_torque_dct_200409",
        version=1,
        target="ac:drivetrain.ini/CLUTCH/MAX_TORQUE",
        kind="computed",
        transform=S.Transform(
            id="identity",
            params={},
            input_units={"x": "N·m"},
            output_unit="N·m",
            formula="MAX_TORQUE = lockTorque por defecto de dctGearbox "
            "(1,25·par máx. + maxRPM·inercia·π/30)",
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs={"x": "ir.drivetrain.clutch.lock_torque_default"},
        conditions=[
            S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
            S.Condition(fact="ir.engine.device_type", op="eq", value="combustionEngine"),
            S.Condition(fact="ir.drivetrain.gearbox.kind", op="eq", value="dctGearbox"),
            S.Condition(fact="ir.drivetrain.clutch.lock_torque", op="absent", value=None),
        ],
        assumptions=[
            "capacidad del embrague: BeamNG limita el par de cada embrague de la DCT a "
            "±lockTorque (dctGearbox.lua l. 73-75); AC usa MAX_TORQUE solo con el embrague "
            "abierto, con la curva Δω/(|Δω|+4) (50 % a 4 rad/s), y es rígido cerrado: la "
            "salida y el deslizamiento no se reproducen, la capacidad sí",
            "dos embragues alternos en BeamNG, uno en AC: en régimen solo transmite uno",
            "mismo criterio que dt.clutch_max_torque@2 (Ibiza, frictionClutch; base "
            "aprobada en AC)",
            "ningún vehículo 200409 se ha probado en AC",
        ],
        open_issues=[],
        source_doc=[
            ev("code", f"{LUA}/dctGearbox.lua", "l. 73-75 (par), l. 180 (por defecto)"),
            ev("code", f"{LUA}/frictionClutch.lua", "l. 104, 174 (misma forma)"),
            ev("code", "src/extract/automatic_transmission.py", "read_dct"),
        ],
        target_doc=[
            ev(
                "doc",
                "docs/research/frenos_diferencial_transmision_2026-10-26/informe.md",
                "§2.4 el cálculo 2WD de AC (embrague)",
            ),
            ev("doc", "docs/research/g211_transmissions_2026-11-02.md", "§3, §7"),
        ],
        evidence=[ev("report", "docs/research/g216_suspension_steering_2026-11-04.md")],
        validations=[],
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason="G2.16 (D-G211-CL): capacidad del embrague de la DCT",
                status_from="",
                status_to="provisional",
            )
        ],
        notes="G2.16: solo 200409, combustionEngine, dctGearbox y lockTorque no declarado",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    r = build()
    S.from_dict(S.to_dict(r))
    if args.dry_run:
        print("ok (sin escribir):", r.key)
        return 0
    print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
