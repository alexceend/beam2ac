"""G2.25: banco de validación funcional (circuito, app, preparación, batería, análisis).

Nada de esto lanza AC ni toca su instalación: la batería corre sobre el doble SINTÉTICO
(:mod:`tools.acbench.simac_g225`) y la preparación escribe en una carpeta temporal.
"""

from __future__ import annotations

import ctypes
import inspect
import math
import re
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from tools.acbench import app_g225, bridge_geo, install, track, track_g225
from tools.acbench.plan import VariantPlan
from tools.acbench.runner import CommLost
from tools.acbench.simac_g225 import SimAcG225
from tools.acbench import validate_analysis as VA
from tools.acbench.validate import TESTS, CarSpec, Heading, Validator, heading_of

KEYWORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def lua_block_balance(src: str) -> tuple[int, int]:
    """Aperturas (function, if, do, repeat) y cierres (end, until), sin cadenas ni
    comentarios."""
    src = re.sub(r"--\[\[.*?\]\]", "", src, flags=re.S)
    src = re.sub(r"--[^\n]*", "", src)
    src = re.sub(r"\[\[.*?\]\]", "''", src, flags=re.S)
    src = re.sub(r"'(\\.|[^'\\\n])*'", "''", src)
    src = re.sub(r'"(\\.|[^"\\\n])*"', '""', src)
    words = KEYWORD.findall(src)
    opens = sum(words.count(k) for k in ("function", "if", "do", "repeat"))
    closes = sum(words.count(k) for k in ("end", "until"))
    return opens, closes


def atlanta_spec() -> CarSpec:
    return CarSpec.from_package("beam2ac_g225_atlanta", app_g225.package_dir("atlanta"))


class TestTrack(unittest.TestCase):
    def test_kn5_walls_and_ground(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.kn5"
            p.write_bytes(track_g225.build_kn5())
            k = read_kn5(p)
        meshes = {n.name: k.mesh_world_vertices(n) for n in k.nodes if n.type == 2}
        self.assertEqual(set(meshes), {"1ROAD", "1WALL", "2WALL"})
        road = meshes["1ROAD"]
        self.assertAlmostEqual(min(v[0] for v in road), -600.0)
        self.assertAlmostEqual(max(v[2] for v in road), 600.0)
        a = meshes["1WALL"]
        self.assertAlmostEqual(min(v[2] for v in a), track_g225.WALL_A_Z, places=4)
        self.assertAlmostEqual(
            min(v[0] for v in meshes["2WALL"]), track_g225.WALL_B_X, places=4
        )

    def test_winding_matches_ground_convention(self):
        v, i = track_g225.box_faces((0, 0, 0), (1, 2, 3))
        self.assertTrue(track_g225.check_winding(v, i))
        ground = [
            (0, 0, 0, 0, 1, 0, 0, 0),
            (1, 0, 0, 0, 1, 0, 0, 0),
            (0, 0, 1, 0, 1, 0, 0, 0),
            (1, 0, 1, 0, 1, 0, 0, 0),
        ]
        self.assertTrue(
            track_g225.check_winding(ground, [0, 2, 1, 1, 2, 3])
        )  # orden del suelo de G2.22
        self.assertFalse(track_g225.check_winding(ground, [0, 1, 2]))

    def test_surfaces_keep_g222_permissions(self):
        s = track_g225.SURFACES_INI
        for key in (
            "KEY=ROAD",
            "WAV_PITCH=extended-0",
            "[_SCRIPTING_PHYSICS]",
            "ALLOW_CUSTOM_AI_MANIPULATION=1",
            "KEY=WALL",
            "[SURFACE_1]",
        ):
            self.assertIn(key, s)
        self.assertNotEqual(track_g225.TRACK_ID, track.TRACK_ID)


class TestApp(unittest.TestCase):
    def test_render_has_no_markers_and_balanced_blocks(self):
        lua = app_g225.render_lua()
        for m in ("@CMD_CDEF@", "@TEL_CDEF@", "@CAI_CDEF@", "@GEO_CDEF@"):
            self.assertNotIn(m, lua)
        o, c = lua_block_balance(lua)
        self.assertEqual(o, c)
        self.assertIn("local g222Update = script.update", lua)
        self.assertIn("Beam2acBench.Cmd.v1", lua)  # el puente de G2.22 sin cambios
        self.assertIn(bridge_geo.GEO_NAME, lua)

    def test_g222_template_balanced_too(self):
        o, c = lua_block_balance(install.render_lua())
        self.assertEqual(o, c)

    def test_balance_checker_detects_missing_end(self):
        self.assertNotEqual(*lua_block_balance("function f() if x then y() end"))

    def test_geo_layout_doubles_first(self):
        kinds = [t for _, t, _ in bridge_geo.GEO_FIELDS]
        self.assertEqual(kinds[:2], ["d", "d"])
        self.assertNotIn("d", kinds[2:])
        size = sum(
            {"d": 8, "i": 4, "f": 4, "c": 1}[t] * n for _, t, n in bridge_geo.GEO_FIELDS
        )
        self.assertEqual(ctypes.sizeof(bridge_geo.Geo), size)

    def test_g225_actions_do_not_collide_with_g222(self):
        from tools.acbench import bridge as B

        g222 = {v for k, v in vars(B).items() if k.startswith("ACT_")}
        g225 = {
            bridge_geo.ACT_CAPTURE,
            bridge_geo.ACT_GEO_DUMP,
            bridge_geo.ACT_GEO_LOG_OPEN,
            bridge_geo.ACT_GEO_LOG_CLOSE,
        }
        self.assertFalse(g222 & g225)


class TestStage(unittest.TestCase):
    def test_exports_match_sealed_manifest(self):
        for short in app_g225.CARS:
            self.assertTrue(app_g225.verify_export(short)["match"], short)

    def test_stage_copies_byte_identical_and_refuses_taken_targets(self):
        with tempfile.TemporaryDirectory() as d:
            fake_ac = Path(d) / "ac"
            (fake_ac / "content" / "cars" / "beam2ac_g225_atlanta").mkdir(parents=True)
            with self.assertRaises(install.InstallError):
                app_g225.stage(Path(d) / "prep0", fake_ac, ["atlanta"])
            m = app_g225.stage(Path(d) / "prep", None, ["atlanta"])
            cid = "content/cars/beam2ac_g225_atlanta"
            self.assertEqual(
                m["targets"][cid], install.tree_hashes(app_g225.package_dir("atlanta"))
            )
            self.assertIn(f"content/tracks/{track_g225.TRACK_ID}", m["targets"])
            self.assertIn("apps/lua/beam2ac_g225_bench", m["targets"])
            with self.assertRaises(install.InstallError):
                app_g225.stage(
                    Path(d) / "prep", None, ["atlanta"]
                )  # nunca se sobrescribe

    def test_install_generalisation_keeps_g222_defaults(self):
        sig = inspect.signature(install.configure_session)
        self.assertEqual(sig.parameters["track_id"].default, track.TRACK_ID)
        self.assertTrue(install._ours_extra("registros/a.csv"))
        self.assertTrue(install._ours_extra("capturas/x_geo.csv"))
        self.assertTrue(install._ours_extra("data/proview_nodes.ini"))
        self.assertFalse(install._ours_extra("data/car.ini"))

    def test_copy_app_outputs_keeps_capture_subpaths(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "app"
            (src / "registros").mkdir(parents=True)
            (src / "capturas").mkdir()
            (src / "registros" / "a.csv").write_text("x")
            (src / "capturas" / "b.png").write_bytes(b"\x89PNG")
            dst = Path(d) / "out"
            install._copy_app_outputs(src, ["registros/a.csv", "capturas/b.png"], dst)
            self.assertTrue((dst / "a.csv").is_file())
            self.assertTrue((dst / "capturas" / "b.png").is_file())


class TestSpec(unittest.TestCase):
    def test_atlanta_spec_reads_like_ac(self):
        s = atlanta_spec()
        self.assertEqual(s.limiter, 6500)
        self.assertEqual(s.minimum, 645)  # getInt de 645.527…
        self.assertEqual(len(s.gears), 7)
        self.assertAlmostEqual(s.graphics_offset[1], -0.4863)
        # τ = I·(RPM − MIN)·π/(30·TORQUE)
        self.assertAlmostEqual(
            s.tau_pred_s(),
            0.185253991138 * (6500 - 645) * math.pi / (30 * 78.3),
            places=6,
        )

    def test_gear_for(self):
        s = atlanta_spec()
        g = s.gear_for(60.0)
        self.assertGreaterEqual(s.rpm_at(60.0, g), 2500.0)
        if g < len(s.gears):
            self.assertLess(s.rpm_at(60.0, g + 1), 2500.0)


class TestControl(unittest.TestCase):
    def test_heading_sign_convention(self):
        # rumbo deseado a la izquierda (+X): girar a la izq. ⇒ volante negativo (F0)
        h = Heading(math.radians(10))
        tel = {"look": [0.0, 0.0, 1.0], "local_ang_vel": [0, 0, 0], "pos": [0, 0, 0]}
        self.assertLess(h.steer(tel, 0.01), 0.0)
        self.assertGreater(
            Heading(math.radians(10)).steer(tel, 0.01, reverse=True), 0.0
        )
        self.assertAlmostEqual(heading_of([1.0, 0.0, 0.0]), math.pi / 2)


def run_sim(reps=1, **sim_kw) -> tuple[Validator, SimAcG225]:
    spec = atlanta_spec()
    sim = SimAcG225(geo=spec.geometry(), **sim_kw)
    sim.gear = 0
    out = Path(tempfile.mkdtemp())
    v = Validator(
        io=sim,
        plan=VariantPlan("SINTETICO", []),
        variant=None,
        out=out,
        run_id="t",
        geo=spec.geometry(),
        spec=spec,
        reps=reps,
    )
    return v, sim


class TestBatterySynthetic(unittest.TestCase):
    def test_full_battery_valid_and_walls_measured(self):
        v, sim = run_sim()
        trials = v.run_tests(TESTS)
        bad = [(t.test, t.item, t.reasons) for t in trials if not t.valid]
        self.assertEqual(bad, [])
        items = {(t.test, t.item) for t in trials}
        for key in ("G150+", "G150-", "A60+", "A60-", "C20+", "C20-"):
            self.assertIn(("curves", key), items)
        kn5 = {
            "collider_box": {
                "min": [-sim.half_width, 0.1, -sim.rear],
                "max": [sim.half_width, 1.2, sim.front],
            }
        }
        for t in trials:
            if t.test == "walls":
                m = VA.m_wall({"item": t.item, "info": t.info}, kn5)
                self.assertLess(abs(m["error_modelo_m"]), 0.03, (t.item, m))
        self.assertIn(13, sim.geo_actions)  # capturas
        self.assertIn(14, sim.geo_actions)  # volcado
        self.assertTrue(list(v.out.glob("*.trials.json")))

    def test_frozen_telemetry_aborts(self):
        v, sim = run_sim(freeze_at=5.0)
        with self.assertRaises(CommLost):
            v.run_tests(("engine",))
        self.assertGreater(sim.safe_stops, 0)

    def test_collision_invalidates_and_repeats(self):
        v, sim = run_sim(reps=1, collide_at=None)
        sim.wall_hit = False
        orig = sim._tel
        state = {"n": 0}

        # contacto carrocería–pista en las primeras lecturas de una ventana de medida
        def tel_with_hit():
            t = orig()
            if sim.seg[1] == 1:
                state["n"] += 1
                if 20 < state["n"] < 25:
                    t["collided_with"] = 0
            return t

        sim._tel = tel_with_hit
        v.run_tests(("engine",))
        eng = [t for t in v.trials if t.test == "engine"]
        self.assertGreaterEqual(len(eng), 2)
        self.assertIn("colision_carroceria_pista", eng[0].reasons)
        self.assertTrue(eng[-1].valid)


class TestAnalysis(unittest.TestCase):
    SPEC = {
        "minimum": 645,
        "coast_rpm": 6500.0,
        "coast_torque": 78.3,
        "tau_pred_s": 1.45,
        "limiter": 6500,
        "inertia": 0.185,
        "gears": [6.75, 4.08, 2.75, 1.95],
        "final": 3.38,
        "tyre_radius": 0.325,
        "graphics_offset": [0, -0.4863, -0.2783],
        "totalmass": 1671.0,
        "steer_ratio": 14.88,
        "steer_lock": 450.0,
        "wheelbase": 2.814,
    }

    def _run(self, rows, trial):
        return VA.Run("c", "r", Path("."), {"trials": [trial]}, rows)

    def test_tau_fit_recovers_exponential(self):
        tau, mn = 1.6, 645
        rows = [
            {
                "seg_uid": 9,
                "seg_meas": 1,
                "phys_ms": t * 5.0,
                "rpm": mn + 5800 * math.exp(-(t * 0.005) / tau),
                "speed_kmh": 0.0,
            }
            for t in range(1600)
        ]
        tr = {"seg_windows": {"caida": 9}}
        m = VA.m_engine(self._run(rows, tr), tr, self.SPEC)
        self.assertAlmostEqual(m["tau_medida_s"], tau, places=3)
        self.assertAlmostEqual(m["tau_ratio"], tau / 1.45, places=3)
        self.assertLess(m["t_hasta_MIN_mas_300_s"], 8.0)

    def test_no_coast_ref_gives_no_fall(self):
        rows = [
            {
                "seg_uid": 9,
                "seg_meas": 1,
                "phys_ms": t * 5.0,
                "rpm": 6400.0,
                "speed_kmh": 0.0,
            }
            for t in range(1600)
        ]
        tr = {"seg_windows": {"caida": 9}}
        m = VA.m_engine(self._run(rows, tr), tr, self.SPEC)
        self.assertTrue(math.isnan(m["tau_medida_s"]))
        self.assertTrue(math.isnan(m["t_hasta_MIN_mas_300_s"]))

    def test_model_to_world_axes(self):
        # +X del modelo = izquierda = up × look
        p = VA.model_to_world((1, 0, 0), (0, 0, 0), (0, 0, 1), (0, 1, 0))
        self.assertEqual(tuple(round(x, 9) for x in p), (1, 0, 0))
        p = VA.model_to_world(
            (1, 0, 0), (0, 0, 0), (1, 0, 0), (0, 1, 0)
        )  # mirando a +X ⇒ izquierda = −Z
        self.assertEqual(tuple(round(x, 9) for x in p), (0, 0, -1))

    @staticmethod
    def _res(fails: list[tuple[str, str, str]], run="r1") -> dict:
        crit = [
            {"grupo": g, "criterio": c, "ok": False, "nivel": n, "valor": None}
            for g, c, n in fails
        ]
        crit.append(
            {
                "grupo": "A_carga",
                "criterio": "x",
                "ok": True,
                "nivel": "primario",
                "valor": None,
            }
        )
        return {
            "run_id": run,
            "criterios": crit,
            "static": {},
            "motor": [],
            "muros": {},
            "pass_hi": [],
            "curvas": {},
        }

    def test_classification(self):
        tol = {"relativa_por_defecto": {"rel": 0.15}}
        ok = self._res([])
        self.assertEqual(
            VA.classify([ok], {"a": "sin_anomalias"}, tol)["estado"],
            "ready_for_further_testing",
        )
        self.assertEqual(
            VA.classify([ok, self._res([], "r2")], {"a": "sin_anomalias"}, tol)[
                "estado"
            ],
            "validated_in_ac",
        )
        self.assertEqual(
            VA.classify([ok, self._res([], "r2")], None, tol)["estado"],
            "ready_for_further_testing",
        )
        geo = self._res([("A_geometria", "ruedas_dy", "primario")])
        self.assertEqual(VA.classify([geo], {}, tol)["estado"], "geometry_failed")
        phys = self._res([("C_motor", "rpm_bajan_al_soltar", "primario")])
        self.assertEqual(VA.classify([phys, phys], {}, tol)["estado"], "physics_failed")
        self.assertEqual(VA.classify([phys, ok], {}, tol)["estado"], "partial")
        sec = self._res([("C_motor", "tau_ley_acs", "secundario")])
        self.assertEqual(
            VA.classify([sec, sec], {"a": "sin_anomalias"}, tol)["estado"],
            "ready_for_further_testing",
        )
        load = self._res([("A_carga", "telemetria_y_sesion", "primario")])
        load["criterios"] = [c for c in load["criterios"] if c["criterio"] != "x"]
        self.assertEqual(VA.classify([load], {}, tol)["estado"], "load_failed")

    def test_reproducibility(self):
        a = {
            "motor": [{"tau_ratio": 1.0}],
            "static": {},
            "muros": {"frontal": [{"extension_modelo_m": 2.28}]},
            "pass_hi": [],
            "curvas": {},
        }
        b = {
            "motor": [{"tau_ratio": 1.2}],
            "static": {},
            "muros": {"frontal": [{"extension_modelo_m": 2.29}]},
            "pass_hi": [],
            "curvas": {},
        }
        tol = {
            "tau_ratio": {"rel": 0.10},
            "muro_": {"abs": 0.03},
            "relativa_por_defecto": {"rel": 0.15},
        }
        r = VA.reproducibility([a, b], tol)
        self.assertFalse(r["metricas"]["tau_ratio"]["ok"])
        self.assertTrue(r["metricas"]["muro_frontal"]["ok"])
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()


class TestBenchV2Fixes(unittest.TestCase):
    def test_wall_winding_modes(self):
        idx = [0, 1, 2]
        self.assertEqual(track_g225.wall_indices(idx), [0, 2, 1])  # banco v4: inverso
        old = track_g225.WALL_WINDING
        try:
            track_g225.WALL_WINDING = "doble"
            self.assertEqual(track_g225.wall_indices(idx), [0, 1, 2, 0, 2, 1])
        finally:
            track_g225.WALL_WINDING = old

    def _walls_double_sided(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.kn5"
            p.write_bytes(track_g225.build_kn5())
            k = read_kn5(p)
        walls = [n for n in k.nodes if n.type == 2 and n.name.endswith("WALL")]
        for w in walls:
            tris = {tuple(w.indices[i : i + 3]) for i in range(0, len(w.indices), 3)}
            self.assertEqual(len(w.indices), 72)
            for a, b, c in list(tris):
                self.assertIn((a, c, b), tris)

    def test_teleport_passes_opposite_dir(self):
        v, sim = run_sim()
        self.assertTrue(v.place(10.0, 20.0, math.radians(30)))
        self.assertAlmostEqual(sim.psi, math.radians(30), places=6)
