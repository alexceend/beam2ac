"""G2.39: inercia de caja y LSD 2WD ×2 para el 38022 (decisión del usuario D-INV-GBI / D-G227-IBIZA).

Resuelve con el motor real; no toca otros exportadores ni exige AC.
"""

from __future__ import annotations

import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "catalog" / "rules"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
HYACINTH = ROOT / "samples_beamng/thershow_hyacinth_sage/vehicles/hyacinth_sage"
ATLANTA = next((ROOT / "samples_beamng/2020_rpm_atlanta_gt_3_6t").glob("vehicles/*"))
INERTIA = "ac:drivetrain.ini/GEARBOX/INERTIA"
DIFF = "ac:drivetrain.ini/DIFFERENTIAL/"
NEW = (
    "dt.gearbox_inertia_38022@1",
    "dt.diff_preload_2wd_lsd_38022@1",
    "dt.diff_power_2wd_lsd_38022@1",
    "dt.diff_coast_2wd_lsd_38022@1",
)


@lru_cache(maxsize=None)
def resolved(vehicle_dir: Path):
    from src.attrs.report import resolve_vehicle
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    pc = (
        "35b84.pc" if vehicle_dir == IBIZA else sorted(vehicle_dir.glob("*.pc"))[0].name
    )
    pv = preview_physics(load_vehicle_dir(vehicle_dir, pc), 0.0)
    _, results = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", 0.0)
    return {r.target: r for r in results}


class TestRules(unittest.TestCase):
    def test_new_rules_limited_to_38022_and_provisional(self):
        for key in NEW:
            d = json.loads((RULES / f"{key}.json").read_text("utf-8"))
            self.assertEqual(d["status"], "provisional", key)
            self.assertEqual(d["supersedes"], "", key)
            v = [
                c for c in d["conditions"] if c["fact"] == "ir.formats.exporter_version"
            ]
            self.assertEqual([c["value"] for c in v], [["38022"]], key)

    def test_lock_coef_bound_kept(self):
        for k in ("power", "coast"):
            d = json.loads(
                (RULES / f"dt.diff_{k}_2wd_lsd_38022@1.json").read_text("utf-8")
            )
            self.assertTrue(
                any(c["op"] == "le" and c["value"] == 1.0 for c in d["conditions"])
            )


class TestResolution(unittest.TestCase):
    def test_ibiza_reproduces_ib2_variant(self):
        r = resolved(IBIZA)
        self.assertEqual(r[INERTIA].value, 0.01)
        self.assertEqual(r[INERTIA].rule.split("@")[0], "dt.gearbox_inertia_38022")
        # lsd del origen 100 / 0,25 / 0,025  ->  200 / 0,5 / 0,05 (ib2, 32/32 en AC)
        self.assertEqual(r[DIFF + "PRELOAD"].value, 200.0)
        self.assertAlmostEqual(r[DIFF + "POWER"].value, 0.5)
        self.assertAlmostEqual(r[DIFF + "COAST"].value, 0.05)

    def test_other_exporters_keep_their_own_rules(self):
        h = resolved(HYACINTH)[INERTIA]
        self.assertEqual(h.rule, "dt.gearbox_inertia_40340@1")
        a = resolved(ATLANTA)[INERTIA]
        self.assertEqual(a.rule, "dt.gearbox_inertia_200409@1")


if __name__ == "__main__":
    unittest.main()
