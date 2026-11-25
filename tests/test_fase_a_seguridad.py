"""Fase A (revisión 2026-11-10): seguridad de escritura y reproducibilidad.

Cubre lo que la auditoría encontró abierto frente al código actual
(``docs/research/fase_a_seguridad_reproducibilidad_2026-11-10.md``):

* producción y versiones históricas SIN ``manifest.json`` protegidas por ruta
  (``safe_paths.HISTORICAL_DIRS``), también frente a ``assemble --overwrite``;
* ``car_id`` de exportación que podría sobrescribir un coche de AC al instalarlo;
* destinos del instalador del banco limitados a carpetas ``beam2ac_*`` propias;
* ``[REAR] TYPE`` y ``aero.ini`` leídos como AC (``read_ini_ac``);
* scripts originales preservados y paquetes sellados intactos (solo lectura).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src import safe_paths as S
from src.ac import car_validate as V
from src.project import export as E
from tools.acbench import install
from tools.variants import verify_originals

ROOT = S.PROJECT_ROOT
OUT = S.DEFAULT_OUTPUT_DIR


class TestHistoricalDirs(unittest.TestCase):
    def test_registry_paths_exist_and_are_under_output(self):
        for d in S.HISTORICAL_DIRS:
            self.assertTrue(S._inside(S._resolve(d), S._resolve(OUT)), d)
        # la salida de producción del ensamblador y las evidencias de G2.25
        self.assertIn(OUT / "seat_ibiza_acfr/assembly", S.HISTORICAL_DIRS)
        self.assertIn(OUT / "g225_2026-11-09", S.HISTORICAL_DIRS)

    def test_inside_rejected_siblings_allowed(self):
        prod = OUT / "seat_ibiza_acfr/assembly"
        paths = [
            prod,
            prod / "seat_ibiza_acfr" / "data" / "car.ini",
            prod / "nueva",
            OUT / "g225_2026-11-09" / "x.json",
        ]
        if S.os.name == "nt":  # Windows no distingue mayúsculas
            paths.append(OUT / "SEAT_IBIZA_ACFR" / "Assembly" / "y")
        for p in paths:
            with self.assertRaises(S.ProtectedPathError, msg=str(p)):
                S.ensure_safe_output_path(p)
            self.assertIn("HISTORICAL_DIRS", S.protection_reason(p) or "")
        # un hermano con prefijo común no está dentro
        self.assertIsNone(
            S.protection_reason(OUT / "seat_ibiza_acfr/assembly_fase_a_prueba/x")
        )

    def test_new_and_claim_refused(self):
        with self.assertRaises(S.ProtectedPathError):
            S.new_output_dir(OUT / "seat_ibiza_acfr/assembly/otra")
        with self.assertRaises(S.ProtectedPathError):
            S.claim_output_dir(OUT / "seat_ibiza_acfr/phaseC_2026-10-25/run9")

    def test_assemble_overwrite_cannot_touch_production(self):
        from src.ac import assemble

        with self.assertRaises(S.ProtectedPathError):
            assemble.assemble(
                Path("no_se_lee"),
                Path("no.pc"),
                "seat_ibiza_acfr",
                {},
                OUT / "seat_ibiza_acfr/assembly",
                None,
                overwrite=True,
            )


class FakeProject:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.id = "fake"


class TestExportCarId(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ac = self.tmp / "ac"
        (self.ac / "content" / "cars" / "beam2ac_instalado").mkdir(parents=True)
        (self.ac / "content" / "cars" / "ks_toyota_celica_st185").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prefix_required(self):
        for bad in ("ks_toyota_celica_st185", "seat_ibiza_acfr", "x"):
            with self.assertRaises(ValueError, msg=bad):
                E.check_car_id(bad)
        with self.assertRaises(ValueError):
            E.check_car_id("Beam2ac_X")  # regex [a-z0-9_]+
        self.assertEqual(E.check_car_id("beam2ac_nuevo", self.ac), [])

    def test_installed_collision_refused_case_insensitive(self):
        with self.assertRaises(ValueError) as cm:
            E.check_car_id("beam2ac_instalado", self.ac)
        self.assertIn("ya está instalado", str(cm.exception))
        (self.ac / "content" / "cars" / "BEAM2AC_MAYUS").mkdir()
        with self.assertRaises(ValueError):
            E.check_car_id("beam2ac_mayus", self.ac)

    def test_allow_flag_turns_into_warning(self):
        w = E.check_car_id("beam2ac_instalado", self.ac, allow_installed=True)
        self.assertEqual([i.code for i in w], ["paquete.car_id_instalado"])
        self.assertEqual(w[0].severity, "warning")

    def test_without_ac_root_only_prefix(self):
        self.assertEqual(E.check_car_id("beam2ac_instalado", None), [])
        self.assertIsNone(E.installed_car_collision("beam2ac_x", self.tmp / "nada"))

    def test_write_export_refuses_before_creating_attempt(self):
        proj = FakeProject(self.tmp / "proj")
        proj.root.mkdir()
        for car_id, root in (("ks_x", None), ("beam2ac_instalado", self.ac)):
            with self.assertRaises(ValueError):
                E.write_export(proj, [], [], {}, car_id, {}, {}, car_id_check_root=root)
        exports = proj.root / "exports"
        self.assertFalse(exports.exists() and any(exports.iterdir()))

    def test_allowed_collision_recorded_without_changing_options(self):
        proj = FakeProject(self.tmp / "proj")
        proj.root.mkdir()
        f = E.PackageFile("data/car.ini", b"[INFO]\nSCREEN_NAME=x\n", {"t": 1})
        res = E.write_export(
            proj,
            [f],
            [],
            {},
            "beam2ac_instalado",
            {},
            {"allow_installed_car_id": True},
            car_id_check_root=self.ac,
        )
        self.assertIn("paquete.car_id_instalado", {i.code for i in res.warnings})
        man = json.loads((res.dir / "manifest.json").read_text("utf-8"))
        self.assertEqual(man["options"], {"allow_installed_car_id": True})
        self.assertNotIn("car_id_check_root", json.dumps(man))

    def test_default_options_manifest_unchanged(self):
        proj = FakeProject(self.tmp / "proj")
        proj.root.mkdir()
        f = E.PackageFile("data/car.ini", b"[INFO]\n", {"t": 1})
        res = E.write_export(
            proj, [f], [], {}, "beam2ac_nuevo", {}, {}, car_id_check_root=self.ac
        )
        man = json.loads((res.dir / "manifest.json").read_text("utf-8"))
        self.assertEqual(man["options"], {})

    def test_cli_refuses_with_exit_code_1(self):
        from src.project.__main__ import main as cli
        from src.project.manifest import SourceSpec, create_project

        ibiza = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
        if not (ibiza / "35b84.pc").is_file():
            self.skipTest("muestra del Ibiza no disponible")
        p = create_project(self.tmp / "p", SourceSpec("dir", str(ibiza), "35b84.pc"))
        rc = cli(
            ["export", str(p.root), "--car-id", "ks_toyota_celica_st185", "--no-model"]
        )
        self.assertEqual(rc, 1)
        rc = cli(
            [
                "export",
                str(p.root),
                "--car-id",
                "beam2ac_instalado",
                "--no-model",
                "--ac-root",
                str(self.ac),
            ]
        )
        self.assertEqual(rc, 1)
        self.assertFalse((p.root / "exports").exists())


class TestInstallTargets(unittest.TestCase):
    def test_pattern(self):
        ok = (
            "content/cars/beam2ac_g225b_atlanta",
            "content/tracks/beam2ac_g222_pad",
            "apps/lua/beam2ac_lsd_bench",
        )
        bad = (
            "content/cars/ks_toyota_celica_st185",
            "content/cars/beam2ac_x/data",
            "content/cars/../cars/beam2ac_x",
            "content/cars/BEAM2AC_X",
            "content/cars",
            "system/cfg/beam2ac_x",
            "apps/python/beam2ac_x",
            "/content/cars/beam2ac_x",
        )
        for rel in ok:
            self.assertTrue(install.TARGET_PATTERN.match(rel), rel)
        for rel in bad:
            self.assertIsNone(install.TARGET_PATTERN.match(rel), rel)

    def test_install_validates_all_targets_before_copying(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            stage, ac, state = d / "stage", d / "ac", d / "state"
            (stage / "content/cars/beam2ac_ok").mkdir(parents=True)
            (stage / "content/cars/beam2ac_ok/a.txt").write_text("a")
            (stage / "content/cars/ks_ajeno").mkdir(parents=True)
            ac.mkdir()
            state.mkdir()
            man = {
                "targets": {
                    "content/cars/beam2ac_ok": {"a.txt": "x"},
                    "content/cars/ks_ajeno": {},
                }
            }
            (stage / "manifest_preparacion.json").write_text(json.dumps(man))
            with self.assertRaises(install.InstallError):
                install.install(stage, state, ac)
            self.assertFalse((ac / "content").exists())  # nada copiado
            # destino admitido pero sin preparar
            man = {"targets": {"content/cars/beam2ac_falta": {}}}
            (stage / "manifest_preparacion.json").write_text(json.dumps(man))
            with self.assertRaises(install.InstallError):
                install.install(stage, state, ac)

    def test_existing_bench_manifests_still_valid(self):
        found = 0
        for base in ("g222_2026-11-07", "g225_2026-11-09"):
            for m in (OUT / base).glob("**/manifest_preparacion.json"):
                for rel in json.loads(m.read_text("utf-8"))["targets"]:
                    found += 1
                    self.assertTrue(install.TARGET_PATTERN.match(rel), (m, rel))
        if not found:
            self.skipTest("sin preparaciones de G2.22/G2.25 en output/")


def _car(tmp: Path, susp: str, aero: str | None) -> Path:
    car = tmp / "beam2ac_t"
    (car / "data").mkdir(parents=True)
    (car / "data" / "suspensions.ini").write_text(susp, encoding="utf-8")
    if aero is not None:
        (car / "data" / "aero.ini").write_text(aero, encoding="utf-8")
    return car


def _load_checks(car: Path) -> dict[str, tuple[bool, str]]:
    v = V.validate_structure(car, ())
    return {
        c["check"]: (c["ok"], str(c["detail"]))
        for c in v.checks
        if c["check"] in (V.LOAD_REAR_TYPE, V.LOAD_AERO)
    }


class TestLoadRequirementsAsAC(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_package_passes(self):
        car = _car(
            self.tmp,
            "[FRONT]\nTYPE=DWB\n[REAR]\nTYPE=DWB ; comentario\n",
            "[WING_0]\nNAME=BODY\n",
        )
        checks = _load_checks(car)
        self.assertEqual(checks[V.LOAD_REAR_TYPE], (True, "DWB"))
        self.assertTrue(checks[V.LOAD_AERO][0])

    def test_tolerant_reader_would_accept_but_ac_does_not(self):
        cases = {
            "minúsculas": "[REAR]\ntype=DWB\n",
            "espacio antes de la clave": "[REAR]\n TYPE=DWB\n",
            "sección repetida": (
                "[REAR]\nTYPE=DWB\n[FRONT]\nTYPE=DWB\n[REAR]\nTRACK=1\n"
            ),
            "sección en minúsculas": "[rear]\nTYPE=DWB\n",
            "valor tras ;": "[REAR]\nTYPE=;DWB\n",
        }
        for why, susp in cases.items():
            car = _car(self.tmp / why.replace(" ", "_"), susp, "[WING_0]\nNAME=B\n")
            # el lector tolerante sí lo da por bueno en los casos de clave/sección
            ok, detail = _load_checks(car)[V.LOAD_REAR_TYPE]
            self.assertFalse(ok, f"{why}: {detail}")
        car = _car(self.tmp / "rep", cases["sección repetida"], "[WING_0]\nNAME=B\n")
        self.assertIn("repetida", _load_checks(car)[V.LOAD_REAR_TYPE][1])
        car = _car(self.tmp / "min", cases["minúsculas"], "[WING_0]\nNAME=B\n")
        self.assertIn("AC no reconoce", _load_checks(car)[V.LOAD_REAR_TYPE][1])

    def test_aero_missing_or_wrong_case(self):
        susp = "[REAR]\nTYPE=DWB\n"
        self.assertEqual(
            _load_checks(_car(self.tmp / "a", susp, None))[V.LOAD_AERO],
            (False, "falta data/aero.ini"),
        )
        for aero in ("[wing_0]\nNAME=B\n", "[HEADER]\nVERSION=1\n", ""):
            car = _car(self.tmp / f"b{len(aero)}", susp, aero)
            self.assertFalse(_load_checks(car)[V.LOAD_AERO][0], aero)
        car = _car(self.tmp / "c", susp, "[DATA]\nX=1\n")
        self.assertTrue(_load_checks(car)[V.LOAD_AERO][0])

    def test_tolerant_api_unchanged(self):
        inis = {"suspensions.ini": {"REAR": {"TYPE": "DWB"}}, "aero.ini": {"DATA": {}}}
        self.assertTrue(all(ok for _, ok, _ in V.load_requirements(inis)))


@unittest.skipUnless(
    verify_originals.BASE.is_dir(), "scripts originales no versionados"
)
class TestPreservedAndSealed(unittest.TestCase):
    def test_original_scripts_intact(self):
        self.assertEqual(verify_originals.verify(), [])

    def test_verifier_detects_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "orig"
            shutil.copytree(verify_originals.BASE, base)
            target = next(p for p in base.rglob("*.py"))
            target.write_bytes(target.read_bytes() + b"\n# cambio\n")
            (base / "extra.py").write_text("x = 1\n")
            problems = verify_originals.verify(base)
            self.assertTrue(any(p.startswith("cambiado") for p in problems))
            self.assertIn("sin listar extra.py", problems)

    def test_sealed_exports_intact(self):
        """Todo intento sellado de output/projects y de los proyectos de G2.23–G2.25
        coincide con su manifiesto (sha256 por archivo)."""
        roots = [OUT / "projects"] + [
            OUT / g / "proyectos"
            for g in ("g223_2026-11-09", "g224_2026-11-09", "g225_2026-11-09")
        ]
        checked = 0
        for root in roots:
            for man in sorted(root.glob("*/exports/*/manifest.json")):
                info = E.inspect_export(man.parent)
                # 2026-11-15: los paquetes de intentos antiguos (regenerables) se podaron
                # y solo quedan manifiesto e informe; se comprueba lo que sigue en disco.
                if not (man.parent / info["car_id"]).is_dir():
                    continue
                self.assertTrue(info["intact"], (str(man.parent), info))
                checked += 1
        if not checked:
            self.skipTest("sin exportaciones selladas en output/")


if __name__ == "__main__":
    unittest.main()
