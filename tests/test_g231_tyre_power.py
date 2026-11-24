"""G2.31: κ referido al giro libre medido (hyacinth/quail) y curva camso in-game."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
RULES = ROOT / "catalog" / "rules"
VALID = ROOT / "catalog" / "validations"

COLS = [
    ["wheel", "text"],
    ["kind", "text"],
    ["condition", "text"],
    ["n", "1"],
    ["fz", "N"],
    ["slip_angle", "deg"],
    ["slip_ratio", "1"],
    ["camber_axis", "deg"],
    ["speed", "m/s"],
    ["fx", "N"],
    ["fy", "N"],
    ["pressure", "Pa"],
]


def row(kind, cond, a, k, wheel="FL"):
    return [wheel, kind, cond, 1, 4000.0, a, k, -2.0, 20.0, 0.0, 0.0, 3e5]


class TestFreeReference(unittest.TestCase):
    def data(self):
        # giro libre medido a −5°, 0° y 5° (κ₀ = 0,016, 0, 0,001) en FL y otro en FR
        lat = [row("lat", "base", a, k) for a, k in ((-5, 0.016), (0, 0.0), (5, 0.001))]
        lat += [
            row("lat", "base", a, k, "FR")
            for a, k in ((-5, 0.001), (0, 0.0), (5, 0.016))
        ]
        return {
            "columns": COLS,
            "rows": lat
            + [row("comb", "b5", 5.0, 0.101), row("comb", "b5", 5.0, 0.101, "FR")],
        }

    def test_default_keeps_raw_slip_ratio(self):
        from src.ac.tyre_fit import points_from_dataset

        pts = points_from_dataset(self.data())
        self.assertAlmostEqual(pts[-2].slip_ratio, 0.101)

    def test_free_reference_subtracts_measured_free_rolling_slip_of_the_same_wheel(
        self,
    ):
        from src.ac.tyre_fit import points_from_dataset

        pts = points_from_dataset(self.data(), free_reference=True)
        self.assertAlmostEqual(pts[-2].slip_ratio, 0.100)  # FL a +5°: 0,001
        self.assertAlmostEqual(pts[-1].slip_ratio, 0.085)  # FR a +5°: 0,016
        # las filas lat quedan en κ' = 0 (giro libre por definición)
        self.assertTrue(all(abs(p.slip_ratio) < 1e-12 for p in pts[:6]))

    def test_interpolates_between_measured_slip_angles(self):
        from src.ac.tyre_fit import free_rolling_slip, _interp

        tab = free_rolling_slip(self.data()["rows"])[("FL", "base")]
        self.assertAlmostEqual(_interp(tab, 2.5), 0.0005)
        self.assertAlmostEqual(
            _interp(tab, 99), 0.001
        )  # fuera del barrido: último valor

    def test_missing_free_rolling_data_is_an_error_not_a_zero(self):
        from src.ac.tyre_fit import points_from_dataset

        d = {"columns": COLS, "rows": [row("comb", "b5", 5.0, 0.1)]}
        with self.assertRaises(ValueError):
            points_from_dataset(d, free_reference=True)

    def test_transform_rejects_unknown_reference(self):
        from src.attrs import transforms as T

        with self.assertRaises(T.TransformError):
            T.t_ac_sctm_fit(
                {
                    "samples": ({"columns": COLS, "rows": []}, "dataset"),
                    "fz0": (4000.0, "N"),
                },
                {"output": "DY_REF", "kappa_reference": "otra"},
                "1",
            )


class TestRules(unittest.TestCase):
    def test_26_rules_v2_supersede_v1_with_same_tolerance_and_scope(self):
        v2 = sorted(RULES.glob("tyre.*_40340@2.json"))
        self.assertEqual(len(v2), 26)
        for p in v2:
            n = json.loads(p.read_text("utf-8"))
            o = json.loads((RULES / p.name.replace("@2", "@1")).read_text("utf-8"))
            self.assertEqual(n["supersedes"], f"{o['id']}@1")
            self.assertEqual(
                n["transform"]["params"]["max_rms"], 0.06
            )  # tolerancia sin cambios
            self.assertEqual(o["transform"]["params"]["max_rms"], 0.06)
            self.assertEqual(
                n["transform"]["params"]["kappa_reference"], "free_rolling_at_alpha"
            )
            self.assertNotIn(
                "kappa_reference", o["transform"]["params"]
            )  # la @1 no se toca
            for k in ("target", "scope", "inputs", "conditions"):
                self.assertEqual(n[k], o[k], (p.name, k))
            self.assertEqual(n["status"], "provisional")


class TestEngineInGame(unittest.TestCase):
    CARS = [
        "autist_cuanto",
        "hyacinth_sage",
        "quail_mirabilis",
        "sucette_poubelle",
        "sucette_poubelle_turbo",
    ]

    def test_validations_exist_cover_power_lut_and_pass(self):
        for car in self.CARS:
            (v,) = json.loads((VALID / f"{car}.engine_in_game.json").read_text("utf-8"))
            self.assertEqual(v["method"], "in_game_value")
            self.assertEqual(v["covers"], ["ac:power.lut"])
            self.assertEqual(v["result"], "pass", car)
            self.assertLessEqual(
                v["observed"]["max_abs_torque_delta_Nm"], v["tolerance"]["torque_Nm"]
            )
            self.assertTrue(any(e["sha256"] for e in v["evidence"]), car)

    def test_hyacinth_power_lut_resolves_with_both_provenances(self):
        from src.convert.service import ConversionSession

        vd = (
            ROOT
            / "samples_beamng"
            / "thershow_hyacinth_sage"
            / "vehicles"
            / "hyacinth_sage"
        )
        s = ConversionSession.from_source(vd, None, None, 0.0)
        res = {r.target: r for r in s.resolve()}
        r = res["ac:power.lut"]
        self.assertIsNotNone(r.value)
        self.assertNotEqual(r.status, "ambiguous")
        facts = s.facts()
        hp = facts["ir.engine.peak_power"].value / 745.69987
        self.assertTrue(math.isclose(hp, 437.255, abs_tol=0.01))
        self.assertTrue(
            math.isclose(
                facts["ir.declared.implied_power"].value, 437.788, abs_tol=0.01
            )
        )  # la ficha se conserva


class TestCoastLeastSquares(unittest.TestCase):
    def test_exact_line_through_minimum_is_recovered(self):
        from src.ac.coast_ref import coast_ref_least_squares

        pts = [(r, 0.01 * (r - 1000)) for r in range(1000, 8001, 500)]
        fit = coast_ref_least_squares(pts, 8000, 1000)
        self.assertAlmostEqual(fit.torque, 70.0)
        self.assertEqual(fit.rpm, 8000)
        self.assertAlmostEqual(fit.max_abs_error, 0.0, places=9)

    def test_offset_curve_gets_lower_rms_than_the_ceiling_anchor(self):
        from src.ac.coast_ref import coast_ref_from_minimum, coast_ref_least_squares

        # freno de origen con desfase en el ralentí: 40 N·m en MINIMUM, 80 en el techo
        pts = [(r, 40 + 40 * (r - 1200) / 10800) for r in range(1200, 12001, 100)]
        a = coast_ref_from_minimum(pts, 12000, 1200)
        b = coast_ref_least_squares(pts, 12000, 1200)
        self.assertAlmostEqual(a.torque, 80.0)
        self.assertGreater(b.torque, a.torque)  # pendiente mayor: baja antes de régimen
        use = [(r, t) for r, t in pts if r > 1200]

        def rms(f):
            s = f.torque / (f.rpm - 1200)
            return (sum((s * (r - 1200) - t) ** 2 for r, t in use) / len(use)) ** 0.5

        self.assertLess(rms(b), rms(a))
        self.assertAlmostEqual(b.rms_error, rms(b), places=6)
        self.assertLessEqual(
            b.max_abs_error, a.max_abs_error + 1e-9
        )  # el máximo no sube

    def test_no_points_above_minimum_gives_none(self):
        from src.ac.coast_ref import coast_ref_least_squares

        self.assertIsNone(
            coast_ref_least_squares([(800, 5.0), (1000, 6.0)], 8000, 1000)
        )

    def test_rules_v2_supersede_v1_and_only_change_the_law(self):
        for k in ("rpm", "torque", "nonlinearity"):
            n = json.loads(
                (RULES / f"eng.coast_{k}_camso91392@2.json").read_text("utf-8")
            )
            o = json.loads(
                (RULES / f"eng.coast_{k}_camso91392@1.json").read_text("utf-8")
            )
            self.assertEqual(n["supersedes"], f"eng.coast_{k}_camso91392@1")
            self.assertEqual(n["transform"]["params"]["law"], "acs_least_squares")
            self.assertEqual(o["transform"]["params"]["law"], "acs_from_minimum")
            for f in ("target", "conditions", "inputs", "scope"):
                self.assertEqual(n[f], o[f], (k, f))
            self.assertEqual(n["status"], "provisional")


if __name__ == "__main__":
    unittest.main()
