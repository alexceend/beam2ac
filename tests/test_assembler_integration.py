"""Mejoras de v7–v12 integradas en el ensamblador: cabina, collider con perfil,
volante bajo STEER_HR, pose del piloto, base física y opciones experimentales.

Todo con datos sintéticos o con la muestra de BeamNG (solo lectura). La comparación
con los paquetes reales v12 se hace fuera de la suite (informe de integración).
"""

import json
import math
import struct
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.ac import chassis as ac_chassis
from src.ac import cockpit_config as cc
from src.ac.assemble import (
    AssemblyError,
    assemble,
    load_profile,
    physics_base_files,
)
from src.ac.car_validate import read_ini
from src.ac.collider_profile import ColliderProfileError, stations_from_points
from src.ac.drivetrain import analyse as analyse_drivetrain
from src.ac.drivetrain import write_drivetrain_files
from src.ac.driver_pose import FINGERS, Knh, build_driver_pose
from src.ac.engine import write_engine_files
from src.ac.kn5_inspect import read_kn5
from src.ac.kn5_tree import Node, dump, load, walk
from src.ac.kn5_write import box_mesh, write_simple_kn5
from src.ac.steer_wheel import SteerWheelError, rig_steering_wheel
from src.extract.engine_camso import build_engine_model
from src.jbeam import PartIndex, load_pc, resolve
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
_RESOLVED = []


def resolved():
    if not _RESOLVED:
        _RESOLVED.append(resolve(PartIndex.from_directories(VEHICLE), load_pc(PC)))
    return _RESOLVED[0]


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


CAR_INI = (
    "[HEADER]\nVERSION=1\n\n[BASIC]\nTOTALMASS=1119.6\n\n"
    "[CONTROLS]\nSTEER_LOCK=450\t\t; DIAG v6 EXPERIMENTAL: steeringWheelLock\n"
    "STEER_RATIO=-12.14\n\n[EXPLICIT_INERTIA]\nINERTIA=1, 2, 3\n"
)


class TestCockpitConfig(unittest.TestCase):
    def test_drivereyes_from_beamng_dash_camera(self):
        e = cc.driver_eyes(resolved(), (0.0, -0.29680, -0.02787))
        self.assertEqual(e.camera_name, "dash")
        self.assertEqual(e.camera_beamng, (0.354966, 0.316576, 1.441))
        # AC = (Bx, Bz, −By) + desfase de origen; valor validado en el juego (v7)
        self.assertEqual(
            ", ".join(f"{c:.3f}" for c in e.eyes_ac), "0.355, 1.144, -0.344"
        )

    EYES = cc.DriverEyes((0.1, 0.2, 1.0), (0.0, -0.3, 0.0), (0.1, 0.7, -0.2), "dash")

    def test_add_drivereyes_appends_section_once(self):
        e = self.EYES
        text = cc.add_drivereyes(CAR_INI, e)
        ini = read_ini_text(text)
        self.assertEqual(ini["GRAPHICS"]["DRIVEREYES"], "0.100, 0.700, -0.200")
        self.assertTrue(text.startswith(CAR_INI.rstrip("\n")))
        # segunda ejecución: mismo texto, una sola sección
        self.assertEqual(cc.add_drivereyes(text, e), text)
        self.assertEqual(text.count("[GRAPHICS]"), 1)

    def test_add_drivereyes_into_existing_graphics(self):
        base = CAR_INI + "\n[GRAPHICS]\nBONNET_CAMERA_POS=0, 0.5, 0.7\t\t; cámara\n\n"
        text = cc.add_drivereyes(base, self.EYES)
        self.assertEqual(text.count("[GRAPHICS]"), 1)
        ini = read_ini_text(text)
        self.assertEqual(
            ini["GRAPHICS"],
            {"BONNET_CAMERA_POS": "0, 0.5, 0.7", "DRIVEREYES": "0.100, 0.700, -0.200"},
        )
        # el resto del archivo no cambia: solo se inserta la línea de DRIVEREYES
        new_lines = text.split("\n")
        drv = [ln for ln in new_lines if ln.startswith("DRIVEREYES=")]
        self.assertEqual(len(drv), 1)
        self.assertEqual([ln for ln in new_lines if ln not in drv], base.split("\n"))
        self.assertEqual(cc.add_drivereyes(text, self.EYES), text)  # idempotente

    def test_add_drivereyes_graphics_not_last_and_crlf(self):
        base = (
            "[HEADER]\r\nVERSION=1\r\n[GRAPHICS]\r\nSHAKE_MUL=3\r\n\r\n"
            "[CONTROLS]\r\nSTEER_LOCK=450\r\n"
        )
        text = cc.add_drivereyes(base, self.EYES)
        self.assertNotIn("\n", text.replace("\r\n", ""))  # sigue en CRLF
        lines = text.split("\r\n")
        i = lines.index("[GRAPHICS]")
        self.assertEqual(lines[i + 1], "SHAKE_MUL=3")
        self.assertTrue(lines[i + 2].startswith("DRIVEREYES=0.100, 0.700, -0.200"))
        self.assertEqual(read_ini_text(text)["CONTROLS"], {"STEER_LOCK": "450"})
        self.assertEqual(cc.add_drivereyes(text, self.EYES), text)

    def test_add_drivereyes_refuses_conflicts_and_odd_format(self):
        same = CAR_INI + "\n[GRAPHICS]\nDRIVEREYES=0.1, 0.7, -0.2 ; a mano\n"
        self.assertEqual(cc.add_drivereyes(same, self.EYES), same)  # mismo valor
        for bad, why in (
            (
                CAR_INI + "\n[GRAPHICS]\nDRIVEREYES=0.3, 1.0, -0.4\n",
                "no se sobrescribe",
            ),
            (
                CAR_INI + "\n[GRAPHICS]\nDRIVEREYES=1,2,3\nDRIVEREYES=1,2,3\n",
                "2 claves",
            ),
            (CAR_INI + "\n[GRAPHICS]\nA=1\n[GRAPHICS]\nB=2\n", "repetida"),
            (CAR_INI + "\n[GRAPHICS] ; ojos\nA=1\n", "formato no soportado"),
            (CAR_INI + "\n[GRAPHICS]\nDRIVEREYES=x, y, z\n", "no numérico"),
        ):
            with (
                self.subTest(why=why),
                self.assertRaisesRegex(cc.CockpitConfigError, why),
            ):
                cc.add_drivereyes(bad, self.EYES)

    def test_experiments_edit_only_their_key(self):
        text = cc.apply_experiment(CAR_INI, "ffmult_1.95")
        lines = text.split("\n")
        self.assertTrue(lines[lines.index("[CONTROLS]") + 1].startswith("FFMULT=1.95"))
        text = cc.apply_experiment(text, "steer_lock_423")
        ini = read_ini_text(text)
        self.assertEqual(ini["CONTROLS"]["STEER_LOCK"], "423")
        self.assertIn("antes 450", text)
        base = read_ini_text(CAR_INI)
        for s in ("HEADER", "BASIC", "EXPLICIT_INERTIA"):
            self.assertEqual(ini[s], base[s])
        self.assertEqual(ini["CONTROLS"]["STEER_RATIO"], "-12.14")
        with self.assertRaises(cc.CockpitConfigError):
            cc.apply_experiment(text, "ffmult_1.95")  # ya existe
        with self.assertRaises(cc.CockpitConfigError):
            cc.apply_experiment("[BASIC]\nTOTALMASS=1\n", "ffmult_1.95")
        with self.assertRaises(cc.CockpitConfigError):
            cc.apply_experiment("[CONTROLS]\nFFMULT=1\n", "steer_lock_423")
        with self.assertRaises(cc.CockpitConfigError):
            cc.apply_experiment(CAR_INI, "steer_lock_999")

    def test_double_sided_materials_and_ext_config(self):
        with tempfile.TemporaryDirectory() as d:
            mj = Path(d) / "x.materials.json"
            mj.write_text(
                json.dumps(
                    {
                        "a": {"doubleSided": True},
                        "b": {"doubleSided": False},
                        "c": {},
                    }
                ),
                encoding="utf-8",
            )
            names = cc.double_sided_materials(["b", "a.001", "c", "a", "z"], mj)
        self.assertEqual(names, ["a", "a.001"])
        ini = read_ini_text(cc.render_ext_config(names, "x.materials.json"))
        sec = ini["SHADER_REPLACEMENT_0_BEAM2AC_DOUBLESIDED"]
        self.assertEqual(sec["MATERIALS"], "a, a.001")
        self.assertEqual(sec["CULL_MODE"], "NONE")
        with self.assertRaises(cc.CockpitConfigError):
            cc.render_ext_config([], "x")

    def test_driver3d_ini(self):
        ini = read_ini_text(cc.render_driver3d_ini())
        self.assertEqual(ini["MODEL"], {"NAME": "driver", "POSITION": "0,0,0"})
        self.assertEqual(ini["HIDE_OBJECT_0"]["NAME"], "DRIVER:HELMET")
        self.assertEqual(ini["HIDE_OBJECT_1"]["NAME"], "DRIVER:GEO_Driver_FACE")


def read_ini_text(text):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.ini"
        p.write_text(text, encoding="utf-8")
        return read_ini(p)


class TestColliderProfile(unittest.TestCase):
    def test_stations_envelope_points(self):
        body = [(x, y, z) for x in (-0.8, 0.8) for y in (0.6, 1.3) for z in (-2, 0, 2)]
        body += [(0.0, 1.4, 0.0)]  # techo más alto en el centro
        rest = body + [(0.0, 0.1, -1.0), (0.0, 0.2, 1.0), (5.0, -1.0, 0.0)]
        half, st, stats = stations_from_points(body, rest, [-0.5, 0.5])
        self.assertEqual(half, 0.8)
        self.assertEqual([s[0] for s in st], [-2, -0.5, 0.5, 2])
        # (5, −1, 0) queda fuera de la semianchura: no baja el fondo
        self.assertEqual(min(s[1] for s in st), 0.1)
        self.assertEqual(stats["floor_points_below_profile"], 0)
        self.assertEqual(stats["roof_points_above_profile"], 0)
        with self.assertRaises(ColliderProfileError):
            stations_from_points(body, rest, [0.5, -0.5])


def _steer_kn5(path: Path) -> None:
    """KN5 sintético: nulo 'body' con aro, buje+columna y otra malla; STEER_HR vacío."""
    meshes = [
        box_mesh("rim", (-0.2, 0.8, 0.30), (0.2, 1.2, 0.31), material=0),
        box_mesh("mixed", (-0.05, 0.95, 0.30), (0.05, 1.05, 0.60), material=1),
        box_mesh("seat", (-0.3, 0.3, -0.5), (0.3, 0.6, -0.2), material=2),
    ]
    write_simple_kn5(
        path,
        meshes,
        [
            ("m_lenkrad", "ksPerPixel"),
            ("m_plastic", "ksPerPixel"),
            ("m_seat", "ksPerPixel"),
        ],
    )
    parts, root = load(path.read_bytes())
    body = Node(1, "body", 1, b"\0" * 64)
    body.set_matrix([[float(i == j) for j in range(4)] for i in range(4)])
    nodes = [n for n, _ in walk(root) if n.type == 2]
    for n, p in list(walk(root)):
        if n.type == 2:
            p.children.remove(n)
    body.children = nodes
    cockpit = Node(1, "COCKPIT_HR", 1, body.payload)
    cockpit.children = [Node(1, "STEER_HR", 1, body.payload)]
    root.children += [body, cockpit]
    path.write_bytes(dump(parts, root))


def _world_tris(path: Path) -> Counter:
    k = read_kn5(path)
    out = Counter()
    for n in k.nodes:
        if n.type == 2:
            vs = k.mesh_world_vertices(n)
            for t in range(0, len(n.indices), 3):
                out[
                    tuple(
                        tuple(round(c, 6) for c in vs[i]) for i in n.indices[t : t + 3]
                    )
                ] += 1
    return out


PROFILE_WHEEL = {
    "parent_mesh": "body",
    "rim_material_token": "lenkrad",
    "move_materials": ["m_lenkrad"],
    "split_material": "m_plastic",
    "split_axial_max_m": 0.05,
    "split_mesh_name": "body_steer_part",
}


class TestSteerWheel(Tmp):
    def test_rig_keeps_world_geometry_and_centres_steer_hr(self):
        src, dst = self.tmp / "a.kn5", self.tmp / "b.kn5"
        _steer_kn5(src)
        r = rig_steering_wheel(src, dst, PROFILE_WHEEL)
        self.assertTrue(all(r["checks"].values()), r["checks"])
        self.assertEqual(_world_tris(dst), _world_tris(src))
        k = read_kn5(dst)
        steer = next(n for n in k.nodes if n.name == "STEER_HR")
        self.assertLess(math.dist(steer.translation, (0.0, 1.0, 0.305)), 1e-6)
        self.assertAlmostEqual(abs(r["rim_axis"][2]), 1.0, places=6)
        under = [
            n.name
            for n in k.nodes
            if n.parent >= 0 and k.nodes[n.parent].name == "GEO_Steer_HR"
        ]
        self.assertEqual(sorted(under), ["body_steer_part", "rim"])
        self.assertGreater(r["split"]["turning_triangles"], 0)
        self.assertGreater(r["split"]["fixed_triangles"], 0)

    def test_missing_mesh_refuses_and_writes_nothing(self):
        src, dst = self.tmp / "a.kn5", self.tmp / "b.kn5"
        _steer_kn5(src)
        with self.assertRaises(SteerWheelError):
            rig_steering_wheel(src, dst, {**PROFILE_WHEEL, "split_material": "nope"})
        self.assertFalse(dst.exists())


def _knh_bytes() -> bytes:
    """Esqueleto mínimo con los nombres de Kunos; solo traslaciones."""

    def node(name, t, kids):
        b = name.encode("latin1")
        m = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, t[0], t[1], t[2], 1.0]
        out = struct.pack("<i", len(b)) + b + struct.pack("<16f", *m)
        return out + struct.pack("<i", len(kids)) + b"".join(kids)

    def arm(side, sx):
        fingers = [node(f, (0.0, 0.0, 0.05), []) for f in FINGERS[side]]
        hand = node(f"DRIVER:RIG_HAND_{side}", (0.0, 0.0, 0.0), fingers)
        end = node(f"DRIVER:RIG_ForeArm_END_{side}", (0.05 * sx, 0.05, 0.25), [hand])
        fore = node(f"DRIVER:RIG_ForeArm_{side}", (0.05 * sx, -0.2, 0.25), [end])
        a = node(f"DRIVER:RIG_Arm_{side}", (0.0, 0.0, 0.0), [fore])
        return node(f"DRIVER:RIG_Clave_{side}", (0.2 * sx, 0.68, 0.0), [a])

    hips = node("DRIVER:RIG_Hips", (0.0, 0.0, 0.0), [])
    center = node(
        "DRIVER:RIG_Center", (0.0, 0.52, 0.0), [hips, arm("L", 1), arm("R", -1)]
    )
    drv = node("DRIVER:DRIVER", (0.0, 0.0, 0.0), [center])
    return node("SCENE_ROOT", (0.0, 0.0, 0.0), [drv])


class TestDriverPose(Tmp):
    def test_seat_and_hands(self):
        src = self.tmp / "src.knh"
        src.write_bytes(_knh_bytes())
        target = (0.34, 0.52, -0.10)
        rim_c, rim_n = (0.0, 1.05, 0.40), (0.0, 0.0, 1.0)
        r = build_driver_pose(src, self.tmp / "out.knh", target, rim_c, rim_n, 0.025)
        self.assertFalse(r["distributable"])
        self.assertTrue(all(r["hands"]["checks"].values()), r["hands"]["checks"])
        knh = Knh((self.tmp / "out.knh").read_bytes())
        w = knh.worlds()
        hips = w["DRIVER:RIG_Hips"][3][:3]
        self.assertLess(math.dist(hips, target), 1e-6)
        for side, fingers in FINGERS.items():
            axial = sum(w[f][3][2] - rim_c[2] for f in fingers) / 4
            self.assertAlmostEqual(axial, 0.025, places=5)
        self.assertEqual(
            len(src.read_bytes()), len((self.tmp / "out.knh").read_bytes())
        )


class TestProfileAndPhysicsBase(Tmp):
    def test_repo_profile_loads_and_is_checked(self):
        profile, path = load_profile(None, "seat_ibiza_acfr")
        self.assertEqual(
            set(profile),
            {
                "car_id",
                "description",
                "collider",
                "steering_wheel",
                "driver",
                "lights",
                "hands",
                "sound",  # fase C1: posición del motor para data/sounds.ini
            },
        )
        self.assertEqual(load_profile(None, "otro_coche"), (None, None))
        with self.assertRaises(AssemblyError):
            load_profile(self.tmp / "no.json", "seat_ibiza_acfr")
        bad = self.tmp / "p.json"
        bad.write_text(json.dumps({"car_id": "x"}), encoding="utf-8")
        with self.assertRaises(AssemblyError):
            load_profile(bad, "seat_ibiza_acfr")

    def _base(self) -> Path:
        r = resolved()
        art = self.tmp / "art"
        write_engine_files(build_engine_model(r), art / "engine")
        write_drivetrain_files(analyse_drivetrain(r), art / "drivetrain")
        ac_chassis.write_chassis_files(ac_chassis.analyse(r), art / "chassis")
        base = self.tmp / "pkg" / "base_car"
        (base / "data").mkdir(parents=True)
        for sub, names in (
            ("engine", ("engine.ini", "power.lut")),
            ("drivetrain", ("drivetrain.ini",)),
            ("chassis", ("suspensions.ini", "brakes.ini", "tyres.ini")),
        ):
            for n in names:
                (base / "data" / n).write_bytes((art / sub / n).read_bytes())
        (base / "data" / "car.ini").write_text(CAR_INI, encoding="utf-8")
        (base / "data" / "aero.ini").write_text(
            "[WING_0]\nNAME=BODY\n", encoding="utf-8"
        )
        (base / "data" / "lods.ini").write_text(
            "[LOD_0]\nFILE=x.kn5\n", encoding="utf-8"
        )
        (base / "data" / "driver3d.ini").write_text(
            "[MODEL]\nNAME=x\n", encoding="utf-8"
        )
        return base

    def test_physics_base_copied_byte_for_byte_with_experiments(self):
        base = self._base()
        info = physics_base_files(base)
        self.assertNotIn("data/lods.ini", info["files"])
        self.assertNotIn("data/car.ini", info["files"])
        report = assemble(
            VEHICLE,
            PC,
            "seat_ibiza_acfr",
            self.tmp / "unused",
            self.tmp / "out",
            with_kn5=False,
            physics_base=base,
            experiments=("ffmult_1.95", "steer_lock_423"),
        )
        car = Path(report["car_dir"])
        for rel in info["files"]:
            self.assertEqual((car / rel).read_bytes(), (base / rel).read_bytes(), rel)
        self.assertEqual(report["physics_base"]["changed"], [])
        ini = read_ini(car / "data" / "car.ini")
        self.assertEqual(ini["CONTROLS"]["FFMULT"], "1.95")
        self.assertEqual(ini["CONTROLS"]["STEER_LOCK"], "423")
        self.assertEqual(ini["BASIC"]["TOTALMASS"], "1119.6")  # el de la base
        self.assertEqual(ini["GRAPHICS"]["DRIVEREYES"], "0.355, 1.144, -0.344")
        self.assertNotEqual(
            read_ini(car / "data" / "driver3d.ini")["MODEL"]["NAME"], "x"
        )
        checks = {c["check"]: c["ok"] for c in report["validation"]["checks"]}
        self.assertTrue(checks["base física: archivos copiados idénticos byte a byte"])
        self.assertTrue(
            checks["car.ini: física de la base intacta (salvo opciones experimentales)"]
        )
        self.assertIn("textures", report["skipped"])  # sin Blender
        # carpeta ya escrita: no se pisa sin --overwrite
        with self.assertRaises(AssemblyError):
            assemble(VEHICLE, PC, "seat_ibiza_acfr", self.tmp, self.tmp / "out", None)

    def test_manifest_mismatch_and_experiments_without_base_refused(self):
        base = self._base()
        (base.parent / "manifest.json").write_text(
            json.dumps({"package_dir": str(base), "files": {}}), encoding="utf-8"
        )
        with self.assertRaises(AssemblyError):
            physics_base_files(base)
        with self.assertRaises(AssemblyError):
            assemble(
                VEHICLE,
                PC,
                "seat_ibiza_acfr",
                self.tmp,
                self.tmp / "out2",
                None,
                experiments=("ffmult_1.95",),
            )
        self.assertFalse((self.tmp / "out2").exists())


if __name__ == "__main__":
    unittest.main()
