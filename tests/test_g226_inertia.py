"""G2.26: inercia de la carrocería de AC (``[EXPLICIT_INERTIA] INERTIA``) del 200409.

Comprueba:

* ``ac_body_inertia``: resta de los bujes puntuales y traslado al CdG del cuerpo
  (caso analítico) y rechazo de entradas no válidas;
* ``ir.mass.inertia_about_cog`` = diagonal del tensor de ``compute_mass_geometry``;
* la regla ``car.explicit_inertia_200409_nodriver@2``: solo 200409 y piloto 0 kg; con
  Prothean y Omega reproduce el cálculo exacto con los centros de rueda medidos
  (``output/g226_2026-11-10/inercia/calcular_inercia.json``) a ≤ 0,5 kg·m²;
* la ausencia de la clave es un error del exportador (AC usaría inercia 0,0,0).
"""

from __future__ import annotations

import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs import transforms
from src.attrs.store import REPO_CATALOG, Catalog
from src.project.export import ZERO_IS_WRONG_KEYS, _blocker_severity

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
PROTHEAN = "2020_rpm_prothean_s"
OMEGA = "rpm_omega_one_concept"
TARGET = "ac:car.ini/EXPLICIT_INERTIA/INERTIA"
RULE = "car.explicit_inertia_200409_nodriver@2"
# calcular_inercia.json (centros de rueda medidos, sin aproximar por TRACK/WB)
EXACT = {PROTHEAN: (2357.74, 2706.96, 532.69), OMEGA: (2422.70, 2643.86, 451.87)}


def available(name: str) -> bool:
    return (SAMPLES / name).is_dir()


@lru_cache(maxsize=None)
def preview(name: str, driver: float):
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    vd = SAMPLES / name / "vehicles" / name
    return preview_physics(load_vehicle_dir(vd, f"{name}.pc"), driver)


@lru_cache(maxsize=None)
def resolved(name: str, driver: float):
    from src.attrs.report import resolve_vehicle

    _, results = resolve_vehicle(
        preview(name, driver), Catalog.load(REPO_CATALOG), "ac_physics", driver
    )
    return {r.target: r for r in results}


class TestTransform(unittest.TestCase):
    # vehículo de 1000 kg, bujes de 20/30 kg en (±0,8, −0,1, 1,2) y (±0,8, −0,1, −1,3)
    INPUTS = {
        "inertia": ([2000.0, 2200.0, 500.0], "kg·m²"),
        "total": (1000.0, "kg"),
        "front_fraction": (1.3 / 2.5, "1"),
        "wheelbase": (2.5, "m"),
        "track_front": (1.6, "m"),
        "track_rear": (1.6, "m"),
        "height_front": (-0.1, "m"),
        "height_rear": (-0.1, "m"),
        "hub_front": (20.0, "kg"),
        "hub_rear": (30.0, "kg"),
    }

    def test_analytic(self):
        pts = [(20.0, (s * 0.8, -0.1, 1.2)) for s in (1, -1)]
        pts += [(30.0, (s * 0.8, -0.1, -1.3)) for s in (1, -1)]
        mc = 1000.0 - 100.0
        e = [-sum(h * r[i] for h, r in pts) / mc for i in range(3)]
        exp = []
        for i, base in enumerate((2000.0, 2200.0, 500.0)):
            v = base - sum(h * (sum(c * c for c in r) - r[i] ** 2) for h, r in pts)
            v -= mc * (sum(c * c for c in e) - e[i] ** 2)
            exp.append(round(v, 1))
        got = transforms.apply("ac_body_inertia", self.INPUTS, {}, "kg·m²")
        self.assertEqual(got, exp)
        self.assertTrue(all(g < b for g, b in zip(got, (2000.0, 2200.0, 500.0))))

    def test_rejects_bad_inputs(self):
        for key, bad in (
            ("inertia", ([1.0, 2.0], "kg·m²")),
            ("inertia", ([0.0, 2.0, 3.0], "kg·m²")),
            ("hub_front", (600.0, "kg")),
            ("front_fraction", (1.2, "1")),
        ):
            inp = dict(self.INPUTS)
            inp[key] = bad
            with self.assertRaises(transforms.TransformError, msg=key):
                transforms.apply("ac_body_inertia", inp, {}, "kg·m²")
        inp = dict(self.INPUTS)
        del inp["wheelbase"]
        with self.assertRaises(transforms.TransformError):
            transforms.apply("ac_body_inertia", inp, {}, "kg·m²")
        self.assertTrue(transforms.implied_assumptions("ac_body_inertia", {}))


class TestImport(unittest.TestCase):
    @unittest.skipUnless(available(PROTHEAN), "sin samples_beamng")
    def test_ir_inertia_is_tensor_diagonal(self):
        from src.importers.beamng import import_preview

        pv = preview(PROTHEAN, 0.0)
        p = import_preview(pv, 0.0).get("mass.inertia_about_cog")
        self.assertEqual(p.unit, "kg·m²")
        self.assertEqual(p.kind, "measured")
        diag = [pv.geometry.inertia_ac[i][i] for i in range(3)]
        for a, b in zip(p.value, diag):
            self.assertAlmostEqual(a, b, places=6)


class TestRule(unittest.TestCase):
    def test_rule_scope(self):
        rule = Catalog.load(REPO_CATALOG).rules[RULE]
        conds = {(c.fact, c.op, str(c.value)) for c in rule.conditions}
        self.assertIn(("ir.formats.exporter_version", "in", "['200409']"), conds)
        self.assertIn(("ir.mass.driver", "eq", "0"), conds)
        self.assertEqual(rule.status, "provisional")
        self.assertEqual(rule.target, TARGET)

    def test_values_match_exact_computation(self):
        for name, exp in EXACT.items():
            if not available(name):
                self.skipTest("sin samples_beamng")
            r = resolved(name, 0.0)[TARGET]
            self.assertEqual(r.status, "inferred", name)
            self.assertEqual(r.rule, RULE)
            for a, b in zip(r.value, exp):
                self.assertAlmostEqual(a, b, delta=0.5, msg=name)

    @unittest.skipUnless(available(PROTHEAN), "sin samples_beamng")
    def test_driver_rule_applies_with_driver(self):
        # G2.45: con piloto manda car.explicit_inertia_200409_driver (inercia mayor)
        r0 = resolved(PROTHEAN, 0.0)[TARGET]
        r = resolved(PROTHEAN, 70.0)[TARGET]
        self.assertEqual((r.status, r.rule), ("inferred", "car.explicit_inertia_200409_driver@1"))
        self.assertTrue(all(0 < b - a < 25 for a, b in zip(r0.value, r.value)))


class TestProtectedEvidence(unittest.TestCase):
    def test_g226_evidence_is_historical(self):
        from src import safe_paths as S

        self.assertIn(S.DEFAULT_OUTPUT_DIR / "g226_2026-11-10", S.HISTORICAL_DIRS)
        with self.assertRaises(Exception):
            S.ensure_safe_output_path(
                S.DEFAULT_OUTPUT_DIR / "g226_2026-11-10" / "runs" / "x.json"
            )


class TestExportSeverity(unittest.TestCase):
    def test_missing_inertia_is_error(self):
        self.assertIn(TARGET, ZERO_IS_WRONG_KEYS)
        self.assertEqual(_blocker_severity({"target": TARGET}, {}), "error")


if __name__ == "__main__":
    unittest.main()
