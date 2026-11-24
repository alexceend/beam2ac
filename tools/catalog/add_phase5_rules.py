"""Añade al catálogo las reglas nuevas de la fase 5 (2026-10-26). Se niega a sobrescribir.

    python tools/catalog/add_phase5_rules.py

* ``dt.clutch_max_torque@1``: ``[CLUTCH] MAX_TORQUE`` = ``lockTorque`` por defecto de BeamNG
  (``frictionClutch.lua:174``). Ámbito: vehículo Ibiza, estado provisional. Reproduce el valor
  DIAG de su base (494,3; ``ensamblado.md`` §9.13). Supuesto declarado: equivalencia
  ``lockTorque`` ↔ ``MAX_TORQUE`` (los INI de Kunos no comentan la clave).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-10-26"


def R(t: str, p: str, loc: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=t, path=p, locator=loc, note=note)


def rules() -> list[S.Rule]:
    clutch = S.Rule(
        id="dt.clutch_max_torque", version=1, target="ac:drivetrain.ini/CLUTCH/MAX_TORQUE", kind="computed",
        transform=S.Transform(id="identity", output_unit="N·m", input_units={"x": "N·m"},
                              formula="MAX_TORQUE = 1,25·par neto máx. + maxRPM·inercia·π/30"),
        scope=S.Scope(level="vehicle", vehicle="seat_ibiza_acfr"), status="provisional",
        inputs={"x": "bng.clutch.lock_torque_default"},
        conditions=[S.Condition(fact="bng.clutch.lock_torque", op="absent"),
                    S.Condition(fact="bng.engine.device_type", op="eq", value="camsoEngine")],
        assumptions=["lockTorque de BeamNG ≈ par máximo transmisible del embrague de AC (MAX_TORQUE): "
                     "la clave no está comentada en los INI de Kunos"],
        source_doc=[R("code", "lua/vehicle/powertrain/frictionClutch.lua", "174"),
                    R("code", "sample_beamng/vehicles/seat_ibiza_acfr/lua/powertrain/camsoEngine.lua",
                      "64-74, 252, 1997, 2007")],
        target_doc=[R("file", "sample_assetto_unpacked/ks_toyota_ae86/data/drivetrain.ini", "[CLUTCH] MAX_TORQUE")],
        evidence=[R("doc", "docs/research/ensamblado.md", "§9.13: 494,3 N·m (fórmula de frictionClutch.lua)"),
                  R("doc", "docs/research/casos_reales_2026-10-26.md", "embrague")],
        notes="fase 5: reproduce el valor DIAG de la base del Ibiza",
        history=[S.HistoryEntry(date=DATE, author="claude (fase 5)", action="create", status_to="provisional",
                                reason="fórmula de frictionClutch.lua:174 verificada contra la base del Ibiza")],
    )
    return [clutch]


def main() -> int:
    for r in rules():
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
