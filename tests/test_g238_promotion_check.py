"""G2.38, verificación final: promoción A→B y rechazo posterior, solo en directorios temporales."""

import json
import tempfile
import unittest
from pathlib import Path

from src.project import builds
from src.project.export import VERDICT_BLOCKED
from tests import test_g238_builds_audio as base

CAR = "beam2ac_g238_promotion_test"


class TestPromotionCheck(unittest.TestCase):
    def test_a_then_b_then_rejected(self):
        base.CAR = CAR  # make_attempt lee la constante del módulo
        try:
            with tempfile.TemporaryDirectory() as d:
                tmp = Path(d)
                root = tmp / "builds"
                kn5 = lambda p: (p / CAR / f"{CAR}.kn5").read_bytes()  # noqa: E731
                a = base.make_attempt(tmp, "001", content=b"VERSION-A")
                b = base.make_attempt(tmp, "002", content=b"VERSION-B")
                man_a = (a / "manifest.json").read_bytes()
                man_b = (b / "manifest.json").read_bytes()

                self.assertTrue(builds.promote(a, root=root)["promoted"])
                self.assertEqual(kn5(root / CAR), b"VERSION-A")
                r = builds.promote(b, root=root)
                self.assertTrue(r["promoted"] and r["replaced"], r)

                # A: build = B; manifiesto y build.json de B; A y B intactas en origen
                self.assertEqual(kn5(root / CAR), b"VERSION-B")
                self.assertEqual((root / CAR / "manifest.json").read_bytes(), man_b)
                bj = json.loads((root / CAR / "build.json").read_text())
                self.assertEqual(bj["car_id"], CAR)
                self.assertEqual(bj["source_attempt"], str(b))
                self.assertEqual(bj["source_manifest_sha256"], __import__("hashlib").sha256(man_b).hexdigest())
                self.assertEqual(kn5(a), b"VERSION-A")
                self.assertEqual(kn5(b), b"VERSION-B")
                self.assertEqual((a / "manifest.json").read_bytes(), man_a)
                self.assertEqual((b / "manifest.json").read_bytes(), man_b)

                # B: intento bloqueado e intento con KN5 ausente se rechazan; B sigue intacta
                bad = base.make_attempt(tmp, "003", verdict=VERDICT_BLOCKED, errors=3, content=b"MALO")
                inc = base.make_attempt(tmp, "004", content=b"INCOMPLETO")
                (inc / CAR / f"{CAR}.kn5").unlink()
                for att in (bad, inc):
                    r = builds.promote(att, root=root)
                    self.assertFalse(r["promoted"])
                    self.assertTrue(r["reason"])
                self.assertEqual(kn5(root / CAR), b"VERSION-B")
                self.assertEqual((root / CAR / "manifest.json").read_bytes(), man_b)
                self.assertEqual(json.loads((root / CAR / "build.json").read_text())["source_attempt"], str(b))
                self.assertEqual([p.name for p in root.iterdir() if p.name.startswith(".")], [])

                # el motivo del rechazo queda registrado
                log = [json.loads(x) for x in (root / builds.LOG).read_text(encoding="utf-8").splitlines()]
                rej = [e for e in log if not e["promoted"]]
                self.assertEqual(len(rej), 2)
                self.assertIn("veredicto", rej[0]["reason"])
                self.assertTrue(all(e["reason"] for e in rej))
        finally:
            base.CAR = "beam2ac_demo"


if __name__ == "__main__":
    unittest.main()
