"""G2.25 §1.3: registro de arranque (solo lectura) y su resumen."""

import json
import math
import tempfile
import unittest
from pathlib import Path

from tools.acbench import validate as V


class _Geo:
    def read(self):
        return {"loaded_radius": [0.3375, 0.3375, 0.135, 0.3375], "body_up": [0, 1, 0]}


def _tel(frame, ms, pos, load):
    return {
        "frame": frame,
        "phys_ms": ms,
        "pos": pos,
        "load": load,
        "speed_kmh": 0.0,
        "up": [0, 1, 0],
    }


class TestStartup(unittest.TestCase):
    def _rec(self):
        path = Path(tempfile.mkdtemp()) / "arranque.jsonl"
        rec = V.StartupRecorder.__new__(V.StartupRecorder)
        rec.s, rec.geo, rec.path, rec.last_frame, rec.n, rec.last_phys = (
            None,
            _Geo(),
            path,
            None,
            0,
            0.0,
        )
        return rec, path

    def test_nan_then_recovery(self):
        rec, path = self._rec()
        nan = float("nan")
        for i in range(5):
            rec(_tel(i, 3000 + 10 * i, [nan] * 3, [0.0] * 4))
        # fotograma repetido: no se registra
        rec(_tel(4, 3050, [nan] * 3, [0.0] * 4))
        for i in range(5, 9):
            rec(
                _tel(
                    i, 7400 + 10 * i, [0.0, 0.0, 0.1], [4000.0, 4000.0, 5000.0, 5000.0]
                )
            )
        self.assertEqual(rec.n, 9)
        s = V.summarize_startup(path)
        self.assertEqual(s["no_finitos"], 5)
        self.assertEqual(s["primer_no_finito_ms"], 3000)
        self.assertEqual(s["ultimo_no_finito_ms"], 3040)
        self.assertTrue(s["final"]["finito"])
        self.assertEqual(s["final"]["suma_cargas_N"], 18000.0)
        self.assertFalse(s["primero"]["finito"])
        # NaN se escribe como NaN de JSON y se relee
        self.assertTrue(
            math.isnan(json.loads(path.read_text("utf-8").splitlines()[0])["pos"][0])
        )

    def test_all_finite(self):
        rec, path = self._rec()
        for i in range(3):
            rec(_tel(i, 3000 + i, [0.0, 0.0, 0.0], [1.0] * 4))
        s = V.summarize_startup(path)
        self.assertEqual(s["no_finitos"], 0)
        self.assertIsNone(s["primer_no_finito_ms"])


if __name__ == "__main__":
    unittest.main()


class TestAdenda3(unittest.TestCase):
    @staticmethod
    def _res(fails, run="r1"):
        crit = [
            {"grupo": g, "criterio": c, "ok": False, "nivel": n, "valor": None}
            for g, c, n in fails
        ]
        crit.append(
            {"grupo": "A_carga", "criterio": "x", "ok": True, "nivel": "primario"}
        )
        return {"run_id": run, "criterios": crit, "curvas": {}, "motor": []}

    def test_startup_alone_caps_at_partial(self):
        from tools.acbench import validate_analysis as VA

        tol = {"relativa_por_defecto": {"rel": 0.15}}
        s = self._res([("A_arranque", "sin_estado_no_finito", "primario")])
        s2 = self._res([("A_arranque", "sin_estado_no_finito", "primario")], "r2")
        out = VA.classify([s, s2], {"a": "sin_anomalias"}, tol)
        self.assertEqual(out["estado"], "partial")
        both = self._res(
            [
                ("A_arranque", "sin_estado_no_finito", "primario"),
                ("C_motor", "rpm_bajan_al_soltar", "primario"),
            ]
        )
        self.assertEqual(VA.classify([both, both], {}, tol)["estado"], "physics_failed")

    def test_m_startup_final_state(self):
        from tools.acbench import validate_analysis as VA

        path = Path(tempfile.mkdtemp()) / "arranque.jsonl"
        nan = float("nan")
        rows = [_tel(0, 3000, [nan] * 3, [0.0] * 4)]
        rows.append(_tel(1, 12000, [0.0, 0.0, 0.0], [2452.5] * 4))
        path.write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
        a = VA.m_startup(path, 1000.0)
        self.assertEqual(a["no_finitos"], 1)
        self.assertTrue(a["final_valido"])
        self.assertEqual(VA.m_startup(path.with_name("x.jsonl"), 1.0)["registros"], 0)
