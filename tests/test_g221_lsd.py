"""G2.21: LSD (D-G212-LSD) — port de el cálculo 4WD de AC, método del banco de AC, transformación
``scale_product``, herramienta de reglas con puerta de evidencia e invariantes de
producción.

El factor ×2 NO está demostrado en AC (falta el banco del juego, pendiente de
autorización):
estas pruebas comprueban que producción sigue sin reglas LSD y que la puerta no se
abre con
datos sintéticos ni sin medida.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.attrs.transforms import TransformError, apply
from src.lab.ac_step4wd import (
    AcDiff,
    AwdInertia,
    AwdSetup,
    AwdState,
    Corner,
    sat,
    simulate_corner,
    step2wd_axle,
    step4wd,
    transfer_axle,
    transfer_centre,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "catalog" / "add_lsd_awd_200409_rules.py"
BANCO = ROOT / "output" / "g221_2026-11-07" / "banco_ac"


def _load_tool():
    spec = importlib.util.spec_from_file_location("add_lsd_awd_200409_rules", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestStep4wdPort(unittest.TestCase):
    """Lectura [C] de el cálculo 4WD de AC: ±lim/2 por rueda; central ±lim/4 por rueda."""

    def test_axle_lock_is_half_limit_each_side(self):
        st = AwdState(shafts=[10.0, 10.0, 12.0, 10.0])
        setup = AwdSetup(rear=AcDiff(preload=400.0))
        t = step4wd(st, setup, AwdInertia(), 0.0, (0.0, 0.0, 0.0, 0.0), 0.0, 1 / 333)
        self.assertEqual(t.lim_rear, 400.0)
        l_, r_ = t.axle_rear
        self.assertAlmostEqual(
            l_ - r_, -400.0 * sat(2.0), places=9
        )  # diferencia = lim·sat
        self.assertAlmostEqual(l_, -r_, places=12)

    def test_centre_lock_quarter_per_wheel(self):
        st = AwdState(shafts=[10.0, 10.0, 12.0, 12.0])
        t = step4wd(
            st,
            AwdSetup(centre=AcDiff(preload=300.0)),
            AwdInertia(),
            0.0,
            (0.0,) * 4,
            0.0,
            1 / 333,
        )
        rear = t.centre[2] + t.centre[3]
        front = t.centre[0] + t.centre[1]
        self.assertAlmostEqual(rear - front, -300.0 * sat(2.0), places=9)

    def test_limits_use_nominal_share_and_coast(self):
        d = AcDiff(power=0.5, coast=0.3, preload=10.0)
        self.assertAlmostEqual(d.limit(100.0, 10.0), 10.0 + 500.0)
        self.assertAlmostEqual(d.limit(-100.0, 10.0), 10.0 + 300.0)
        self.assertAlmostEqual(d.limit(100.0, 0.0), 10.0)  # punto muerto: solo precarga

    def test_open_diffs_each_wheel_own_reaction(self):
        """Con bloqueos 0, I·Δω_i/dt − fb_i es igual en las dos ruedas de cada eje."""
        st = AwdState(shafts=[10.0, 11.0, 12.0, 13.0])
        before = list(st.shafts)
        fb = (-50.0, 20.0, -80.0, 5.0)
        dt, i_s = 1 / 333, AwdInertia().shaft
        step4wd(st, AwdSetup(), AwdInertia(), 0.0, fb, 0.0, dt)
        tau = [i_s * (st.shafts[k] - before[k]) / dt - fb[k] for k in range(4)]
        for k in range(1, 4):
            self.assertAlmostEqual(tau[k], tau[0], places=6)

    def test_2wd_static_lock_when_demand_below_limit(self):
        shafts, drive = [10.0, 10.0], [10.0]
        slipped = step2wd_axle(
            shafts,
            drive,
            AcDiff(preload=400.0),
            1.35,
            0.0,
            0.0,
            0.0,
            (-30.0, 30.0),
            1 / 333,
        )
        self.assertFalse(slipped)
        self.assertEqual(shafts[0], shafts[1])
        slipped = step2wd_axle(
            shafts,
            drive,
            AcDiff(preload=20.0),
            1.35,
            0.0,
            0.0,
            0.0,
            (-30.0, 30.0),
            1 / 333,
        )
        self.assertTrue(slipped)


class TestBenchMethod(unittest.TestCase):
    """Los estimadores recuperan el bloqueo en datos sintéticos y distinguen las
    hipótesis."""

    def _rho(self, scale: float) -> float:
        setup = AwdSetup(rear=AcDiff(preload=400.0))
        rows = simulate_corner(
            setup, Corner(radius=10.0), 0.0, 0.0, seconds=3.0, lock_scale=scale
        )
        tail = rows[len(rows) // 2 :]
        xs, ps = [], []
        for a, b in zip(tail, tail[1:]):
            d_rel = ((b["w2"] - b["w3"]) - (a["w2"] - a["w3"])) / (b["t"] - a["t"])
            xs.append(transfer_axle(a["fb2"], a["fb3"], d_rel, a["I"]))
            ps.append(400.0 * sat(a["w2"] - a["w3"]))
        return sum(x * p for x, p in zip(xs, ps)) / sum(p * p for p in ps)

    def test_rho_reading(self):
        self.assertAlmostEqual(self._rho(1.0), 1.0, places=3)

    def test_rho_alternatives_distinguished(self):
        self.assertAlmostEqual(self._rho(2.0), 2.0, places=2)
        self.assertAlmostEqual(self._rho(0.5), 0.5, places=3)

    def test_centre_estimator_neutral(self):
        setup = AwdSetup(centre=AcDiff(preload=300.0))
        rows = simulate_corner(setup, Corner(radius=10.0), 0.0, 0.0, seconds=3.0)
        tail = rows[len(rows) // 2 :]
        a, b = tail[10], tail[11]

        def s(q):
            return (q["w2"] + q["w3"]) - (q["w0"] + q["w1"])

        x = transfer_centre(
            tuple(a[f"fb{i}"] for i in range(4)),
            (s(b) - s(a)) / (b["t"] - a["t"]),
            a["I"],
        )
        dw = s(a) / 2.0
        self.assertGreater(abs(dw), 0.5)
        self.assertAlmostEqual(x / (300.0 * sat(dw)), 1.0, places=2)

    def test_sub_saturation_chatter_prediction(self):
        """[C+L] con g = dt·lim/(0,1·I) > 2 el bloqueo oscila paso a paso con poca
        demanda."""
        dt, i_s = 1 / 333, 1.35
        for preload, expect in ((60.0, False), (400.0, True)):
            setup = AwdSetup(rear=AcDiff(preload=preload))
            rows = simulate_corner(
                setup,
                Corner(radius=150.0),
                0.0,
                0.0,
                seconds=3.0,
                sample_every=1,
                inertia=AwdInertia(shaft=i_s),
            )
            tail = rows[len(rows) // 2 :]
            incs = [
                (b["w2"] - b["w3"]) - (a["w2"] - a["w3"])
                for a, b in zip(tail, tail[1:])
            ]
            rms = (sum(x * x for x in incs) / len(incs)) ** 0.5
            self.assertEqual(
                rms > 0.1, expect, (preload, rms, dt * preload / (0.1 * i_s))
            )


class TestScaleProduct(unittest.TestCase):
    def test_value_and_units(self):
        y = apply(
            "scale_product",
            {"x": (50.0, "N·m"), "final": (3.38, "1")},
            {"factor": 2.0, "base": "x"},
            "N·m",
        )
        self.assertAlmostEqual(y, 338.0)

    def test_rejects_dimensional_factor(self):
        with self.assertRaises(TransformError):
            apply(
                "scale_product",
                {"x": (50.0, "N·m"), "f": (3.0, "N·m")},
                {"factor": 2.0, "base": "x"},
                "N·m",
            )
        with self.assertRaises(TransformError):
            apply(
                "scale_product",
                {"x": (50.0, "N·m")},
                {"factor": 2.0, "base": "y"},
                "N·m",
            )


class TestRuleToolGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = _load_tool()

    def test_dry_run_builds_nine_valid_rules(self):
        rules = self.tool.build(self.tool.ev("report", "x"))
        self.assertEqual(len(rules), 9)
        targets = {r.target for r in rules}
        self.assertEqual(len(targets), 9)
        for r in rules:
            facts = {c.fact: c for c in r.conditions}
            self.assertEqual(facts["ir.formats.exporter_version"].value, ["200409"])
            self.assertEqual(facts["ir.engine.device_type"].value, "combustionEngine")
            self.assertEqual(facts["ir.drivetrain.layout"].value, "AWD")
            self.assertEqual(r.status, "provisional")
            self.assertTrue(
                any(f.endswith(".kind") and c.value == "lsd" for f, c in facts.items())
            )

    def _verdict(self, d: Path, **over) -> Path:
        v = {
            "schema": "beam2ac.g221.lsd_ac_verdict/1",
            "source": "ac_in_game",
            "acs_exe": {"sha256": "" + "0" * 56},
            "acs_matches_g211_analysis": True,
            "params": {k: "coincide_lectura_C" for k in self.tool.REQUIRED},
        }
        v.update(over)
        p = d / "veredicto.json"
        p.write_text(json.dumps(v), "utf-8")
        return p

    def test_gate_rejects_synthetic_and_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            with self.assertRaises(self.tool.GateError):
                self.tool.check_verdict(self._verdict(d, source="sintetico"))
            params = {k: "coincide_lectura_C" for k in self.tool.REQUIRED}
            params["A2.centre.power"] = "no_concluyente"
            with self.assertRaises(self.tool.GateError):
                self.tool.check_verdict(self._verdict(d, params=params))
            with self.assertRaises(self.tool.GateError):
                self.tool.check_verdict(
                    self._verdict(d, acs_matches_g211_analysis=False)
                )
            v, sha = self.tool.check_verdict(self._verdict(d))
            self.assertEqual(len(sha), 64)

    def test_synthetic_bench_verdict_is_rejected(self):
        p = BANCO / "simulacion" / "analisis_escala_1" / "veredicto.json"
        if not p.exists():
            self.skipTest("sin la simulación de G2.21")
        with self.assertRaises(self.tool.GateError):
            self.tool.check_verdict(p)

    def test_cli_refuses_without_evidence_and_hypothetical_on_repo(self):
        self.assertEqual(self.tool.main([]), 2)
        self.assertEqual(self.tool.main(["--hypothetical"]), 2)

    def test_hypothetical_writes_only_into_copy(self):
        with tempfile.TemporaryDirectory() as t:
            cat = Path(t) / "catalog"
            # G2.23: el catálogo real ya tiene las 9; copia = catálogo de antes.
            shutil.copytree(
                REPO_CATALOG, cat, ignore=shutil.ignore_patterns("*_lsd_200409@*.json")
            )
            self.assertEqual(
                self.tool.main(["--hypothetical", "--catalog", str(cat)]), 0
            )
            c = Catalog.load(cat)
            new = [k for k in c.rules if "_lsd_200409" in k]
            self.assertEqual(len(new), 9)


class TestProductionInvariants(unittest.TestCase):
    """Producción solo tiene reglas LSD respaldadas por una medida de AC.

    Hasta G2.22 el invariante era «ninguna regla LSD» (no había medida). G2.23 escribe
    las 9 con el veredicto de G2.22 (``source = ac_in_game``): el invariante pasa a ser
    que toda regla LSD del catálogo cita un veredicto que la puerta acepta. Estado nuevo
    en ``tests/test_g223_lsd_rules.py``."""

    def test_lsd_rules_only_with_ac_verdict(self):
        tool = _load_tool()
        names = sorted(
            p.name
            for p in (REPO_CATALOG / "rules").glob("dt.awd_*.json")  # las del [AWD]
            if "_lsd" in p.name
            and "_lsd_40340@"
            not in p.name  # G2.30: 40340 aparte; G2.27: las 2WD citan B0
            and not p.name.endswith(
                ("_lsd_viscc_40340@1.json", "_openmode_lsdc_40340@1.json")
            )  # familias 40340 posteriores
        )
        expected = sorted(f"{r.key}.json" for r in tool.build(tool.ev("report", "x")))
        self.assertEqual(names, expected)
        for n in names:
            rule = json.loads((REPO_CATALOG / "rules" / n).read_text("utf-8"))
            files = [e for e in rule["evidence"] if e["type"] == "file"]
            self.assertEqual(len(files), 1, n)
            _, sha = tool.check_verdict(ROOT / files[0]["path"])
            self.assertEqual(sha, files[0]["sha256"], n)

    def test_2wd_preload_rule_unchanged(self):
        names = {p.name for p in (REPO_CATALOG / "rules").glob("dt.preload@*.json")}
        self.assertEqual(
            names,
            {
                "dt.preload@1.json",
                "dt.preload@2.json",
                "dt.preload@3.json",
                "dt.preload@4.json",
            },  # @4: G2.39
        )


class TestBenchPackages(unittest.TestCase):
    """Los paquetes de diagnóstico existen fuera de la instalación y no están
    instalados."""

    def test_manifest(self):
        m = BANCO / "paquetes" / "manifest.json"
        if not m.exists():
            self.skipTest("sin los paquetes de G2.21")
        data = json.loads(m.read_text("utf-8"))
        self.assertFalse(data["installed"])
        self.assertEqual(len(data["packages"]), 7)
        for p in data["packages"]:
            self.assertTrue(p["car_id"].startswith("beam2ac_g221lsd_"))
            self.assertIn("data/script.lua", p["files"])


if __name__ == "__main__":
    sys.exit(unittest.main())
