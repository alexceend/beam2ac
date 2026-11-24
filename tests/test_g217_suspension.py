"""G2.17: cierre de suspensión y dirección del 200409.

Comprueba la transformación ``ac_toe_out``, el diedro de las barras de torsión, la
extracción nueva (convergencia con signo, topes, barra estabilizadora, sentido del
amortiguador), el IR, las reglas (solo 200409 y DWB medido; LSRR @2 sin tocar el
atributo ni las decisiones) y que nada cambia fuera de su ámbito.
"""

from __future__ import annotations

import json
import math
import unittest
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from src.attrs import transforms
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract.linkage import dihedral
from src.importers.beamng import LIMIT_LINEAR_FACTOR, _import_damper_g217

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
PROTHEAN = "2020_rpm_prothean_s"
ATLANTA = "2020_rpm_atlanta_gt_3_6t"
ATACAMA = "2020_rpm_atacama_sport__4x4"


@lru_cache(maxsize=None)
def preview(name: str):
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    vd = SAMPLES / name / "vehicles" / name
    pc = sorted(p.name for p in vd.glob("*.pc"))[0]
    return preview_physics(load_vehicle_dir(vd, pc), 70.0)


@lru_cache(maxsize=None)
def session(name: str):
    from src.convert.service import ConversionSession
    from src.importers.beamng import import_preview

    ir = import_preview(preview(name), 70.0)
    s = ConversionSession.from_ir(ir)
    s.resolve()
    return ir, {r.target: r for r in s.results}


def available() -> bool:
    return (SAMPLES / PROTHEAN).is_dir()


class TestTransforms(unittest.TestCase):
    def test_ac_toe_out_sign_and_units(self):
        # toe-in 0,08° y la barra va 3,2 mm hacia DENTRO por grado de toe-in ⇒
        # hay que llevarla hacia FUERA (TOE_OUT negativo)
        v = transforms.apply(
            "ac_toe_out",
            {"toe": (0.08, "deg"), "shift": (0.0032, "m/deg")},
            {},
            "m",
        )
        self.assertAlmostEqual(v, -0.08 * 0.0032, places=12)
        # barra que va hacia dentro con toe-OUT (signo de la sensibilidad negativo)
        v2 = transforms.apply(
            "ac_toe_out",
            {"toe": (0.08, "deg"), "shift": (-0.0032, "m/deg")},
            {},
            "m",
        )
        self.assertAlmostEqual(v2, 0.08 * 0.0032, places=12)
        # radianes en la entrada: se convierten
        v3 = transforms.apply(
            "ac_toe_out",
            {"toe": (math.radians(0.08), "rad"), "shift": (0.0032, "m/deg")},
            {},
            "m",
        )
        self.assertAlmostEqual(v3, v, places=12)
        with self.assertRaises(transforms.TransformError):
            transforms.apply("ac_toe_out", {"toe": (0.08, "deg")}, {}, "m")
        self.assertTrue(transforms.implied_assumptions("ac_toe_out", {}))

    def test_dihedral(self):
        # brazos a 90° alrededor del eje x
        a = dihedral((0, 1, 0), (0, 0, 0), (1, 0, 0), (1, 0, 1))
        self.assertAlmostEqual(abs(a), math.pi / 2, places=12)
        b = dihedral((0, 1, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0))
        self.assertAlmostEqual(b, 0.0, places=12)


class TestDamperDirection(unittest.TestCase):
    """Compresión = el amortiguador se acorta → beamDamp/beamDampFast [M]; si se alarga
    al subir la rueda, los papeles se cruzan."""

    def run_import(self, shortens: bool):
        d = SimpleNamespace(
            bump=4000.0,
            rebound=5000.0,
            bump_fast=1000.0,
            rebound_fast=2000.0,
            velocity_split=0.1,
            velocity_split_rebound=0.2,
            cutoff_hz=500.0,
        )
        axle = SimpleNamespace(corners=[SimpleNamespace(damper=d)] * 2)
        res = [
            SimpleNamespace(damper_motion_ratio=0.5, damper_shortens_on_bump=shortens)
        ] * 2
        got: dict = {}

        def put(path, value, unit, kind, *refs, note=""):
            got[path.rsplit(".", 1)[1]] = value

        def missing(name, unit, why):
            got[name] = None

        _import_damper_g217(None, axle, "x", res, put, missing)
        return got

    def test_shortens(self):
        g = self.run_import(True)
        self.assertAlmostEqual(g["damper_rate_slow_bump"], 1000.0)
        self.assertAlmostEqual(g["damper_rate_slow_rebound"], 1250.0)
        self.assertAlmostEqual(g["damper_rate_fast_bump"], 250.0)
        self.assertAlmostEqual(g["damper_rate_fast_rebound"], 500.0)
        self.assertAlmostEqual(g["damper_fast_threshold_bump"], 0.2)
        self.assertAlmostEqual(g["damper_fast_threshold_rebound"], 0.4)

    def test_lengthens(self):
        g = self.run_import(False)
        self.assertAlmostEqual(g["damper_rate_slow_bump"], 1250.0)
        self.assertAlmostEqual(g["damper_rate_fast_bump"], 500.0)
        self.assertAlmostEqual(g["damper_fast_threshold_bump"], 0.4)


@unittest.skipUnless(available(), "sin samples_beamng")
class TestExtraction(unittest.TestCase):
    def test_prothean_corners(self):
        pv = preview(PROTHEAN)
        for ax in pv.chassis.suspension.axles:
            for c in ax.corners:
                L = c.linkage
                self.assertTrue(L.is_dwb)
                self.assertTrue(L.damper_shortens_on_bump)
                # barra detrás del eje: llevarla hacia dentro abre la rueda
                self.assertLess(L.rod_inward_shift_per_toe_in, 0)
                lin = L.rest_toe_deg * L.rod_inward_shift_per_toe_in
                self.assertLess(abs(lin - L.rod_inward_shift_exact), 1e-3 * abs(lin))
                self.assertGreater(L.bumpstop_travel_up, 0.05)
                self.assertGreater(L.bumpstop_travel_down, 0.05)
                self.assertEqual(len(L.torsion_angle_rates), 1)
                self.assertIsNotNone(c.bumpstop)

    def test_bumpstop_contact_length(self):
        # en el contacto la viga del tope mide la longitud de creación − shortBoundRange
        pv = preview(PROTHEAN)
        c = pv.chassis.suspension.axles[0].corners[0]
        s = c.bumpstop
        self.assertAlmostEqual(s.short_bound_range, 0.06007, places=6)
        self.assertGreater(c.linkage.bumpstop_travel_up, s.short_bound_range * 0.95)

    def test_strut_and_axle_have_no_probes(self):
        # G2.19: el puntal de atlanta ya se mide (strut_rail) y tiene sondeos de topes y
        # barra; el eje rígido de atacama sigue sin ellos
        for name, axle in ((ATACAMA, 1),):
            pv = preview(name)
            for c in pv.chassis.suspension.axles[axle].corners:
                self.assertFalse(c.linkage.is_dwb)
                self.assertTrue(c.linkage.is_axle)  # G2.44: heave puro, sin sondeos DWB
                self.assertIsNotNone(c.linkage.bumpstop_travel_up)
                self.assertEqual(c.linkage.torsion_angle_rates, [])
        for c in preview(ATLANTA).chassis.suspension.axles[0].corners:
            self.assertFalse(c.linkage.is_dwb)
            self.assertTrue(c.linkage.is_strut)
            self.assertIsNotNone(c.linkage.bumpstop_travel_up)
            self.assertEqual(len(c.linkage.torsion_angle_rates), 1)


@unittest.skipUnless(available(), "sin samples_beamng")
class TestIRAndRules(unittest.TestCase):
    def test_ir_fields(self):
        ir, _ = session(PROTHEAN)
        ax = ir.suspension.axles["front"]
        self.assertTrue(ax.damper_rate_fast_bump.present)
        self.assertTrue(ax.bumpstop_rate.present)
        self.assertTrue(ax.antiroll_rate.present)
        self.assertTrue(ax.rod_inward_shift_per_toe_in.present)
        self.assertEqual(ax.damper_fast_threshold_bump.unit, "m/s")
        # 11·kL·MR² en el contacto
        kl = 434238.5
        mr = preview(PROTHEAN).chassis.suspension.axles[0].corners[0].linkage
        self.assertAlmostEqual(
            ax.bumpstop_rate.value,
            round(LIMIT_LINEAR_FACTOR * kl * mr.bumpstop_motion_ratio_up**2, 1),
            places=0,
        )
        # G2.19: con el puntal en raíl medido, la barra delantera de atlanta también
        # se mide (antes ausente con motivo)
        atl, _ = session(ATLANTA)
        self.assertTrue(atl.suspension.axles["front"].antiroll_rate.present)

    def test_rules_resolve_only_dwb(self):
        _, res = session(PROTHEAN)
        for key in (
            "ac:suspensions.ini/FRONT/TOE_OUT",
            "ac:suspensions.ini/FRONT/DAMP_FAST_BUMP",
            "ac:suspensions.ini/REAR/DAMP_FAST_REBOUNDTHRESHOLD",
            "ac:suspensions.ini/FRONT/BUMPSTOP_UP",
            "ac:suspensions.ini/REAR/BUMPSTOP_DN",
            "ac:suspensions.ini/FRONT/BUMP_STOP_RATE",
            "ac:suspensions.ini/ARB/FRONT",
            "ac:suspensions.ini/ARB/REAR",
            "ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO",
        ):
            self.assertEqual(res[key].status, "inferred", key)
        self.assertGreater(res["ac:suspensions.ini/FRONT/TOE_OUT"].value, 0)
        self.assertNotEqual(res["ac:suspensions.ini/REAR/TOE_OUT"].status, "inferred")
        self.assertEqual(
            res["ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO"].unit, "m/deg"
        )
        # G2.19: en el puntal de atlanta las reglas DWB siguen sin aplicarse; esos
        # objetivos los resuelven sus clones para strut_rail
        _, atl = session(ATLANTA)
        for key in (
            "ac:suspensions.ini/FRONT/TOE_OUT",
            "ac:suspensions.ini/ARB/FRONT",
            "ac:suspensions.ini/FRONT/BUMPSTOP_UP",
        ):
            self.assertEqual(atl[key].status, "inferred", key)
            self.assertIn("_strut_200409@1", atl[key].rule, key)
        self.assertEqual(atl["ac:suspensions.ini/ARB/REAR"].status, "inferred")

    def test_lsrr_attribute_and_decisions_untouched(self):
        cat = Catalog.load(REPO_CATALOG)
        attr = cat.attributes["ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO"]
        self.assertEqual(attr.unit, "undocumented")
        r2 = cat.rules["ctrl.linear_steer_rod_ratio_200409@2"]
        self.assertEqual(r2.supersedes, "ctrl.linear_steer_rod_ratio_200409@1")
        self.assertEqual(r2.transform.output_unit, "m/deg")
        dec = [
            json.loads(x)
            for x in (REPO_CATALOG / "decisions/seat_ibiza_acfr.jsonl")
            .read_text("utf-8")
            .splitlines()
            if "LINEAR_STEER_ROD_RATIO" in x
        ]
        self.assertEqual(
            [(d["value"], d["unit"]) for d in dec], [(0.003156, "undocumented")]
        )

    def test_g217_rules_scope(self):
        cat = Catalog.load(REPO_CATALOG)
        new = [
            r for r in cat.rules.values() if any("G2.17" in h.reason for h in r.history)
        ]
        self.assertGreaterEqual(len(new), 22)
        for r in new:
            facts = {c.fact: c for c in r.conditions}
            self.assertEqual(
                facts["ir.formats.exporter_version"].value, ["200409"], r.key
            )
            self.assertTrue(any(f.endswith(".linkage") for f in facts), r.key)
            self.assertEqual(r.status, "provisional")


if __name__ == "__main__":
    unittest.main()
