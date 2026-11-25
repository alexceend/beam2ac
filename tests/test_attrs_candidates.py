"""Candidatos de la fase 5 (DWB, masas, frenos, embrague): Ibiza y spicy_mandala."""

import unittest
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs.candidates import build_candidates, mass_checks
from src.attrs.candidates_dwb import (
    StructureError,
    dwb_candidates,
    measure_dwb,
    symmetry_error,
)
from src.attrs.report import context_from_preview, resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
SPICY = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods/spicy_mandala.zip"
COMMON = Path((default_beamng_root() / "content/vehicles/common.zip").as_posix())
_C: dict = {}


def pv_ibiza():
    if "ib" not in _C:
        _C["ib"] = preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"))
    return _C["ib"]


def pv_spicy():
    if "sp" not in _C:
        _C["sp"] = preview_physics(load_vehicle_zip(SPICY, COMMON))
    return _C["sp"]


def cands(pv):
    cat = Catalog.load(REPO_CATALOG)
    return build_candidates(pv, context_from_preview(pv, cat, 70.0).facts)


@unittest.skipUnless(IBIZA.is_dir(), "muestra del Ibiza no disponible")
class TestIbizaCandidates(unittest.TestCase):
    def test_not_a_double_wishbone(self):
        c = dwb_candidates(pv_ibiza().resolved)
        self.assertIn("error", c["FRONT"])  # MacPherson: un solo triángulo
        self.assertIn("error", c["REAR"])  # eje torsional
        with self.assertRaises(StructureError):
            measure_dwb(pv_ibiza().resolved, "FR", "FRONT")

    def test_brake_interpretation_reproduces_approved_diag_value(self):
        mt = {
            round(x["value"], 1)
            for x in cands(pv_ibiza())["candidates"]["ac:brakes.ini/DATA/MAX_TORQUE"]
        }
        self.assertIn(2536.2, mt)  # base: 2173,55 + 362,68
        hb = {
            x["value"]
            for x in cands(pv_ibiza())["candidates"][
                "ac:brakes.ini/DATA/HANDBRAKE_TORQUE"
            ]
        }
        self.assertIn(1208.9, hb)

    def test_clutch_rule_reproduces_base_value(self):
        cat = Catalog.load(REPO_CATALOG)
        ctx, res = resolve_vehicle(pv_ibiza(), cat)
        self.assertAlmostEqual(
            ctx.facts["bng.clutch.lock_torque_default"].value, 494.3, delta=0.05
        )
        r = {x.target: x for x in res}["ac:drivetrain.ini/CLUTCH/MAX_TORQUE"]
        self.assertEqual((r.source, r.value), ("decision", 494.3))
        self.assertIn("coincide con la decisión", r.alternatives[0]["note"])

    def test_mass_checks_are_computed_per_vehicle(self):
        checks = mass_checks(pv_ibiza())
        self.assertEqual((checks["node_rows"], checks["unique_node_ids"]), (437, 437))
        self.assertEqual(checks["wheel_mass_options_used"], {})
        gap = cands(pv_ibiza())["gaps"]["mass_difference"]
        self.assertTrue(gap["compatible_with_rounding"])


@unittest.skipUnless(
    SPICY.is_file() and COMMON.is_file(), "spicy_mandala o common.zip no instalados"
)
class TestSpicyCandidates(unittest.TestCase):
    def test_front_double_wishbone_measured_structurally(self):
        c = measure_dwb(pv_spicy().resolved, "FR", "FRONT")
        self.assertEqual(
            c.nodes,
            {
                "WBCAR_TOP_FRONT": "fx3r",
                "WBCAR_TOP_REAR": "fx4r",
                "WBCAR_BOTTOM_FRONT": "fx1r",
                "WBCAR_BOTTOM_REAR": "fx2r",
                "WBCAR_STEER": "st2r",
                "WBTYRE_TOP": "fh2r",
                "WBTYRE_BOTTOM": "fh1r",
                "WBTYRE_STEER": "fh3r",
            },
        )
        p = c.points
        self.assertGreater(p["WBCAR_TOP_FRONT"][2], 0)  # delante del centro de rueda
        self.assertLess(p["WBCAR_TOP_REAR"][2], 0)
        self.assertGreater(p["WBTYRE_TOP"][1], p["WBTYRE_BOTTOM"][1])
        for key in p:
            if key.startswith("WBCAR"):
                self.assertGreater(p[key][0], 0, key)  # +X hacia dentro (rueda derecha)
        self.assertEqual(symmetry_error(pv_spicy().resolved, "FRONT"), 0.0)

    def test_rear_needs_a_human_decision(self):
        c = dwb_candidates(pv_spicy().resolved)
        self.assertIn("dos rótulas", c["REAR"]["error"])
        self.assertIn("decisión humana", c["REAR"]["error"])

    def test_candidates_and_gaps(self):
        out = cands(pv_spicy())
        self.assertEqual(
            out["candidates"]["ac:suspensions.ini/FRONT/TYPE"][0]["value"], "DWB"
        )
        self.assertNotIn("ac:suspensions.ini/REAR/TYPE", out["candidates"])
        self.assertFalse(
            any(x["applied"] for lst in out["candidates"].values() for x in lst)
        )
        gap = out["gaps"]["mass_difference"]
        self.assertAlmostEqual(gap["difference_kg"], 3.19, places=2)
        self.assertFalse(gap["compatible_with_rounding"])
        self.assertEqual(gap["checks"]["unique_node_ids"], 458)
        aero = out["candidates"]["ac:aero.ini/WING_0/NAME"]
        self.assertTrue(all("sustentación" in x["method"] for x in aero))

    def test_clutch_proposal(self):
        cat = Catalog.load(REPO_CATALOG)
        ctx, res = resolve_vehicle(pv_spicy(), cat)
        r = {x.target: x for x in res}["ac:drivetrain.ini/CLUTCH/MAX_TORQUE"]
        self.assertEqual(
            # G2.44: la regla clonada del 40340 (dt.clutch_max_torque_38022) lo resuelve
            (r.source, r.status, r.requires_approval),
            ("rule", "inferred", False),
        )
        self.assertAlmostEqual(
            r.value,
            1.25 * 113.1 + 7500 * 0.097981451090418 * 3.141592653589793 / 30,
            places=2,
        )


if __name__ == "__main__":
    unittest.main()
