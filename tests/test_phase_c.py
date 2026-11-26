"""Fase C: cámaras, luces y manos en el ensamblador.

Pruebas sintéticas de las reglas y de las entradas incompatibles, y pruebas de
regresión de solo lectura contra los paquetes aprobados (se omiten si no existen):
el código integrado debe dar los mismos bytes que v15li (luces), v14ha (manos) y
v17cam/v18bu (cámaras).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.ac import cameras as cam
from src.ac import cockpit_config as cc
from src.ac import hands
from src.ac.assemble import assemble
from src.ac.car_validate import read_ini
from src.ac.kn5_materials import split_kn5
from src.ac.lights import LightsError, apply_lights, light_functions
from src.jbeam import PartIndex, lax_json, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
IC = PROJECT_ROOT / "output" / "seat_ibiza_acfr" / "ingame_cycle"
CAMERA_WORK = PROJECT_ROOT / "output" / "seat_ibiza_acfr" / "camera_work_2026-10-25"
PROFILE = json.loads(
    (PROJECT_ROOT / "src/ac/vehicle_profiles/seat_ibiza_acfr.json").read_text(
        encoding="utf-8"
    )
)
_RESOLVED = []


def resolved():
    if not _RESOLVED:
        _RESOLVED.append(resolve(PartIndex.from_directories(VEHICLE), load_pc(PC)))
    return _RESOLVED[0]


def pkg(name: str) -> Path | None:
    m = IC / name / "manifest.json"
    if not m.is_file():
        return None
    return Path(json.loads(m.read_text(encoding="utf-8"))["package_dir"])


def lf(b: bytes) -> str:
    return b.decode("utf-8").replace("\r\n", "\n")


# ------------------------------------------------------------------ cámaras
def car_profile_tris():
    """Perfil central sintético: techo, parabrisas, escalón del capó y capó."""
    pts = [(-1.0, 1.30), (0.20, 1.30), (0.90, 0.85), (0.92, 0.85), (0.96, 0.88)]
    pts += [(1.50, 0.80), (2.00, 0.60)]
    tris = []
    for (z0, y0), (z1, y1) in zip(pts, pts[1:]):
        a, b = (-0.5, y0, z0), (0.5, y0, z0)
        c, d = (-0.5, y1, z1), (0.5, y1, z1)
        tris += [(a, b, c), (b, d, c)]
    return 2.0, tris


class TestCameraRules(unittest.TestCase):
    GO = (0.0, -0.46, -0.09)
    EYES = (0.355, 1.144, -0.35)

    def test_rules_on_synthetic_profile(self):
        front, tris = car_profile_tris()
        c = cam.compute_cameras(front, tris, self.EYES, self.GO)
        # escalón entre z 0,92 y 0,96; la búsqueda va en pasos de 2 cm
        self.assertTrue(0.93 <= c.hood_rear_edge_z <= 0.97, c.hood_rear_edge_z)
        self.assertEqual(c.bonnet_kn5[2], round(c.hood_rear_edge_z - 0.222, 3))
        self.assertAlmostEqual(c.bonnet_kn5[1], c.bonnet_surface_y + 0.055, places=3)
        self.assertEqual(
            c.bonnet_body,
            tuple(round(c.bonnet_kn5[i] + self.GO[i], 3) for i in range(3)),
        )
        self.assertEqual(c.bumper_body[:2], (0.0, 0.575))
        self.assertEqual(c.bumper_kn5[2], round(2.0 - 0.185, 3))
        self.assertEqual(c.dash_pos, self.EYES)
        self.assertEqual(c.dash_exp, 26)
        self.assertGreaterEqual(c.bumper_min_clearance, 0.10)

    def test_incompatible_inputs_refused(self):
        front, tris = car_profile_tris()
        with self.assertRaisesRegex(cam.CameraError, "PITCH"):
            cam.compute_cameras(front, tris, self.EYES, self.GO, 1.0)
        with self.assertRaisesRegex(cam.CameraError, "capó"):
            cam.compute_cameras(front, [], self.EYES, self.GO)
        # carrocería que tapa la y de los Kunos: la cámara Bumper sube (ya no es un error)
        c = cam.compute_cameras(front, tris, self.EYES, (0.0, 0.5, -0.09))
        self.assertGreater(c.bumper_body[1], cam.BUMPER_Y_BODY)
        self.assertGreaterEqual(c.bumper_min_clearance, 0.10)

    def test_constants_match_saved_measurements(self):
        meas = CAMERA_WORK / "cameras_measurement.json"
        bump = CAMERA_WORK / "bumper_measurement.json"
        if not (meas.is_file() and bump.is_file()):
            self.skipTest("sin mediciones de camera_work_2026-10-25")
        r = json.loads(meas.read_text(encoding="utf-8"))["ours"]["BONNET_derived"]
        self.assertEqual(r["rule"]["behind_hood_edge_median"], 0.222)
        self.assertEqual(r["rule"]["above_surface_median"], 0.055)
        b = json.loads(bump.read_text(encoding="utf-8"))["rule"]
        self.assertEqual(b["y_body_median"], cam.BUMPER_Y_BODY)
        self.assertEqual(b["z_minus_front_median"], cam.BUMPER_Z_MINUS_FRONT_M)
        self.assertEqual(
            (min(b["z_minus_front_values"]), max(b["z_minus_front_values"])),
            cam.BUMPER_Z_RANGE_M,
        )

    def test_graphics_lines_no_duplicates_and_repeatable(self):
        front, tris = car_profile_tris()
        c = cam.compute_cameras(front, tris, self.EYES, self.GO)
        ini = "[BASIC]\nTOTALMASS=1\n\n[GRAPHICS]\nDRIVEREYES=0.355, 1.144, -0.35\n"
        once = cc.add_graphics_line(
            ini, "BONNET_CAMERA_POS", c.bonnet_body, cam.bonnet_line(c)
        )
        once = cc.add_graphics_line(
            once, "BUMPER_CAMERA_POS", c.bumper_body, cam.bumper_line(c)
        )
        self.assertEqual(once.count("[GRAPHICS]"), 1)
        self.assertEqual(
            [ln.split("=")[0] for ln in once.split("\n")[4:7]],
            ["DRIVEREYES", "BONNET_CAMERA_POS", "BUMPER_CAMERA_POS"],
        )
        again = cc.add_graphics_line(
            once, "BONNET_CAMERA_POS", c.bonnet_body, cam.bonnet_line(c)
        )
        self.assertEqual(again, once)  # segunda ejecución: igual
        with self.assertRaisesRegex(cc.CockpitConfigError, "no se sobrescribe"):
            cc.add_graphics_line(once, "BONNET_CAMERA_POS", (0, 9, 9), "X=1")
        dup = once + "BONNET_CAMERA_POS=0, 1, 1\n"
        with self.assertRaisesRegex(cc.CockpitConfigError, "2 claves"):
            cc.add_graphics_line(dup, "BONNET_CAMERA_POS", c.bonnet_body, "X=1")

    def test_dash_cam_text(self):
        front, tris = car_profile_tris()
        body = cam.render_dash_cam_ini(
            cam.compute_cameras(front, tris, self.EYES, self.GO)
        )
        ini = lf(body)
        self.assertIn("[DASH_CAM]\nPOS=0.355, 1.144, -0.35\nEXP=26\n", ini)
        self.assertNotIn("\n", body.decode().replace("\r\n", ""))  # CRLF


class TestCamerasRegression(unittest.TestCase):
    def test_v18bu_values_and_bytes(self):
        car = pkg("pkg_v18bu")
        if car is None:
            self.skipTest("sin pkg_v18bu")
        ini = read_ini(car / "data/car.ini")
        go = tuple(float(v) for v in ini["BASIC"]["GRAPHICS_OFFSET"].split(","))
        eyes = tuple(float(v) for v in ini["GRAPHICS"]["DRIVEREYES"].split(","))
        c = cam.cameras_from_kn5(car / "seat_ibiza_acfr.kn5", eyes, go)
        self.assertEqual(c.bonnet_body, (0.0, 0.546, 0.683))
        self.assertEqual(c.bumper_body, (0.0, 0.575, 1.817))
        lines = (car / "data/car.ini").read_text(encoding="utf-8").splitlines()
        # el comentario de la línea se redactó de nuevo al anonimizar ("acs.exe CarAvatar+0x26c" ->
        # "AC"); el valor (antes del ';') es lo congelado
        keys = [ln.split(";")[0].rstrip() for ln in lines]
        self.assertIn(cam.bonnet_line(c).split(";")[0].rstrip(), keys)
        self.assertIn(cam.bumper_line(c).split(";")[0].rstrip(), keys)

        def no_comments(b: bytes) -> list[bytes]:
            return [ln for ln in b.splitlines() if not ln.startswith(b";")]

        self.assertEqual(
            no_comments(cam.render_dash_cam_ini(c)),
            no_comments((car / "data/dash_cam.ini").read_bytes()),
        )


# ------------------------------------------------------------------ luces
class TestLights(unittest.TestCase):
    def _inputs(self):
        before, after = pkg("pkg_v14ha_alternating_grip"), pkg("pkg_v15li_lights")
        if before is None or after is None:
            self.skipTest("sin pkg_v14ha / pkg_v15li")
        mj = lax_json.load(VEHICLE / "35b84.materials.json").value
        return before, after, light_functions(resolved()), mj

    def test_matches_validated_v15li(self):
        before, after, functions, mj = self._inputs()
        r = apply_lights(
            (before / "seat_ibiza_acfr.kn5").read_bytes(),
            functions,
            mj,
            PROFILE["lights"],
        )
        self.assertEqual(r.kn5, (after / "seat_ibiza_acfr.kn5").read_bytes())
        self.assertEqual(r.lights_ini, lf((after / "data/lights.ini").read_bytes()))
        ext_before = lf((before / "extension/ext_config.ini").read_bytes())
        self.assertEqual(
            ext_before + r.csp_emissive,
            lf((after / "extension/ext_config.ini").read_bytes()),
        )
        self.assertEqual(len(r.renamed), 19)
        self.assertTrue(all(o == "35b84_body0" for o, _ in r.renamed))
        self.assertTrue(all(n.startswith("B2AC_") for _, n in r.renamed))

    def test_incompatible_inputs_refused(self):
        before, after, functions, mj = self._inputs()
        kn5 = (before / "seat_ibiza_acfr.kn5").read_bytes()
        bad_count = {**PROFILE["lights"], "expected_count": 18}
        with self.assertRaisesRegex(LightsError, "espera 18"):
            apply_lights(kn5, functions, mj, bad_count)
        bad_name = {**PROFILE["lights"], "source_mesh_name": "otro_body"}
        with self.assertRaisesRegex(LightsError, "otro_body"):
            apply_lights(kn5, functions, mj, bad_name)
        with self.assertRaises(LightsError):  # KN5 ya renombrado (v15li)
            apply_lights(
                (after / "seat_ibiza_acfr.kn5").read_bytes(),
                functions,
                mj,
                PROFILE["lights"],
            )
        with self.assertRaises(LightsError):  # material de luz sin malla
            apply_lights(
                kn5,
                {**functions, "material_x_inexistente": "BRAKE"},
                mj,
                {**PROFILE["lights"], "expected_count": 20},
            )


# ------------------------------------------------------------------ manos
class TestHands(unittest.TestCase):
    def test_matches_validated_v14ha(self):
        ref, base = pkg("pkg_v14ha_alternating_grip"), pkg("pkg_v13vw_shiftwindow")
        if ref is None or base is None:
            self.skipTest("sin pkg_v14ha / pkg_v13vw")
        kn5 = base / "seat_ibiza_acfr.kn5"
        mats = [m.name for m in split_kn5(kn5.read_bytes()).materials]
        centre, axis = hands.rim(
            kn5, mats, PROFILE["steering_wheel"]["rim_material_token"]
        )
        r = hands.alternating_grip(
            (base / "driver_base_pos.knh").read_bytes(), centre, axis, PROFILE["hands"]
        )
        self.assertEqual(r.lock_deg, 125.0)
        self.assertEqual(r.frames, 600)
        self.assertEqual(r.ksanim, (ref / "animations/steer.ksanim").read_bytes())
        self.assertEqual(
            hands.add_steer_animation(
                lf((base / "data/driver3d.ini").read_bytes()), 125
            ),
            lf((ref / "data/driver3d.ini").read_bytes()),
        )

    def test_profile_and_driver3d_checks(self):
        knh = b""
        with self.assertRaisesRegex(hands.HandsError, "solo está integrada"):
            hands.alternating_grip(
                knh, (0, 0, 0), (0, 0, 1), {"variant": "hand_over_hand"}
            )
        with self.assertRaisesRegex(hands.HandsError, "incompleto"):
            hands.alternating_grip(
                knh, (0, 0, 0), (0, 0, 1), {"variant": hands.VARIANT}
            )
        text = hands.add_steer_animation("[MODEL]\nNAME=driver\n\n", 125.0)
        self.assertTrue(
            text.endswith("[STEER_ANIMATION]\nNAME=steer.ksanim\nLOCK=125\n")
        )
        self.assertEqual(text.count("[STEER_ANIMATION]"), 1)
        with self.assertRaises(hands.HandsError):
            hands.add_steer_animation(text, 125.0)


# ------------------------------------------------------------------ ensamblador
class TestAssemblerSkips(unittest.TestCase):
    def test_without_kn5_features_are_skipped_with_reason(self):
        with tempfile.TemporaryDirectory() as d:
            from tests.test_physics_base import make_package, pipeline_physics
            from src.ac import physics_base as pb

            pkg_dir = make_package(Path(d), pipeline_physics(), ("data/wear.lut",))
            m = pb.freeze(pkg_dir, Path(d) / "bases", "b", "seat_ibiza_acfr")
            report = assemble(
                VEHICLE,
                PC,
                "seat_ibiza_acfr",
                Path(d),
                Path(d) / "out",
                with_kn5=False,
                physics_base=Path(m["package_dir"]),
            )
            for k in ("lights", "hands", "cameras"):
                self.assertEqual(report["skipped"][k], "sin KN5 (no solicitado)")
            car = Path(report["car_dir"])
            self.assertFalse((car / "data/dash_cam.ini").exists())
            self.assertFalse((car / "animations").exists())


if __name__ == "__main__":
    unittest.main()
