"""Editor de animación del piloto: ajustes y vista previa (src/project/anim_preview.py)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.project.export import default_ac_root
from src.project import anim_preview as AP

AC = Path((default_ac_root() or Path("assettocorsa")).as_posix())
PROFILE = {
    "driver": {"knuckle_target_m": 0.025, "pose_source_car": "ks_toyota_ae86"},
    "hands": {"left": [-55, 85, 40], "right": [140, 85, 40], "lift_m": 0.08},
}


class TestEdits(unittest.TestCase):
    def test_clean_validates(self) -> None:
        self.assertEqual(AP.clean(None), {})
        ok = AP.clean({"left": [0, 80, 40], "right": [150, 70, 50], "lift_m": 0.1})
        self.assertEqual(ok["lift_m"], 0.1)
        for bad in (
            {"left": [0, 80, 40]},  # sin right
            {"left": [0, 80, 40], "right": [0, 80, 41]},  # periodos distintos
            {"lift_m": 5},
            {"lift_m": float("nan")},
            {"x": 1},
            {"left": [0, 1, 2, 3], "right": [0, 1, 2]},
            [1],
        ):
            with self.assertRaises(AP.AnimError):
                AP.clean(bad)

    def test_override_does_not_touch_the_original(self) -> None:
        out = AP.apply_override(PROFILE, {"lift_m": 0.1, "knuckle_target_m": 0.05})
        self.assertEqual(out["hands"]["lift_m"], 0.1)
        self.assertEqual(out["driver"]["knuckle_target_m"], 0.05)
        self.assertEqual(PROFILE["hands"]["lift_m"], 0.08)

    def test_versions_append_only(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(AP, "ROOT", Path(tmp)),
            mock.patch.object(AP, "ensure_safe_output_path", lambda p: Path(p)),
        ):
            key = "0123456789abcdef_x"
            self.assertEqual(AP.current(key)["version"], 0)
            AP.save(key, {"lift_m": 0.05})
            AP.save(key, {})
            self.assertEqual([v["version"] for v in AP.versions(key)], [1, 2])
            self.assertEqual(AP.current(key)["edits"], {})
            self.assertEqual(
                json.loads((Path(tmp) / key / "v001.json").read_text())["edits"],
                {"lift_m": 0.05},
            )
            with self.assertRaises(AP.AnimError):
                AP.current("../x")


def _ibiza():
    from src.safe_paths import PROJECT_ROOT

    stages = sorted(
        (PROJECT_ROOT / "output/projects/ibiza_35b84_b1/stages/model").glob(
            "*/model.json"
        )
    )
    return stages[-1].parent if stages else None


@unittest.skipUnless(AC.is_dir() and _ibiza(), "necesita AC local y el proyecto Ibiza")
class TestPreview(unittest.TestCase):
    def test_preview_and_edits_move_the_hands(self) -> None:
        from src.safe_paths import PROJECT_ROOT

        d = _ibiza()
        model = json.loads((d / "model.json").read_text(encoding="utf-8"))
        prof = json.loads(
            (PROJECT_ROOT / model["profile"]["path"]).read_text(encoding="utf-8")
        )
        hips = tuple(prof["driver"]["hips_target"])
        a = AP.preview(d, model, prof, AC, hips, 12)
        self.assertEqual(len(a["frames"]), 12)
        self.assertEqual(a["period_deg"], 125.0)
        self.assertGreater(a["reach_margin_m"], 0)
        names = [b["name"] for b in a["bones"]]
        h = names.index("HAND_L")
        # con el volante girando, la mano izquierda recorre el aro
        self.assertNotEqual(a["frames"][0][h], a["frames"][3][h])
        # un ajuste de manos cambia la pose
        b = AP.preview(d, model, AP.apply_override(prof, {"lift_m": 0.2}), AC, hips, 12)
        self.assertNotEqual(a["frames"], b["frames"])
        with self.assertRaises(AP.AnimError):
            AP.preview(d, model, prof, AC, hips, 3)

    def test_unreachable_hips_still_preview(self) -> None:
        """Con unas caderas fuera de alcance la vista previa dibuja el brazo estirado y lo dice
        (``reachable`` falso) en vez de lanzar el error: así el editor puede mostrarlo y moverlo.
        """
        from src.safe_paths import PROJECT_ROOT

        d = _ibiza()
        model = json.loads((d / "model.json").read_text(encoding="utf-8"))
        prof = json.loads(
            (PROJECT_ROOT / model["profile"]["path"]).read_text(encoding="utf-8")
        )
        hips = tuple(prof["driver"]["hips_target"])
        far = (hips[0], hips[1] + 0.3, hips[2] - 0.4)
        r = AP.preview(d, model, prof, AC, far, 8)
        self.assertFalse(r["reachable"])
        self.assertLess(r["reach_margin_m"], 0)
        self.assertTrue(AP.preview(d, model, prof, AC, hips, 8)["reachable"])

    def test_exporter_applies_the_edits(self) -> None:
        from src.project.local_driver import local_driver_files

        d = _ibiza()
        model = json.loads((d / "model.json").read_text(encoding="utf-8"))
        from hashlib import sha256

        from src.safe_paths import PROJECT_ROOT

        # la etapa model guardada puede ser anterior al perfil actual: solo se prueba el ajuste
        pf = PROJECT_ROOT / model["profile"]["path"]
        model["profile"]["sha256"] = sha256(pf.read_bytes()).hexdigest()
        a, rep_a = local_driver_files(d, model, AC)
        b, rep_b = local_driver_files(d, model, AC, anim_edits={"lift_m": 0.2})
        self.assertNotEqual(a["animations/steer.ksanim"], b["animations/steer.ksanim"])
        self.assertEqual(rep_b["anim_edits"], {"lift_m": 0.2})
        self.assertIsNone(rep_a["anim_edits"])


if __name__ == "__main__":
    unittest.main()
