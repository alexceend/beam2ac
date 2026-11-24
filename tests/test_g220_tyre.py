"""G2.20: modelo de agarre del neumático (banco de BeamNG → SCTM de AC)."""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from src.ac import tyre_sctm as T
from src.ac.tyre_fit import (
    INI_FIELDS,
    Point,
    ac_camber,
    fit,
    points_from_dataset,
    wheel_side,
)
from src.attrs import transforms
from src.extract import tyre_bench as tb

P0 = T.SctmParams(
    fz0=5000.0,
    dy_ref=1.45,
    dx_ref=1.5,
    ls_expy=0.9,
    ls_expx=0.93,
    friction_limit_angle=7.5,
    flex_gain=0.45,
    falloff_level=0.6,
    falloff_speed=1.5,
    cx_mult=1.6,
    brake_dx_mod=0.02,
    speed_sensitivity=0.02,
    camber_gain=0.12,
)


class TestSctm(unittest.TestCase):
    def test_shape_peak_and_slope(self):
        p = 0.13
        self.assertAlmostEqual(T.pure_shape(p, p, 0.6, 1.5), 1.0, places=12)
        h = 1e-7
        slope0 = T.pure_shape(h, p, 0.6, 1.5) / h
        self.assertAlmostEqual(slope0, 3.0 / p, places=4)  # f = 1 − (1 − x)³
        # continuidad y caída hacia el nivel asintótico
        self.assertAlmostEqual(T.pure_shape(p + 1e-9, p, 0.6, 1.5), 1.0, places=6)
        self.assertAlmostEqual(T.pure_shape(1e6, p, 0.6, 1.5), 0.6, places=4)

    def test_load_sensitivity_reference(self):
        self.assertAlmostEqual(T.static_d(P0, P0.fz0, "y"), P0.dy_ref, places=12)
        self.assertAlmostEqual(T.static_d(P0, P0.fz0, "x"), P0.dx_ref, places=12)
        d2 = T.static_d(P0, 2 * P0.fz0, "y")
        self.assertAlmostEqual(d2, P0.dy_ref * 2 ** (P0.ls_expy - 1), places=12)
        # DX_REF = 0 ⇒ 1,2·DY_REF (la lectura de compuestos de AC)
        self.assertAlmostEqual(
            T.static_d(P0.with_(dx_ref=0.0), P0.fz0, "x"), 1.2 * 1.45
        )

    def test_peak_slip_from_fla_and_flex_gain(self):
        self.assertAlmostEqual(
            T.peak_slip(P0, P0.fz0), math.tan(7.5 * 0.017453), places=9
        )
        self.assertAlmostEqual(
            T.peak_slip(P0, 2 * P0.fz0), math.tan(1.45 * 7.5 * 0.017453), places=9
        )

    def test_solve_pure_cases(self):
        # AC convierte con 0,017453 (no π/180): el máximo está en
        # atan(tan(7,5·0,017453))
        fy, fx = T.solve(P0, 5000.0, math.atan(T.peak_slip(P0, 5000.0)), 0.0, speed=0.0)
        self.assertAlmostEqual(fy, 1.45 * 5000.0, places=6)
        self.assertEqual(fx, 0.0)
        fy, fx = T.solve(P0, 5000.0, math.radians(-3), 0.0)
        self.assertLess(fy, 0)
        # longitudinal: máximo en s = p/CX_MULT con s = κ/(1+κ)
        s = math.tan(7.5 * 0.017453) / 1.6
        k = s / (1 - s)
        _, fxp = T.solve(P0, 5000.0, 0.0, k, speed=0.0)
        self.assertAlmostEqual(fxp, 1.5 * 5000.0, places=6)
        # frenada con BRAKE_DX_MOD = 0,02 ⇒ ×1,02
        kb = -s / (1 + s)
        _, fxb = T.solve(P0, 5000.0, 0.0, kb, speed=0.0)
        self.assertAlmostEqual(fxb, -1.02 * 1.5 * 5000.0, places=6)
        self.assertEqual(T.SctmParams(1, 1, 1, 1, 1, 7).brake_dx_mult, 1.0)

    def test_camber_thrust_sign(self):
        # γ_AC > 0 con CAMBER_GAIN > 0 equivale a deriva positiva
        fy, _ = T.solve(P0, 5000.0, 0.0, 0.0, camber=math.radians(2))
        self.assertGreater(fy, 0)
        self.assertEqual(ac_camber(-0.03, "left"), 0.03)
        self.assertEqual(ac_camber(-0.03, "right"), -0.03)
        self.assertEqual(wheel_side("RL"), "left")
        with self.assertRaises(ValueError):
            wheel_side("X1")

    def test_dcamber_defaults(self):
        self.assertEqual(P0.dcamber, (0.1, -0.8))
        self.assertEqual(P0.with_(dcamber_0=1.0, dcamber_1=-5.0).dcamber, (1.0, -5.0))

    def test_ini_fields_complete(self):
        vals = T.ini_values(P0)
        self.assertTrue(set(INI_FIELDS) <= set(vals))
        for key, field in INI_FIELDS.items():
            self.assertEqual(vals[key], getattr(P0, field))


def synthetic_points(p: T.SctmParams) -> list[Point]:
    pts = []
    for fz in (3200.0, 5000.0, 8000.0):
        for v in (10.0, 20.0, 30.0):
            for a in [x * 0.5 for x in range(-40, 41)]:
                pt = Point("lat", fz, math.radians(a), 0.0, 0.02, v, 0.0, 0.0)
                fy, fx = T.solve(p, fz, pt.slip_angle, 0.0, camber=0.02, speed=v)
                pts.append(Point("lat", fz, pt.slip_angle, 0.0, 0.02, v, fx, fy))
            for k in [x * 0.01 for x in range(-50, 51)]:
                fy, fx = T.solve(p, fz, 0.0, k, camber=0.02, speed=v)
                pts.append(Point("lon", fz, 0.0, k, 0.02, v, fx, fy))
    return pts


class TestFit(unittest.TestCase):
    def test_recovers_synthetic_parameters(self):
        pts = synthetic_points(P0)[::3]
        res = fit(pts, P0.fz0)
        for k, v in res.rms.items():
            self.assertLess(v, 2e-3, k)
        self.assertAlmostEqual(res.params.dy_ref, P0.dy_ref, delta=0.01)
        self.assertAlmostEqual(res.params.ls_expy, P0.ls_expy, delta=0.01)
        self.assertAlmostEqual(res.params.friction_limit_angle, 7.5, delta=0.1)
        self.assertAlmostEqual(res.params.cx_mult, 1.6, delta=0.03)
        self.assertAlmostEqual(res.params.camber_gain, 0.12, delta=0.02)

    def test_needs_both_sweeps(self):
        pts = [p for p in synthetic_points(P0) if p.kind == "lat"][:50]
        with self.assertRaises(ValueError):
            fit(pts, 5000.0)

    def test_dataset_columns_checked(self):
        with self.assertRaises(ValueError):
            points_from_dataset({"columns": [["x", "1"]], "rows": []})


def _row(seg, t, wheels, kload=0.0):
    r = {
        "seg": str(seg),
        "t": str(t),
        "Vcmd": "20",
        "betacmd": "0",
        "kload": str(kload),
    }
    for i, w in enumerate(wheels, start=1):
        for k, v in w.items():
            if len(k) == 2 and k[0] in "Spva" and k[1] in "xyz":
                r[f"{k[0]}{i}{k[1]}"] = str(v)  # cabecera del CSV: S1x, p1z, …
            else:
                r[f"{k}{i}"] = str(v)
    return r


class TestBenchReduction(unittest.TestCase):
    META = {
        "totalMass": 1000.0,
        "segments": [{"name": "settle", "dur": 4}],
        "wheels": [{"name": "FL", "axle": "front", "mass": 40.0}],
    }

    def wheel(self, sz=-4000.0, sy=500.0, vy=0.0):
        # eje (0,1,0) ⇒ rumbo h = (−1,0) o (1,0) según la velocidad; velocidad +x
        return {
            "Sx": 100.0,
            "Sy": sy,
            "Sz": sz,
            "px": 0,
            "py": 0,
            "pz": 0.34,
            "vx": 20.0,
            "vy": vy,
            "vz": 0,
            "ax": 0,
            "ay": 1,
            "az": 0,
            "om": 58.0,
            "T": 0,
            "k": 0,
            "zt": 0.0,
            "temp": 288.15,
            "pr": 300000,
            "df": 0,
        }

    def test_contact_force_and_frame(self):
        s = tb.reduce_rows(self.META, [_row(1, 0.1, [self.wheel()])])[0]
        self.assertAlmostEqual(s.fz, 4000.0 + 40.0 * tb.G)  # F_c = −S + m·g·ẑ
        self.assertAlmostEqual(s.fx, -100.0)  # h = +x (sentido de la velocidad)
        self.assertAlmostEqual(s.fy, -500.0)  # l = ẑ × h = +y
        self.assertAlmostEqual(s.hub_height, 0.34)
        s2 = tb.reduce_rows(self.META, [_row(1, 0.1, [self.wheel(vy=2.0)])])[0]
        self.assertAlmostEqual(s2.slip_angle, math.atan2(2.0, 20.0))
        self.assertAlmostEqual(s2.slip_ratio(20.0 / 58.0), 0.0, places=12)

    def test_lockin_recovers_stiffness_and_damping(self):
        k, c, m, f = 500000.0, 2000.0, 40.0, 12.0
        w = 2 * math.pi * f
        meta = {
            "totalMass": 1000.0,
            "wheels": [{"name": "FL", "axle": "front", "mass": m}],
            "segments": [{"name": "vosc_12hz", "dur": 2, "vosc": {"a": 0.2, "f": f}}],
        }
        rows = []
        for i in range(800):
            t = i * 0.0025
            h = 0.34 + 0.003 * math.sin(w * t + 0.3)
            hd = 0.003 * w * math.cos(w * t + 0.3)
            hdd = -(w**2) * (h - 0.34)
            fz_c = 5000.0 - k * (h - 0.34) - c * hd
            fz_meas = fz_c + m * hdd  # F_medida = F_c + m·z̈
            wd = self.wheel(sz=-(fz_meas - m * tb.G))
            wd["pz"], wd["zt"] = h, 0.0
            rows.append(_row(1, t, [wd]))
        out = tb.vertical_dynamic(meta, tb.reduce_rows(meta, rows), "vosc_12hz")["FL"]
        self.assertAlmostEqual(out["stiffness"], k, delta=1.0)
        self.assertAlmostEqual(out["damping"], c, delta=0.1)


class TestTransforms(unittest.TestCase):
    def test_rolling_resistance(self):
        v, notes = transforms.apply_detailed(
            "ac_rolling_resistance",
            {"table": ([[10, 0.008], [30, 0.006]], "table:m/s->1")},
            {},
            "1",
        )
        self.assertAlmostEqual(v, 7.0)
        self.assertTrue(notes)
        with self.assertRaises(transforms.TransformError):
            transforms.apply(
                "ac_rolling_resistance",
                {"table": ([[10, -0.001]], "table:m/s->1")},
                {},
                "1",
            )

    def test_sctm_fit_transform_checks(self):
        with self.assertRaises(transforms.TransformError):
            transforms.apply(
                "ac_sctm_fit",
                {"samples": ([], "table:deg->1"), "fz0": (5000, "N")},
                {"output": "DY_REF"},
                "1",
            )
        with self.assertRaises(transforms.TransformError):
            transforms.apply(
                "ac_sctm_fit",
                {"samples": ({}, "dataset"), "fz0": (5000, "N")},
                {"output": "NOPE"},
                "1",
            )

    def test_sctm_fit_transform_on_synthetic_dataset(self):
        rows = []
        for pt in synthetic_points(P0)[::6]:
            for wheel, cam in (("FL", -0.02), ("FR", 0.02)):
                rows.append(
                    [
                        wheel,
                        pt.kind,
                        "base",
                        1,
                        pt.fz,
                        math.degrees(pt.slip_angle),
                        pt.slip_ratio,
                        math.degrees(cam),
                        pt.speed,
                        pt.fx,
                        pt.fy,
                        0,
                    ]
                )
        data = {
            "columns": [
                [c, "x"]
                for c in (
                    "wheel",
                    "kind",
                    "condition",
                    "n",
                    "fz",
                    "slip_angle",
                    "slip_ratio",
                    "camber_axis",
                    "speed",
                    "fx",
                    "fy",
                    "pressure",
                )
            ],
            "rows": rows,
        }
        inp = {"samples": (data, "dataset"), "fz0": (5000.0, "N")}
        v, notes = transforms.apply_detailed(
            "ac_sctm_fit", inp, {"output": "DY_REF"}, "1"
        )
        self.assertAlmostEqual(v, 1.45, delta=0.01)
        self.assertIn("error en μ", notes[0])
        fla = transforms.apply(
            "ac_sctm_fit", inp, {"output": "FRICTION_LIMIT_ANGLE"}, "deg"
        )
        self.assertAlmostEqual(fla, 7.5, delta=0.1)
        with self.assertRaises(transforms.TransformError):  # unidad de salida
            transforms.apply(
                "ac_sctm_fit", inp, {"output": "FRICTION_LIMIT_ANGLE"}, "1"
            )
        with self.assertRaises(transforms.TransformError):  # tolerancia
            transforms.apply(
                "ac_sctm_fit", inp, {"output": "DY_REF", "max_rms": -1.0}, "1"
            )


class TestValidator(unittest.TestCase):
    def _write(self, text: str) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "tyres.ini").write_text(text, "utf-8")
        return d

    def test_version_below_10_not_checked(self):
        from src.ac.car_validate import tyre_model_problem

        self.assertIsNone(tyre_model_problem(self._write("[HEADER]\nVERSION=7\n")))

    def test_version_10_requires_keys(self):
        from src.ac.car_validate import SCTM_REQUIRED, tyre_model_problem

        body = "".join(f"{k}=1\n" for k in SCTM_REQUIRED)
        ok = tyre_model_problem(
            self._write(f"[HEADER]\nVERSION=10\n[FRONT]\n{body}[REAR]\n{body}")
        )
        self.assertTrue(ok[1])
        bad_body = body.replace("CX_MULT=1\n", "")
        bad = tyre_model_problem(
            self._write(f"[HEADER]\nVERSION=10\n[FRONT]\n{bad_body}[REAR]\n{body}")
        )
        self.assertFalse(bad[1])
        self.assertIn("CX_MULT", bad[2])


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------ integración
ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
LEGION = "2020_rpm_legion_grand_touring"
IBIZA_DIR = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
_RES: dict = {}


def _vdir(name: str) -> Path:
    return next(SAMPLES.glob(f"*/vehicles/{name}"))


def _available() -> bool:
    return SAMPLES.is_dir() and any(SAMPLES.glob(f"*/vehicles/{LEGION}"))


def _resolved(name: str):
    from src.attrs.report import resolve_vehicle
    from src.attrs.store import REPO_CATALOG, Catalog
    from src.importers.beamng import import_preview
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    if name not in _RES:
        if name == "ibiza":
            src = load_vehicle_dir(IBIZA_DIR, "35b84.pc")
            driver = 70.0
        else:
            src = load_vehicle_dir(_vdir(name), f"{name}.pc")
            driver = 0.0
        pv = preview_physics(src, driver)
        ir = import_preview(pv, driver)
        _, res = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", driver)
        _RES[name] = (ir, {r.target: r for r in res})
    return _RES[name]


@unittest.skipUnless(_available(), "sin samples_beamng")
class TestIntegration(unittest.TestCase):
    def test_ir_fields_from_measurement(self):
        ir, _ = _resolved(LEGION)
        ax = ir.tyres.axles["front"]
        self.assertEqual(ax.force_slip_samples.status, "present")
        self.assertEqual(ax.force_slip_samples.unit, "dataset")
        self.assertGreater(len(ax.force_slip_samples.value["rows"]), 1000)
        self.assertAlmostEqual(
            ax.free_rolling_radius.value, ax.radius.value, delta=0.002
        )
        self.assertGreater(ax.vertical_stiffness.value, 1e5)
        self.assertEqual(ax.rotational_inertia.kind, "computed")
        # no medidos: ausentes con motivo, nunca 0
        for p in (ax.vertical_damping, ax.relaxation_length):
            self.assertEqual(p.status, "missing")
            self.assertIsNone(p.value)
            self.assertTrue(p.reason)

    def test_rules_resolve_sctm_model(self):
        ir, res = _resolved(LEGION)
        self.assertEqual(res["ac:tyres.ini/HEADER/VERSION"].value, 10)
        for sec, axle in (("FRONT", "front"), ("REAR", "rear")):
            for key in INI_FIELDS:
                r = res[f"ac:tyres.ini/{sec}/{key}"]
                self.assertEqual(r.rule, f"tyre.{axle}.{key.lower()}_200409@1", key)
                self.assertTrue(any("error en μ" in a for a in r.assumptions), key)
            for key in ("DX0", "DY0", "DX1", "DY1", "XMU"):
                self.assertEqual(
                    res[f"ac:tyres.ini/{sec}/{key}"].status, "not_applicable"
                )
            # G2.20 lo dejaba «manual_required»; G2.25 (decisión del usuario) lo
            # resuelve con la mediana del corpus de AC (tyre.<eje>.damp_200409@1)
            self.assertEqual(
                res[f"ac:tyres.ini/{sec}/DAMP"].rule, f"tyre.{axle}.damp_200409@1"
            )
            self.assertEqual(res[f"ac:tyres.ini/{sec}/DAMP"].value, 500.0)
            fz0 = ir.get(f"tyres.axles.{axle}.reference_load").value
            self.assertAlmostEqual(res[f"ac:tyres.ini/{sec}/FZ0"].value, fz0)
            # traza: el dataset se resume con su huella, no se copia
            inp = res[f"ac:tyres.ini/{sec}/DY_REF"].inputs[0]["value"]
            self.assertIn("dataset_sha256", inp)
        self.assertTrue(1.0 < res["ac:tyres.ini/FRONT/DY_REF"].value < 2.0)

    def test_modified_source_has_no_tyre_measurement(self):
        import shutil

        from src.importers.beamng import import_preview
        from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "vehicles" / LEGION
            shutil.copytree(
                _vdir(LEGION), dst, ignore=shutil.ignore_patterns("*.dae", "*.dds")
            )
            f = dst / "wheels_front.jbeam"
            text = f.read_text("utf-8")
            self.assertIn('{"numRays":16}', text)
            f.write_text(text.replace('{"numRays":16}', '{"numRays":18}'), "utf-8")
            pv = preview_physics(load_vehicle_dir(dst, f"{LEGION}.pc"), 0.0)
            ir = import_preview(pv, 0.0)
            p = ir.get("tyres.axles.front.force_slip_samples")
            self.assertEqual(p.status, "missing")
            self.assertIn("huella", p.reason)

    def test_ibiza_untouched(self):
        _, res = _resolved("ibiza")
        self.assertEqual(res["ac:tyres.ini/FRONT/DY0"].source, "decision")
        self.assertEqual(res["ac:tyres.ini/FRONT/CX_MULT"].status, "not_applicable")
        self.assertNotEqual(res["ac:tyres.ini/HEADER/VERSION"].value, 10)


class TestTraceValue(unittest.TestCase):
    def test_dataset_summarized(self):
        from src.attrs.engine import _trace_value

        data = {"columns": [["a", "1"]], "rows": [[1], [2]]}
        v = _trace_value(data, "dataset")
        self.assertEqual(v["rows"], 2)
        self.assertEqual(len(v["dataset_sha256"]), 64)
        self.assertEqual(_trace_value([1, 2], "table:m/s->1"), [1, 2])
