"""G2.22: banco LSD autónomo — estructuras, puente, instalación, análisis corregido
y máquina
de estados (con el doble SINTÉTICO ``tools.acbench.simac``; nada de esto prueba AC: las
pruebas en el juego están en ``output/g222_2026-11-07/``)."""

from __future__ import annotations

import csv
import ctypes
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

from tools.acbench import analysis as A
from tools.acbench import bridge as B
from tools.acbench import cai, install, shm, track
from tools.acbench.control import YAW_SIGN, CarGeometry, Circle, online_saturation
from tools.acbench.plan import PLANS, Item, VariantPlan, plan_for
from tools.acbench.runner import CommLost, Runner
from tools.acbench.simac import SimAc

ROOT = Path(__file__).resolve().parents[1]


class TestCustomAiStructs(unittest.TestCase):
    def test_sizes_match_documentation(self):
        for k, v in cai.EXPECTED_SIZES.items():
            self.assertEqual(cai.SIZES[k], v, k)

    def test_controls_offsets(self):
        self.assertEqual(cai.CarControls.teleport_pos.offset, 44)
        self.assertEqual(cai.CarControls.teleport_dir.offset, 56)
        self.assertEqual(cai.CarControls.autoshift_active.offset, 68)

    def test_initial_controls_brake(self):
        c = cai.initial_controls()
        self.assertEqual((c.gas, c.brake), (0.0, 1.0))
        self.assertEqual(len(cai.to_bytes(c)), 72)


class TestBridge(unittest.TestCase):
    def test_lua_cdef_has_every_field(self):
        for fields in (B.CMD_FIELDS, B.TEL_FIELDS):
            cdef = B.lua_cdef(fields)
            self.assertEqual(cdef.count(";"), len(fields))

    def test_doubles_first(self):
        kinds = [t for _, t, _ in B.TEL_FIELDS]
        self.assertEqual(kinds[: kinds.count("d")], ["d"] * kinds.count("d"))

    def test_template_placeholders_rendered(self):
        lua = install.render_lua()
        self.assertNotIn("@CMD_CDEF@", lua)
        self.assertNotIn("@TEL_CDEF@", lua)
        self.assertIn(B.lua_cdef(B.TEL_FIELDS), lua)
        self.assertIn("float teleport_pos[3];", lua)

    def test_roundtrip_memory_registry(self):
        reg = shm.MemoryRegistry()
        br = B.Bridge(reg)
        self.assertIsNone(br.telemetry())
        t = B.Tel()
        t.magic, t.version, t.struct_size, t.frame, t.gear = (
            B.MAGIC_TEL,
            B.VERSION,
            ctypes.sizeof(B.Tel),
            7,
            2,
        )
        t.car_id = b"beam2ac_g221lsd_a1"
        reg.create(B.TEL_NAME, ctypes.sizeof(B.Tel)).write(
            bytes(memoryview(t).cast("B"))
        )
        tel = br.telemetry()
        self.assertEqual(
            (tel["frame"], tel["gear"], tel["car_id"]), (7, 2, "beam2ac_g221lsd_a1")
        )
        seq = br.request(B.ACT_TELEPORT, pos=(1, 2, 3))
        cmd = B.Cmd.from_buffer_copy(
            reg.open_existing(B.CMD_NAME, ctypes.sizeof(B.Cmd)).read()
        )
        self.assertEqual(
            (cmd.action, cmd.action_seq, list(cmd.pos)),
            (B.ACT_TELEPORT, seq, [1, 2, 3]),
        )

    def test_size_mismatch_detected(self):
        reg = shm.MemoryRegistry()
        br = B.Bridge(reg)
        t = B.Tel()
        t.magic, t.version, t.struct_size = B.MAGIC_TEL, B.VERSION, 8
        reg.create(B.TEL_NAME, ctypes.sizeof(B.Tel)).write(
            bytes(memoryview(t).cast("B"))
        )
        with self.assertRaises(RuntimeError):
            br.telemetry()


class TestInstallHelpers(unittest.TestCase):
    def test_set_ini_preserves_and_adds(self):
        t = "[RACE]\nMODEL=x\nTRACK=drift\n\n[OTHER]\nA=1\n"
        out = install._set_ini(t, "RACE", {"TRACK": "pad", "CARS": "1"})
        self.assertIn("TRACK=pad", out)
        self.assertIn("CARS=1", out)
        self.assertIn("MODEL=x", out)
        self.assertIn("[OTHER]\nA=1", out)
        out2 = install._set_ini(out, "NEW", {"K": "V"})
        self.assertTrue(out2.rstrip().endswith("[NEW]\nK=V"))

    def test_game_generated_is_tolerated(self):
        self.assertTrue(install._ours_extra("data/proview_nodes.ini"))
        self.assertTrue(install._ours_extra("registros/x.csv"))
        self.assertFalse(install._ours_extra("data/car.ini"))


class TestTrack(unittest.TestCase):
    def test_kn5_parses_with_spawns(self):
        from src.ac.kn5_inspect import read_kn5

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.kn5"
            p.write_bytes(track.build_kn5())
            k = read_kn5(p)
        names = [n.name for n in k.nodes]
        for s in ("1ROAD", "AC_PIT_0", "AC_START_0", "AC_HOTLAP_START_0"):
            self.assertIn(s, names)
        road = k.find("1ROAD")[0]
        ys = {v[1] for v in road.vertices}
        self.assertEqual(ys, {0.0})

    def test_surfaces_permissions(self):
        self.assertIn("ALLOW_CUSTOM_AI_MANIPULATION=1", track.SURFACES_INI)
        self.assertIn("WAV_PITCH=extended-0", track.SURFACES_INI)


class TestControl(unittest.TestCase):
    def test_feedforward(self):
        g = CarGeometry()
        self.assertAlmostEqual(
            g.steer_ff(12.0), math.degrees(math.atan(2.50033 / 12)) * 12.14 / 423, 9
        )

    def test_circle_sign(self):
        c = Circle(CarGeometry(), 12.0, 1.0)
        u = c.steer(10.0, YAW_SIGN * 10.0 / 12.0, 0.01)  # ya en el objetivo
        self.assertAlmostEqual(u, CarGeometry().steer_ff(12.0), 6)

    def test_step_stable_saturation(self):
        tel = {"shaftv": [10.6, 10.0, 10.0, 10.0]}
        self.assertTrue(online_saturation(tel, "front", 50.0))  # paso 0,11 rad/s
        self.assertFalse(
            online_saturation(tel, "front", 1000.0)
        )  # paso 2,2 rad/s ⇒ exige 4,4


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _row(t_ms, seg, meas, lim_axle, clutch=1.0, dw=1.0):
    """Fila sintética de A1 (precarga del. 200): eje delantero saturado,
    X = lim·sat."""
    r = {
        "sim_ms": t_ms,
        "phys_ms": t_ms,
        "segment": seg,
        "seg_uid": seg,
        "seg_meas": meas,
        "gear": 0,
        "gas": 0,
        "brake": 0,
        "handbrake": 0,
        "clutch": clutch,
        "rpm": 1400,
        "speed_kmh": 30,
        "drivetrain_torque": 0,
        "gear_ratio": 0,
        "final_ratio": 5.98,
        "clutch_inertia": 1,
        "engine_inertia": 0.1,
        "drivetrain_inertia": 0.01,
    }
    sat = dw / (abs(dw) + A.WINDOW["AWD"])
    fb = [lim_axle * sat, 0.0, 0.0, 0.0]
    for i in range(4):
        r.update(
            {
                f"shaftv{i}": 30.0 + (dw if i == 0 else 0.0),
                f"shafti{i}": 1.34984994,
                f"ang{i}": 30.0,
                f"angi{i}": 1.4648,
                f"fbt{i}": fb[i],
                f"load{i}": 3500.0,
            }
        )
    return r


class TestAnalysis(unittest.TestCase):
    def test_blocks_by_segment_and_meas_window(self):
        if "beam2ac_g221lsd_a1" not in A.BY_ID:
            self.skipTest("variantes de G2.21 no disponibles (output/ local)")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rows = []
            t = 0.0
            for seg in range(1, 7):
                for _ in range(40):
                    t += 6.0
                    rows.append(_row(t, seg, 1, 200.0))
                for _ in range(10):  # fuera de la ventana: no cuenta
                    t += 6.0
                    rows.append(_row(t, seg, 0, 9999.0))
                t += 2000.0
            name = "beam2ac_g221lsd_a1_SINTETICO.csv"
            _write_csv(d / name, rows)
            segs = [{"seg_uid": s, "valid": s != 6} for s in range(1, 7)]
            (d / "beam2ac_g221lsd_a1_SINTETICO.segments.json").write_text(
                json.dumps({"run_id": "x", "segments": segs})
            )
            res = A.analyse_folders([d])
        p = res["params"]["A1.front.preload"]
        self.assertEqual(p["blocks"], 5)  # la repetición 6 se declaró inválida
        self.assertAlmostEqual(p["rho_median"], 1.0, 6)
        self.assertEqual(p["status"], "coincide_lectura_C")
        self.assertTrue(res["synthetic"])
        self.assertEqual(A.verdict(res)["source"], "sintetico")

    def test_clutch_engaged_is_one(self):
        self.assertEqual(A.CLUTCH_ENGAGED, 0.99)

    def test_fwd_uses_angular_speed(self):
        self.assertEqual(A.wheel_cols("FWD", {"shafti0": 0.0}), ("ang", "angi"))
        self.assertEqual(A.wheel_cols("AWD", {"shafti0": 1.35}), ("shaftv", "shafti"))


class TestRunnerSynthetic(unittest.TestCase):
    def _runner(self, sim, items, tmp):
        if "beam2ac_g221lsd_a1" not in A.BY_ID:
            self.skipTest("variantes de G2.21 no disponibles (output/ local)")
        variant = A.BY_ID["beam2ac_g221lsd_a1"]
        return Runner(
            sim,
            VariantPlan("beam2ac_g221lsd_a1", items),
            variant,
            Path(tmp),
            "SINTETICO",
        )

    def test_completes_neutral_items(self):
        sim = SimAc()
        items = [
            Item(
                "N+", "N", 1.0, radius=12.0, v_start_kmh=40.0, target=2, max_attempts=4
            )
        ]
        with tempfile.TemporaryDirectory() as d:
            reps = self._runner(sim, items, d).run()
            seg_file = json.loads(
                (Path(d) / "beam2ac_g221lsd_a1_SINTETICO.segments.json").read_text(
                    "utf-8"
                )
            )
        self.assertGreaterEqual(sum(r.saturated for r in reps), 2)
        self.assertTrue(all(r.valid for r in reps), [r.reasons for r in reps])
        self.assertEqual(len({r.seg_uid for r in reps}), len(reps))
        self.assertEqual(seg_file["state"], "FIN")
        self.assertIn(B.ACT_OPEN_LOG, sim.actions)
        self.assertIn(B.ACT_CLOSE_LOG, sim.actions)
        self.assertGreaterEqual(sim.safe_stops, 1)

    def test_collision_invalidates_and_retries(self):
        sim = SimAc(collide_at=20.0)
        items = [
            Item(
                "D+",
                "D",
                1.0,
                v_start_kmh=22.0,
                gas=0.3,
                t_max_s=8.0,
                target=2,
                max_attempts=5,
            )
        ]
        with tempfile.TemporaryDirectory() as d:
            reps = self._runner(sim, items, d).run()
        bad = [r for r in reps if not r.valid]
        self.assertTrue(any("colision" in r.reasons for r in bad) or reps[0].valid)
        self.assertGreaterEqual(sum(r.saturated for r in reps), 2)

    def test_watchdog_aborts_on_frozen_telemetry(self):
        sim = SimAc(freeze_at=5.0)
        items = [Item("N+", "N", 1.0, target=3, max_attempts=3)]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CommLost):
                self._runner(sim, items, d).run()
        self.assertGreaterEqual(sim.safe_stops, 1)
        self.assertEqual(sim.controls["gas"], 0.0)

    def test_contact_loss_invalidates(self):
        sim = SimAc(airborne_at=(0.0, 1e9))
        items = [Item("C+", "C", 1.0, v_start_kmh=40.0, target=1, max_attempts=2)]
        with tempfile.TemporaryDirectory() as d:
            reps = self._runner(sim, items, d).run()
        self.assertTrue(
            all("perdida_de_contacto" in r.reasons for r in reps if r.frames)
        )


class TestPlans(unittest.TestCase):
    def test_every_variant_planned_and_measured(self):
        for cid, v in A.BY_ID.items():
            self.assertIn(cid, PLANS)
            diffs = {d for it in PLANS[cid] for d in it.diffs}
            for m in v.measures:
                diff, param = m.split(".")
                if param != "none":
                    self.assertIn(diff, diffs | {"front", "rear"}, (cid, m))

    def test_scale(self):
        p = plan_for("beam2ac_g221lsd_a1", 0.25)
        self.assertTrue(all(it.target >= 1 for it in p.items))


class TestRepeatability(unittest.TestCase):
    def test_combine_requires_both_and_close_medians(self):
        from tools.acbench.repeat import combine

        a = {
            "params": {
                "P1": {"status": "coincide_lectura_C", "rho_median": 1.0},
                "P2": {"status": "coincide_lectura_C", "rho_median": 1.0},
                "P3": {"status": "coincide_lectura_C", "rho_median": 1.0},
            }
        }
        b = {
            "params": {
                "P1": {"status": "coincide_lectura_C", "rho_median": 0.97},
                "P2": {"status": "insuficiente"},
                "P3": {"status": "coincide_lectura_C", "rho_median": 0.90},
            }
        }
        params, _ = combine(a, b, {}, {})
        self.assertEqual(
            params,
            {"P1": "coincide_lectura_C", "P2": "no_repetido", "P3": "no_repetido"},
        )


class TestSessionLock(unittest.TestCase):
    def test_second_session_refused(self):
        from tools.acbench.session import AcSession

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            a = AcSession(
                "x",
                d / "estado",
                d,
                d / "a",
                registry=shm.MemoryRegistry(),
                launch=False,
            )
            b = AcSession(
                "x",
                d / "estado",
                d,
                d / "b",
                registry=shm.MemoryRegistry(),
                launch=False,
            )
            a._lock()
            with self.assertRaises(RuntimeError):
                b._lock()
            a._unlock()
            b._lock()
            b._unlock()


class TestNoProductionImport(unittest.TestCase):
    def test_src_does_not_import_acbench(self):
        for f in (ROOT / "src").rglob("*.py"):
            self.assertIsNone(
                re.search(r"tools\.acbench", f.read_text("utf-8", errors="replace")), f
            )


if __name__ == "__main__":
    unittest.main()
