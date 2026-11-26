"""Base física congelada (src.ac.physics_base) y ``assemble --physics-base``."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.ac import physics_base as pb
from src.ac.assemble import AssemblyError, assemble, physics_base_files
from src.ac.car_validate import LOAD_AERO, LOAD_REAR_TYPE, read_ini
from src.ac.ingame_package import sha256, tree_hashes
from src.safe_paths import PROJECT_ROOT, ProtectedPathError

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
IC = PROJECT_ROOT / "output" / "seat_ibiza_acfr" / "ingame_cycle"
REAL_BASES = PROJECT_ROOT / "output" / "seat_ibiza_acfr" / "physics_bases"

CAR_INI = (
    b"[HEADER]\r\nVERSION=1\r\n\r\n[BASIC]\r\nTOTALMASS=1119.6\r\n"
    b"GRAPHICS_OFFSET=0, -0.46, -0.09\r\n\r\n[CONTROLS]\r\nSTEER_LOCK=423\r\n"
    b"STEER_RATIO=-12.14\r\n\r\n[EXPLICIT_INERTIA]\r\nINERTIA=1, 2, 3\r\n\r\n"
    b"[GRAPHICS]\r\nDRIVEREYES=0.355, 1.144, -0.344\t\t; ojos\r\n"
    b"BONNET_CAMERA_POS=0, 0.546, 0.683\r\nBUMPER_CAMERA_POS=0, 0.575, 1.817\r\n"
)
PHYSICS = {
    "data/aero.ini": b"[WING_0]\r\nNAME=BODY\r\n",
    "data/brakes.ini": b"[DATA]\r\nFRONT_SHARE=0.857\r\n",
    "data/drivetrain.ini": b"[TRACTION]\r\nTYPE=FWD\r\n[GEARBOX]\r\n"
    b"VALID_SHIFT_RPM_WINDOW=800\r\n",
    "data/engine.ini": b"[HEADER]\r\nPOWER_CURVE=power.lut\r\n",
    "data/power.lut": b"1000|100\r\n",
    "data/suspensions.ini": b"[FRONT]\r\nTYPE=STRUT\r\n[REAR]\r\nTYPE=DWB\r\n",
    "data/tyres.ini": b"[FRONT]\r\nWEAR_CURVE=wear.lut\r\n",
    "data/wear.lut": b"0|1\r\n",
    "data/setup.ini": b"[DISPLAY_METHOD]\r\nSHOW_CLICKS=0\r\n",
}
OTHERS = {
    "data/dash_cam.ini": b"[DASH_CAM]\r\nPOS=0, 1, 0\r\n",
    "data/lights.ini": b"[LIGHT_0]\r\nNAME=x\r\n",
    "data/sounds.ini": b"[ENGINE]\r\nPOSITION=front\r\n",
    "data/driver3d.ini": b"[MODEL]\r\nNAME=driver\r\n",
    "data/lods.ini": b"[LOD_0]\r\nFILE=car.kn5\r\n",
    "car.kn5": b"sc6969",
    "collider.kn5": b"sc6969",
    "driver_base_pos.knh": b"knh",
    "animations/steer.ksanim": b"anim",
    "sfx/car.bank": b"RIFF",
    "sfx/GUIDs.txt": b"x",
    "extension/ext_config.ini": b"[X]\r\n",
    "skins/s/ui_skin.json": b"{}",
    "ui/ui_car.json": b'{"name": "Car"}',
}
EXPECTED_PHYSICS = sorted(list(PHYSICS) + ["data/car.ini"])


def make_package(root: Path, extra: dict[str, bytes] | None = None, drop=()) -> Path:
    """Paquete «aprobado» con su manifest.json, como los de ingame_cycle."""
    car = root / "pkg_vx" / "beam2ac_test_car"
    files = {**PHYSICS, **OTHERS, "data/car.ini": CAR_INI, **(extra or {})}
    for rel, body in files.items():
        if rel in drop:
            continue
        (car / rel).parent.mkdir(parents=True, exist_ok=True)
        (car / rel).write_bytes(body)
    (car.parent / "manifest.json").write_text(
        json.dumps({"package_dir": str(car), "files": tree_hashes(car)}),
        encoding="utf-8",
    )
    return car


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestFreeze(Tmp):
    def test_manifest_inventory_and_byte_copies(self):
        pkg = make_package(self.tmp)
        before = tree_hashes(pkg)
        m = pb.freeze(pkg, self.tmp / "bases", "base_x", "car")
        self.assertEqual(m["kind"], pb.KIND)
        self.assertIn("NO está calibrada", m["status"])
        self.assertEqual(sorted(m["physics_files"]), EXPECTED_PHYSICS)
        self.assertEqual(sorted(m["excluded"]), sorted(OTHERS))
        base = Path(m["package_dir"])
        for rel in PHYSICS:
            self.assertEqual((base / rel).read_bytes(), PHYSICS[rel], rel)
            self.assertEqual(m["physics_files"][rel]["sha256"], before[rel])
        cats = {r: i["category"] for r, i in m["physics_files"].items()}
        self.assertEqual(cats["data/setup.ini"], "requisito de carga")
        self.assertEqual(cats["data/car.ini"], "plantilla")
        self.assertEqual(cats["data/wear.lut"], "física")
        # car.ini: sin [GRAPHICS]; el resto, byte a byte
        car_ini = (base / "data/car.ini").read_bytes()
        self.assertEqual(car_ini, CAR_INI[: CAR_INI.index(b"[GRAPHICS]")])
        self.assertEqual(len(m["car_ini_removed_lines"]), 4)
        self.assertEqual(
            m["checks"]["drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW"], "800"
        )
        self.assertEqual(m["checks"][LOAD_REAR_TYPE], "DWB")
        self.assertEqual(m["checks"][LOAD_AERO], "WING_0")
        self.assertEqual(
            m["source"]["manifest_sha256"], sha256(pkg.parent / "manifest.json")
        )
        self.assertEqual(tree_hashes(pkg), before)  # el origen no cambia
        self.assertEqual(m["files"], tree_hashes(base))

    def test_frozen_base_is_sealed_for_other_processes(self):
        m = pb.freeze(make_package(self.tmp), self.tmp / "bases", "base_x", "car")
        code = (
            "import sys; from src.safe_paths import protection_reason; "
            f"print(protection_reason(r'{m['package_dir']}\\data\\aero.ini'))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("paquete sellado", out)

    def test_second_freeze_with_same_id_refused(self):
        pkg = make_package(self.tmp)
        m = pb.freeze(pkg, self.tmp / "bases", "base_x", "car")
        before = tree_hashes(Path(m["package_dir"]).parent)
        with self.assertRaises(ProtectedPathError):
            pb.freeze(pkg, self.tmp / "bases", "base_x", "car")
        self.assertEqual(tree_hashes(Path(m["package_dir"]).parent), before)

    def test_refusals_write_nothing(self):
        cases = {
            "sin clasificar": ({"data/mystery.ini": b"[X]\r\n"}, ()),
            "VALID_SHIFT": ({"data/drivetrain.ini": b"[TRACTION]\r\nTYPE=FWD\r\n"}, ()),
            "REAR": (
                {"data/suspensions.ini": b"[FRONT]\r\nTYPE=STRUT\r\n[REAR]\r\n"},
                (),
            ),
            "aero": ({}, ("data/aero.ini",)),
            "tablas referenciadas": ({}, ("data/wear.lut",)),
        }
        for i, (why, (extra, drop)) in enumerate(cases.items()):
            with self.subTest(why=why):
                root = self.tmp / f"c{i}"
                pkg = make_package(root, extra, drop)
                with self.assertRaises(pb.PhysicsBaseError):
                    pb.freeze(pkg, root / "bases", "b", "car")
                self.assertFalse((root / "bases").exists())

    def test_package_must_match_its_manifest(self):
        pkg = make_package(self.tmp)
        (pkg / "data" / "brakes.ini").write_bytes(b"[DATA]\r\nFRONT_SHARE=0.5\r\n")
        with self.assertRaisesRegex(pb.PhysicsBaseError, "manifiesto"):
            pb.freeze(pkg, self.tmp / "bases", "b", "car")

    def test_tampered_base_refused_by_assembler(self):
        m = pb.freeze(make_package(self.tmp), self.tmp / "bases", "base_x", "car")
        base = Path(m["package_dir"])
        self.assertTrue(physics_base_files(base)["declared"])
        (base / "data" / "aero.ini").write_bytes(b"[WING_0]\r\nNAME=OTHER\r\n")
        with self.assertRaises(AssemblyError):
            physics_base_files(base)

    def test_strip_section_keeps_other_bytes(self):
        raw = b"[A]\nX=1\n[GRAPHICS]\nD=1\n[B]\nY=2\n"
        out, removed = pb.strip_section(raw, "GRAPHICS")
        self.assertEqual(out, b"[A]\nX=1\n[B]\nY=2\n")
        self.assertEqual(removed, ["[GRAPHICS]", "D=1"])
        self.assertEqual(
            pb.strip_section(b"[A]\nX=1\n", "GRAPHICS"), (b"[A]\nX=1\n", [])
        )
        with self.assertRaises(pb.PhysicsBaseError):
            pb.strip_section(b"[GRAPHICS]\n[GRAPHICS]\n", "GRAPHICS")


_PIPELINE: dict[str, bytes] = {}


def pipeline_physics() -> dict[str, bytes]:
    """Física de las fases 3–5 de la muestra (la que el ensamblador sabe comprobar),
    con [REAR] TYPE añadido para que cumpla los requisitos de carga."""
    if not _PIPELINE:
        from src.ac import chassis as ac_chassis
        from src.ac.drivetrain import analyse as analyse_drivetrain
        from src.ac.drivetrain import write_drivetrain_files
        from src.ac.engine import write_engine_files
        from src.extract.engine_camso import build_engine_model
        from src.jbeam import PartIndex, load_pc, resolve

        r = resolve(PartIndex.from_directories(VEHICLE), load_pc(PC))
        with tempfile.TemporaryDirectory() as d:
            art = Path(d)
            write_engine_files(build_engine_model(r), art / "engine")
            write_drivetrain_files(analyse_drivetrain(r), art / "drivetrain")
            ac_chassis.write_chassis_files(ac_chassis.analyse(r), art / "chassis")
            for sub, names in (
                ("engine", ("engine.ini", "power.lut")),
                ("drivetrain", ("drivetrain.ini",)),
                ("chassis", ("suspensions.ini", "brakes.ini", "tyres.ini")),
            ):
                for n in names:
                    _PIPELINE[f"data/{n}"] = (art / sub / n).read_bytes()
        susp = _PIPELINE["data/suspensions.ini"]
        nl = b"\r\n" if b"\r\n" in susp else b"\n"
        assert susp.count(b"[REAR]" + nl) == 1
        _PIPELINE["data/suspensions.ini"] = susp.replace(
            b"[REAR]" + nl, b"[REAR]" + nl + b"TYPE=DWB" + nl
        )
    return dict(_PIPELINE)


class TestAssembleWithDeclaredBase(Tmp):
    def _run(self, base_root: Path, out: Path) -> dict:
        return assemble(
            VEHICLE,
            PC,
            "seat_ibiza_acfr",
            self.tmp / "unused",
            out,
            with_kn5=False,
            physics_base=base_root,
        )

    def _package(self, drop=()) -> Path:
        return make_package(self.tmp, pipeline_physics(), ("data/wear.lut", *drop))

    def test_assemble_uses_exactly_the_declared_physics(self):
        m = pb.freeze(self._package(), self.tmp / "bases", "base_x", "seat_ibiza_acfr")
        base_root = Path(m["package_dir"]).parent  # se admite la carpeta de la base
        report = self._run(base_root, self.tmp / "out1")
        car = Path(report["car_dir"])
        base = Path(m["package_dir"])
        copied = sorted(r for r in m["physics_files"] if r != "data/car.ini")
        self.assertEqual(sorted(report["physics_base"]["files"]), copied)
        for rel in copied:
            self.assertEqual((car / rel).read_bytes(), (base / rel).read_bytes(), rel)
        self.assertEqual(report["physics_base"]["changed"], [])
        self.assertEqual(report["physics_base"]["base_id"], "base_x")
        self.assertEqual(
            read_ini(car / "data/drivetrain.ini")["GEARBOX"]["VALID_SHIFT_RPM_WINDOW"],
            "800",
        )
        for excluded in ("dash_cam.ini", "lights.ini", "sounds.ini"):
            self.assertFalse((car / "data" / excluded).exists(), excluded)
        self.assertFalse((car / "sfx").exists() or (car / "animations").exists())
        ini = read_ini(car / "data/car.ini")
        self.assertEqual(list(ini["GRAPHICS"]), ["DRIVEREYES"])  # sin cámaras
        self.assertEqual(ini["CONTROLS"]["STEER_LOCK"], "423")
        checks = {c["check"]: c["ok"] for c in report["validation"]["checks"]}
        self.assertTrue(checks[LOAD_REAR_TYPE])
        self.assertTrue(checks[LOAD_AERO])
        self.assertTrue(
            checks[
                "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0 "
                "(ensamblado.md §9.15)"
            ]
        )
        self.assertTrue(checks["base física: archivos copiados idénticos byte a byte"])
        self.assertTrue(
            checks["car.ini: física de la base intacta (salvo opciones experimentales)"]
        )
        # Solo falla lo que depende del KN5 (sin Blender) y, desde 2026-10-26,
        # MAX_TORQUE: esta base se construye con los BORRADORES de las fases 3–5
        # (pipeline_physics) y el borrador de brakes.ini solo escribe FRONT_SHARE (AC
        # leería 0: coche sin frenos). El error se exige; FINAL sí está y debe pasar.
        errors = report["validation"]["errors"]
        brake = [e for e in errors if e.startswith("brakes.ini [DATA] MAX_TORQUE")]
        self.assertEqual(len(brake), 1, errors)
        self.assertFalse(
            any(e.startswith("drivetrain.ini [GEARS] FINAL") for e in errors)
        )
        self.assertTrue(
            all("KN5" in e or "kn5" in e for e in errors if e not in brake), errors
        )

    def test_repeated_assembly_refused_and_deterministic(self):
        m = pb.freeze(self._package(), self.tmp / "bases", "base_x", "seat_ibiza_acfr")
        r1 = self._run(Path(m["package_dir"]), self.tmp / "out1")
        car1 = Path(r1["car_dir"])
        before = tree_hashes(car1)
        with self.assertRaises(AssemblyError):  # misma salida: no se pisa
            self._run(Path(m["package_dir"]), self.tmp / "out1")
        self.assertEqual(tree_hashes(car1), before)
        r2 = self._run(Path(m["package_dir"]), self.tmp / "out2")
        self.assertEqual(tree_hashes(Path(r2["car_dir"])), before)
        self.assertEqual(
            (car1 / "data/car.ini").read_text(encoding="utf-8").count("[GRAPHICS]"), 1
        )

    def test_legacy_package_base_with_graphics_now_accepted(self):
        """Un paquete con [GRAPHICS] (v7–v18) ya no hace fallar add_drivereyes."""
        pkg = self._package(("data/dash_cam.ini", "data/lights.ini", "data/sounds.ini"))
        report = self._run(pkg, self.tmp / "out")
        ini = read_ini(Path(report["car_dir"]) / "data/car.ini")
        self.assertEqual(
            sorted(ini["GRAPHICS"]),
            ["BONNET_CAMERA_POS", "BUMPER_CAMERA_POS", "DRIVEREYES"],
        )
        checks = {c["check"]: c["ok"] for c in report["validation"]["checks"]}
        self.assertTrue(
            checks["car.ini: física de la base intacta (salvo opciones experimentales)"]
        )


class TestRealFrozenBase(unittest.TestCase):
    """La base real congelada desde pkg_v18bu (solo lectura; se omite si no existe)."""

    def test_real_base_matches_v18bu(self):
        bases = (
            sorted(REAL_BASES.glob("*/manifest.json")) if REAL_BASES.is_dir() else []
        )
        bases = [
            b
            for b in bases
            if json.loads(b.read_text(encoding="utf-8"))
            .get("source", {})
            .get("package_dir", "")
            .find("pkg_v18bu")
            >= 0
        ]
        if not bases:
            self.skipTest("no hay base congelada desde pkg_v18bu")
        v18 = json.loads(
            (IC / "pkg_v18bu" / "manifest.json").read_text(encoding="utf-8")
        )
        v18_car = Path(v18["package_dir"])
        for man in bases:
            with self.subTest(base=man.parent.name):
                m = json.loads(man.read_text(encoding="utf-8"))
                self.assertEqual(m["kind"], pb.KIND)
                self.assertEqual(
                    m["source"]["manifest_sha256"],
                    sha256(IC / "pkg_v18bu" / "manifest.json"),
                )
                info = pb.load_declared(Path(m["package_dir"]), m)
                for rel, h in info["files"].items():
                    self.assertEqual(h, v18["files"][rel], rel)  # byte a byte
                stripped, _ = pb.strip_section(
                    (v18_car / "data/car.ini").read_bytes(), "GRAPHICS"
                )
                self.assertEqual(
                    sha256(Path(m["package_dir"]) / "data/car.ini"),
                    __import__("hashlib").sha256(stripped).hexdigest(),
                )
                self.assertEqual(
                    read_ini(Path(m["package_dir"]) / "data/drivetrain.ini")["GEARBOX"][
                        "VALID_SHIFT_RPM_WINDOW"
                    ],
                    "800",
                )


if __name__ == "__main__":
    unittest.main()
