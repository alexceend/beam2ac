"""Cambiar la masa del piloto con decisiones de masa en el proyecto: se recalculan (no se bloquea)."""

import tempfile
import unittest
from pathlib import Path

from src.app.workspace import Workspace
from src.attrs.store import REPO_CATALOG, Catalog

EAGLE = next(iter(Path("samples_beamng").glob("thershow_eagle*/vehicles/eagle*")), None)


@unittest.skipUnless(EAGLE, "corpus no disponible")
class TestRederive(unittest.TestCase):
    def test_driver_mass_change_supersedes_mass_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            ws.create({"name": "t_eagle", "kind": "dir", "path": str(EAGLE), "pc": "c27b2.pc",
                       "driver_mass": 0, "mass_choice": "declared"})
            root = Path(tmp) / "t_eagle"
            def total(cat):
                ds = [d for d in cat.decisions if d.target == "ac:car.ini/BASIC/TOTALMASS"]
                return ds, [d for d in ds if not any(x.supersedes == d.id for x in ds)][0]
            ds0, act0 = total(Catalog.load(REPO_CATALOG, root))
            ws.set_driver_mass("t_eagle", {"driver_mass": 70})  # antes: error «crea un proyecto nuevo»
            ds1, act1 = total(Catalog.load(REPO_CATALOG, root))
            self.assertEqual(len(ds1), len(ds0) + 1)
            self.assertEqual(act1.supersedes, act0.id)
            self.assertAlmostEqual(act1.value - act0.value, 70.0, delta=0.5)

    def test_concurrent_changes_keep_unique_ids(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace(Path(tmp))
            ws.create({"name": "t_eagle", "kind": "dir", "path": str(EAGLE), "pc": "c27b2.pc",
                       "driver_mass": 0, "mass_choice": "measured"})
            ts = [threading.Thread(target=ws.set_driver_mass, args=("t_eagle", {"driver_mass": m})) for m in (70, 80, 90)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            ids = [d.id for d in Catalog.load(REPO_CATALOG, Path(tmp) / "t_eagle").decisions]  # no lanza: ids únicos
            self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
