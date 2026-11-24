"""Estimador de neumático sin banco (``src/ac/tyre_estimate.py``, 2026-11-16)."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from src.ac import tyre_estimate as TE
from src.attrs import transforms

ROOT = Path(__file__).resolve().parents[1]
BISEXUAL_FRONT = {
    "frictionCoef": 1.0,
    "noLoadCoef": 1.549854,
    "fullLoadCoef": 0.1365,
    "loadSensitivitySlope": 0.000303,
    "wheelReinfBeamSpring": 16722.357422,
    "wheelSideTransitionZone": 0.106054,
    "wheelTreadReinfBeamSpring": 167451.265625,
    "wheelPeripheryBeamSpring": 101970.34375,
    "wheelTreadBeamSpring": 1100000.0,
    "tireWidth": 0.205,
}


class TestEstimator(unittest.TestCase):
    def test_estimate_within_limits_and_complete(self) -> None:
        e = TE.estimate_axle(BISEXUAL_FRONT, 4988.0, 0.39, 0.254, 43.7, 0.205, 29.9)
        for name, (lo, hi) in TE.LIMITS.items():
            self.assertTrue(lo <= e["sctm"][name] <= hi, name)
        self.assertAlmostEqual(e["free_rolling_radius"], 0.39, delta=0.01)
        self.assertGreater(e["rotational_inertia"], 0)
        self.assertEqual(len(e["rolling_resistance"]), 3)
        self.assertEqual(e["warnings"], [])

    def test_bisexual_front_close_to_the_bench_measurement(self) -> None:
        # medido en el banco (exports/003): DX_REF 0,8405, DY_REF 0,8121
        s = TE.estimate_sctm(BISEXUAL_FRONT, 4988.0)
        self.assertAlmostEqual(s["dy_ref"], 0.8121, delta=0.15)
        self.assertAlmostEqual(s["dx_ref"], 0.8405, delta=0.15)

    def test_missing_structure_warns_and_uses_median(self) -> None:
        w = {k: v for k, v in BISEXUAL_FRONT.items() if not k.startswith("wheel")}
        e = TE.estimate_axle(w, 4988.0, 0.39, 0.254, 43.7, 0.205, 29.9)
        self.assertTrue(any("mediana" in x for x in e["warnings"]))
        for name, (lo, hi) in TE.LIMITS.items():
            self.assertTrue(lo <= e["sctm"][name] <= hi, name)

    def test_vertical_stiffness_uses_contact_patch_physics(self) -> None:
        e = TE.estimate_axle(BISEXUAL_FRONT, 4988.0, 0.39, 0.254, 43.7, 0.205, 29.9)
        k = 4 * 0.39 * (43.7 * 6894.76) ** 2 * 0.205**2 / 4988.0
        self.assertAlmostEqual(
            e["vertical_stiffness"] / k,
            TE.coefficients()["aux"]["vertical_stiffness"]["scale"],
        )
        # más presión ⇒ más rigidez (∝ p²)
        e2 = TE.estimate_axle(
            BISEXUAL_FRONT, 4988.0, 0.39, 0.254, 2 * 43.7, 0.205, 29.9
        )
        self.assertAlmostEqual(e2["vertical_stiffness"] / e["vertical_stiffness"], 4.0)

    def test_limit_angle_follows_brush_model(self) -> None:
        # pico de la fuerza lateral en tan(α) = 3·μ·Fz/C_α, C_α ∝ ancho^0,83·k_banda^0,73
        soft = TE.estimate_sctm(BISEXUAL_FRONT, 4988.0)["friction_limit_angle"]
        stiff_w = dict(BISEXUAL_FRONT, wheelTreadBeamSpring=4400000.0)
        stiff = TE.estimate_sctm(stiff_w, 4988.0)["friction_limit_angle"]
        self.assertLess(stiff, soft)  # carcasa más rígida ⇒ pico a menor deriva
        f = TE.features(BISEXUAL_FRONT, 4988.0, 0.4)
        self.assertGreater(f["fla0"], 0.0)
        self.assertLess(f["fla0"], 45.0)

    def test_out_of_range_warns(self) -> None:
        wild = dict(BISEXUAL_FRONT, noLoadCoef=3.5, fullLoadCoef=1.0)
        w = TE.range_warnings(wild, 4988.0)
        self.assertTrue(any("noLoadCoef" in x for x in w))
        self.assertTrue(any("familia" in x for x in w))

    def test_more_grip_coefficient_means_more_grip(self) -> None:
        a = TE.estimate_sctm(BISEXUAL_FRONT, 4988.0)["dy_ref"]
        b = TE.estimate_sctm(dict(BISEXUAL_FRONT, noLoadCoef=1.9), 4988.0)["dy_ref"]
        self.assertGreater(b, a)

    def test_frozen_coefficients_reference_existing_records(self) -> None:
        cal = TE.coefficients()
        self.assertGreaterEqual(len(cal["records"]), 15)
        for rec, sha in cal["records"]:
            path = ROOT / "measurements/beamng_bench/tyre" / f"{rec}.json"
            if (
                path.exists()
            ):  # medidas ausentes (pruebas con carpeta vacía): no se exige
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), sha, rec
                )
        self.assertIn("car", cal["validation"])
        self.assertIn("family", cal["validation"])


class TestTransformEstimated(unittest.TestCase):
    def test_returns_estimated_value_without_fitting(self) -> None:
        data = {"estimated_sctm": {"dy_ref": 0.8749, "ls_expy": 0.49}, "rows": []}
        out = transforms.t_ac_sctm_fit(
            {"samples": (data, "dataset"), "fz0": (4988.0, "N")},
            {"output": "DY_REF"},
            "1",
        )
        self.assertAlmostEqual(out.value, 0.8749)
        self.assertIn("ESTIMADO", out.assumptions[0])
        self.assertTrue(json.dumps(out.assumptions))


if __name__ == "__main__":
    unittest.main()
