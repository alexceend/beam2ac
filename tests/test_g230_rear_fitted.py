"""G2.30: trasera de rango 6 (``dwb_fitted``) y AWD con ``camsoEngine`` del
exportador 40340."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
RULES = ROOT / "catalog" / "rules"
SAMPLES = ROOT / "samples_beamng"
LINKAGE = "ir.suspension.axles.rear.linkage"


def _rule(name: str) -> dict:
    return json.loads((RULES / f"{name}.json").read_text("utf-8"))


def _kinds(rule: dict) -> list | None:
    for c in rule["conditions"]:
        if c["fact"] == LINKAGE:
            return c["value"]
    return None


def session(car: str):
    from src.convert.service import ConversionSession

    vd = SAMPLES / f"thershow_{car}" / "vehicles" / car
    s = ConversionSession.from_source(vd, None, None, 0.0)
    return s, {r.target: r for r in s.resolve()}


class TestFittedRules(unittest.TestCase):
    def test_fitted_rules_are_exclusive_with_their_origin(self):
        fitted = sorted(RULES.glob("susp.*_rear_fitted_40340@1.json"))
        self.assertEqual(len(fitted), 22)
        for p in fitted:
            f = json.loads(p.read_text("utf-8"))
            o = _rule(p.stem.replace("_fitted", ""))
            self.assertEqual(_kinds(f), ["dwb_fitted"], p.name)
            self.assertNotIn("dwb_fitted", _kinds(o), p.name)
            self.assertEqual(f["target"], o["target"], p.name)
            self.assertEqual(f["status"], "provisional", p.name)
            self.assertEqual(
                f["supersedes"], "", p.name
            )  # no sustituye a la regla de origen
            self.assertEqual(f["transform"], o["transform"], p.name)
            self.assertEqual(f["inputs"], o["inputs"], p.name)

    def test_stiffness_rules_say_that_bars_are_included(self):
        for n in ("spring_rate", "rod_length"):
            r = _rule(f"susp.{n}_rear_fitted_40340@1")
            txt = " ".join(r["assumptions"])
            self.assertIn("barras redundantes", txt)
            self.assertNotIn("rigidez en rueda = beamSpring·MR²", txt)

    def test_awd_rules_cite_the_ac_verdict_and_are_scoped(self):
        lsd = sorted(RULES.glob("dt.awd_*_lsd_40340@1.json"))
        self.assertEqual(len(lsd), 9)
        for p in lsd:
            r = json.loads(p.read_text("utf-8"))
            paths = [e["path"] for e in r["evidence"]]
            self.assertTrue(any("veredicto.json" in x for x in paths), p.name)
            facts = {c["fact"]: c["value"] for c in r["conditions"]}
            self.assertEqual(facts["ir.formats.exporter_version"], ["40340"])
            self.assertEqual(facts["ir.engine.device_type"], "camsoEngine")
            self.assertEqual(r["status"], "provisional")
        for p in sorted(RULES.glob("dt.gear_*_awd_manual_40340@1.json")):
            facts = {
                c["fact"]: c["value"]
                for c in json.loads(p.read_text("utf-8"))["conditions"]
            }
            self.assertEqual(facts["ir.drivetrain.layout"], "AWD", p.name)
            self.assertEqual(
                facts["ir.drivetrain.gearbox.kind"], "manualGearbox", p.name
            )


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestResolution(unittest.TestCase):
    def test_hyacinth_rear_uses_the_fitted_rules_with_the_bar_stiffness(self):
        s, res = session("hyacinth_sage")
        self.assertEqual(s.facts()[LINKAGE].value, "dwb_fitted")
        self.assertEqual(res["ac:suspensions.ini/REAR/TYPE"].value, "DWB")
        sr = res["ac:suspensions.ini/REAR/SPRING_RATE"]
        self.assertEqual(sr.rule, "susp.spring_rate_rear_fitted_40340@1")
        # k·MR² = 22000·1,0267² = 23 190 N/m del muelle + 16 894 N/m de las barras
        # (energía elástica)
        self.assertAlmostEqual(sr.value, 23190 + 16894, delta=60)
        # ROD_LENGTH = (k·MR·c − F_barras)/rigidez total: las barras empujan la rueda
        # hacia arriba y restan
        # apoyo (2 435 − 338 = 2 097 N sobre 40 084 N/m); con el signo inverso
        # saldría 0,069 m
        rl = res["ac:suspensions.ini/REAR/ROD_LENGTH"]
        self.assertEqual(rl.rule, "susp.rod_length_rear_fitted_40340@1")
        self.assertAlmostEqual(rl.value, 0.0523, delta=0.001)
        for k in ("WBCAR_TOP_FRONT", "WBTYRE_STEER", "WBCAR_STEER"):
            self.assertEqual(res[f"ac:suspensions.ini/REAR/{k}"].status, "inferred", k)

    def test_sucette_rear_still_uses_the_original_rules(self):
        _, res = session("sucette_poubelle")
        self.assertEqual(
            res["ac:suspensions.ini/REAR/SPRING_RATE"].rule,
            "susp.spring_rate_rear_40340@1",
        )

    def test_autist_awd_resolves_with_camso_rules(self):
        _, res = session("autist_cuanto")
        self.assertEqual(res["ac:drivetrain.ini/TRACTION/TYPE"].value, "AWD")
        self.assertEqual(res["ac:drivetrain.ini/GEARS/FINAL"].value, 4.72)
        self.assertEqual(res["ac:drivetrain.ini/GEARS/COUNT"].value, 5)
        self.assertAlmostEqual(
            res["ac:drivetrain.ini/AWD/FRONT_SHARE"].value, 55.0, places=3
        )
        # central lsd 1:1: PRELOAD de AC = 2·lsdPreload(10)·FINAL(4,72) (×2
        # demostrado en G2.22)
        self.assertAlmostEqual(
            res["ac:drivetrain.ini/AWD/CENTRE_DIFF_PRELOAD"].value, 94.4, places=3
        )
        self.assertAlmostEqual(
            res["ac:drivetrain.ini/AWD/CENTRE_DIFF_POWER"].value, 0.8, places=6
        )
        for t in (
            "ac:drivetrain.ini/TRACTION/TYPE",
            "ac:drivetrain.ini/AWD/REAR_DIFF_COAST",
            "ac:drivetrain.ini/GEARS/GEAR_3",
        ):
            self.assertNotIn(res[t].status, ("ambiguous", "manual_required"), t)

    def test_hyacinth_and_quail_tyres_resolve_with_free_rolling_reference(self):
        """G2.31: el ajuste SCTM de hyacinth y quail (fuera de tolerancia en G2.30 con κ
        referido a κ = 0) entra en 0,06 con la referencia de giro libre medido
        (``tyre.*_40340@2``); la tolerancia no cambió."""
        for car, axles in (
            ("hyacinth_sage", ("FRONT", "REAR")),
            ("quail_mirabilis", ("FRONT", "REAR")),
        ):
            _, res = session(car)
            self.assertEqual(res["ac:suspensions.ini/REAR/TYPE"].value, "DWB", car)
            for ax in axles:
                r = res[f"ac:tyres.ini/{ax}/DY_REF"]
                self.assertIsNotNone(r.value, (car, ax))
                self.assertTrue(r.rule.endswith("_40340@2"), (car, ax, r.rule))


class TestPrecheckSctm(unittest.TestCase):
    def test_sctm_keys_are_errors_only_with_version_10(self):
        from src.project.export import _blocker_severity, sctm_targets

        t = "ac:tyres.ini/FRONT/DY_REF"
        v10 = sctm_targets([{"target": "ac:tyres.ini/HEADER/VERSION", "value": 10}])
        self.assertEqual(_blocker_severity({"target": t}, {}, v10), "error")
        self.assertEqual(_blocker_severity({"target": t}, {}), "warning")
        # Ibiza (VERSION 7) y sin versión: no se exigen (el modelo es otro)
        self.assertEqual(
            sctm_targets([{"target": "ac:tyres.ini/HEADER/VERSION", "value": 7}]),
            frozenset(),
        )
        self.assertEqual(sctm_targets([]), frozenset())
        self.assertEqual(_blocker_severity({"target": "ac:power.lut"}, {}), "error")


@unittest.skipUnless(SAMPLES.is_dir(), "sin samples_beamng")
class TestAppDiagnostics(unittest.TestCase):
    def test_app_matches_the_exporter(self):
        from src.app import diagnostics

        for car, ok in (
            ("autist_cuanto", True),
            ("hyacinth_sage", True),
            ("quail_mirabilis", True),
        ):
            d = diagnostics.inspect_vehicle(
                str(SAMPLES / f"thershow_{car}" / "vehicles" / car)
            )
            self.assertEqual(d["exportable_today"], ok, car)


if __name__ == "__main__":
    unittest.main()
