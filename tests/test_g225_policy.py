"""G2.25 (auditoría de r01): clasificación, reintentos, aborto y protocolo mínimo.

Validan la LÓGICA del banco con datos construidos o con el doble sintético; no dicen
nada de cómo se comporta un coche en AC.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from src.ac.kn5_inspect import read_kn5
from tools.acbench import policy as P
from tools.acbench import track_g225
from tools.acbench.plan import VariantPlan
from tools.acbench.simac_g225 import SimAcG225
from tools.acbench.validate import (
    PAD_LIMIT,
    PROTOCOLS,
    CarSpec,
    Validator,
)
from tools.acbench import app_g225

W = track_g225.WALLS


class TestClassify(unittest.TestCase):
    def c(self, reasons, pos=(0.0, 0.0, 0.0)):
        return P.classify(list(reasons), list(pos), W, PAD_LIMIT)[0]

    def test_valid(self):
        self.assertEqual(self.c([]), P.VALID)

    def test_contact_next_to_wall_is_track(self):
        # rueda sin carga con el coche a 1 m de la cara del muro A
        self.assertEqual(self.c(["perdida_de_contacto"], (300.0, 0.0, 399.0)), P.TRACK)
        self.assertEqual(
            self.c(["colision_carroceria_pista"], (449.0, 0.0, 100.0)), P.TRACK
        )

    def test_same_contact_far_from_walls_is_vehicle(self):
        self.assertEqual(self.c(["perdida_de_contacto"], (0.0, 0.0, 0.0)), P.VEHICLE)
        self.assertEqual(
            self.c(["colision_carroceria_pista"], (-300.0, 0.0, -400.0)), P.VEHICLE
        )

    def test_controller_and_telemetry(self):
        self.assertEqual(self.c(["no_establece"]), P.CONTROLLER)
        self.assertEqual(self.c(["no_recoloca"]), P.CONTROLLER)
        self.assertEqual(self.c(["tiempo_maximo_aceleracion"]), P.CONTROLLER)
        self.assertEqual(
            self.c(["fuera_de_explanada"], (0.0, 0.0, 595.0)), P.CONTROLLER
        )
        self.assertEqual(self.c(["vigilante_lua", "trompo"]), P.TELEMETRY)
        self.assertEqual(self.c(["excepcion: RuntimeError()"]), P.TELEMETRY)

    def test_vehicle(self):
        for r in (
            ["trompo"],
            ["se_mueve"],
            ["lectura_invalida"],
            ["trompo", "fuera_de_explanada"],
        ):
            self.assertEqual(self.c(r), P.VEHICLE, r)

    def test_nan_position_does_not_crash(self):
        self.assertEqual(
            self.c(["perdida_de_contacto"], (math.nan, math.nan, math.nan)), P.VEHICLE
        )

    def test_unclassified(self):
        self.assertEqual(self.c(["ventana_corta"]), P.INVALID)

    def test_r01_cases(self):
        # r01: pasada fuera de la explanada tras salir girada 180° (sin trompo)
        self.assertEqual(
            self.c(["fuera_de_explanada"], (-280.0, 0.0, -585.0)), P.CONTROLLER
        )
        # r01: ensayo de muro con rueda sin carga en la cara del muro → pista
        self.assertEqual(self.c(["perdida_de_contacto"], (300.0, 0.0, 398.5)), P.TRACK)


class TestRetryPolicy(unittest.TestCase):
    def test_three_valid_end(self):
        p = P.RetryPolicy(reps=3)
        p.start_maneuver()
        self.assertEqual(
            [p.decide(P.VALID, []) for _ in range(3)], ["otra", "otra", "fin"]
        )

    def test_vehicle_failure_confirmed_once_then_stop(self):
        p = P.RetryPolicy(reps=3, abort_min_trials=100)
        p.start_maneuver()
        self.assertEqual(p.decide(P.VEHICLE, ["trompo"]), "otra")
        self.assertEqual(p.decide(P.VEHICLE, ["trompo"]), "fin")
        self.assertEqual(p.history[-1]["why"], "fallo del coche reproducido")

    def test_same_external_failure_not_retried_without_new_condition(self):
        p = P.RetryPolicy(reps=3, abort_min_trials=100)
        p.start_maneuver()
        self.assertEqual(p.decide(P.CONTROLLER, ["no_establece"]), "otra")
        self.assertEqual(p.decide(P.CONTROLLER, ["no_establece"]), "fin")
        p.start_maneuver()
        self.assertEqual(p.decide(P.CONTROLLER, ["no_establece"]), "otra")
        self.assertEqual(
            p.decide(P.CONTROLLER, ["no_establece"], recovered=True), "otra"
        )

    def test_external_retries_bounded(self):
        p = P.RetryPolicy(reps=3, external_retries=2, abort_min_trials=100)
        p.start_maneuver()
        out = [p.decide(P.TRACK, [f"x{i}"]) for i in range(3)]
        self.assertEqual(out, ["otra", "otra", "fin"])

    def test_attempt_cap(self):
        p = P.RetryPolicy(reps=3, abort_min_trials=100)
        p.start_maneuver()
        seq = [P.VALID, P.TRACK, P.VALID, P.CONTROLLER, P.VEHICLE, P.VALID]
        res = [p.decide(o, [str(i)]) for i, o in enumerate(seq)]
        self.assertIn("fin", res)
        self.assertLessEqual(p.attempts, 3 + 2 + 1)

    def test_session_abort_on_invalid_rate(self):
        p = P.RetryPolicy(reps=3, abort_invalid_rate=0.5, abort_min_trials=6)
        res = []
        for i in range(6):
            p.start_maneuver()
            res.append(p.decide(P.TRACK if i % 3 else P.VALID, [f"m{i}"]))
        self.assertEqual(res[-1], "abortar")

    def test_no_abort_below_min_trials(self):
        p = P.RetryPolicy(abort_invalid_rate=0.5, abort_min_trials=6)
        p.start_maneuver()
        self.assertNotEqual(p.decide(P.TRACK, ["a"]), "abortar")


class TestWallsGeometry(unittest.TestCase):
    def test_wall_positions_in_generated_kn5(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.kn5"
            p.write_bytes(track_g225.build_kn5())
            k = read_kn5(p)
        boxes = {}
        for n in k.nodes:
            if n.type == 2 and n.name.endswith("WALL"):
                v = k.mesh_world_vertices(n)
                boxes[k.nodes[n.parent].name] = (
                    tuple(min(q[i] for q in v) for i in range(3)),
                    tuple(max(q[i] for q in v) for i in range(3)),
                )
        for name, (lo, hi) in W.items():
            self.assertEqual(tuple(round(x, 4) for x in boxes[name][0]), lo)
            self.assertEqual(tuple(round(x, 4) for x in boxes[name][1]), hi)

    def test_maneuvers_keep_margin_from_walls(self):
        # recta (x = −300, z −550 … 550) y círculos del protocolo (centro en ±R)
        for z in range(-550, 551, 10):
            self.assertIsNone(
                P.near_track_element(
                    [track_g225.LANE_X, 0, z], W, PAD_LIMIT, margin=50.0
                )
            )
        for r in (150.0, 60.0):
            for d in (1.0, -1.0):
                cx = d * r
                for k in range(0, 360, 5):
                    x = cx - d * r * math.cos(math.radians(k))
                    z = r * math.sin(math.radians(k))
                    self.assertIsNone(
                        P.near_track_element([x, 0, z], W, PAD_LIMIT, margin=50.0),
                        (r, d, k),
                    )


def run_sim(protocol: str, **kw):
    spec = CarSpec.from_package("SINTETICO", app_g225.package_dir("atlanta"))
    sim = SimAcG225(geo=spec.geometry(), **kw)
    sim.gear = 0
    v = Validator(
        io=sim,
        plan=VariantPlan("SINTETICO", []),
        variant=None,
        out=Path(tempfile.mkdtemp()),
        run_id="t",
        geo=spec.geometry(),
        spec=spec,
        reps=3,
        protocol=protocol,
        policy=P.RetryPolicy(reps=3),
    )
    return v, sim


class TestMinimalProtocolSynthetic(unittest.TestCase):
    def test_minimal_protocol_runs_and_chains_curves(self):
        v, sim = run_sim("minimo")
        trials = v.run_tests(PROTOCOLS["minimo"])
        self.assertEqual(
            [(t.test, t.item, t.reasons) for t in trials if not t.valid], []
        )
        self.assertEqual({t.outcome for t in trials}, {P.VALID})
        curves = [t for t in trials if t.test == "curves"]
        self.assertEqual(len(curves), 3 + 1 + 3 + 1)  # 3 en + y 1 en − por curva
        self.assertEqual(sum(1 for t in curves if t.info.get("encadenada")), 4)
        passes = [t for t in trials if t.test == "pass"]
        self.assertEqual(len(passes), 3)
        self.assertIn("gas0_marcha_bajas", passes[0].seg_windows)
        self.assertNotIn("walls", {t.test for t in trials})
        self.assertLess(len(trials), 20)

    def test_session_aborts_when_most_trials_fail(self):
        v, sim = run_sim("minimo")
        orig = sim._tel

        def broken():  # pérdida de contacto permanente lejos de los muros
            t = orig()
            t["load"] = [0.0] * 4
            return t

        sim._tel = broken
        v.policy.abort_min_trials = 4
        v.run_tests(PROTOCOLS["minimo"])
        self.assertTrue(v.aborted)
        self.assertLessEqual(len(v.trials), 6)


if __name__ == "__main__":
    unittest.main()
