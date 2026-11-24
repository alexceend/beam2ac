"""G2.8: genericidad del IR y de los extractores (estados y supuestos heredados).

EVIDENCIA SINTÉTICA: las variantes son COPIAS del Ibiza (en una carpeta temporal, nunca
en ``sample_*``) con UN cambio de texto en un JBeam. Prueban que el flujo real
(``load_vehicle_dir`` → ``preview_physics`` → ``import_preview``) clasifica bien la
estructura; NO son evidencia de que un vehículo real con esa estructura se convierta
bien (no hay ninguno en los 15 proyectos; informe G2.8 §6).

* ``awd_sin_central``: las ruedas traseras cuelgan del MISMO diferencial que las
  delanteras (``frontDiff`` salidas 1 y 2): tracción total sin central reconocido. Antes
  el reparto y el central quedaban «no aplica: sin diferencial central».
* ``compresor``: la sección del motor nombra ``supercharger`` en vez de
  ``turbocharger``. Antes ``engine.forced_induction`` era ``False``.
* ``atmosferico``: sin ninguna de las dos claves → ``False`` (sigue igual).
* ``deposito_renombrado``: el ``fuelTank`` se llama ``tankA``. Antes no se encontraba
  (se suponía ``mainTank``).
* ``dos_depositos``: dos ``fuelTank`` → ausente con motivo (suma sin verificar).
"""

from __future__ import annotations

import pytest

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.importers.beamng import _explain_missing, import_preview
from src.ir.architecture import classify
from src.ir.legacy_facts import ir_to_facts
from src.ir.vehicle import VehicleIR
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
AUTIST = ROOT / "samples_beamng/thershow_autist_cuanto/vehicles/autist_cuanto"
SKIP = ("*.dds", "*.dae", "*.png", "*.cdae", "*.jpg")
HUB_R = "camso_hubbearings_rear.jbeam"
ENGINE = "eng_83f4c/camso_engine_83f4c.jbeam"
TANK = "35b84/camso_fueltank_35b84.jbeam"
TURBO_KEY = '"turbocharger":"turbocharger" //Just'
VARIANTS: dict[str, list[tuple[str, str, str]]] = {
    "original": [],
    "awd_sin_central": [
        (HUB_R, '"wheelaxleRL", "halfshaftRL", 1', '"wheelaxleRL", "frontDiff", 1'),
        (HUB_R, '"wheelaxleRR", "halfshaftRR", 1', '"wheelaxleRR", "frontDiff", 2'),
    ],
    "compresor": [(ENGINE, TURBO_KEY, '"supercharger":"turbocharger" //Just')],
    "atmosferico": [(ENGINE, TURBO_KEY, '"sinUso":"turbocharger" //Just')],
    "deposito_renombrado": [
        (TANK, '["fuelTank", "mainTank"]', '["fuelTank", "tankA"]'),
        (TANK, '"mainTank": {', '"tankA": {'),
    ],
    "dos_depositos": [
        (
            TANK,
            '["fuelTank", "mainTank"]',
            '["fuelTank", "mainTank"],\n["fuelTank", "b"]',
        )
    ],
}


def _reasonless(ir: VehicleIR) -> list[str]:
    return [
        p
        for p, x in ir.walk()
        if x.status in ("missing", "not_applicable", "unparsed") and not x.reason
    ]


@unittest.skipUnless(IBIZA.is_dir(), "falta sample_beamng")
class TestIbizaVariants(unittest.TestCase):
    pv: dict = {}
    ir: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        for name, edits in VARIANTS.items():
            dst = Path(cls._tmp.name) / name / "seat_ibiza_acfr"
            shutil.copytree(IBIZA, dst, ignore=shutil.ignore_patterns(*SKIP))
            for rel, old, new in edits:
                f = dst / rel
                text = f.read_text(encoding="utf-8")
                assert text.count(old) >= 1, (name, rel, old)
                f.write_text(text.replace(old, new), encoding="utf-8")
            pv = preview_physics(load_vehicle_dir(dst, "35b84.pc"), 70.0)
            cls.pv[name] = pv
            cls.ir[name] = import_preview(pv, 70.0)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_every_absent_has_reason(self) -> None:
        for name, ir in self.ir.items():
            with self.subTest(variant=name):
                self.assertEqual(_reasonless(ir), [])

    def test_original_differentials_consistent(self) -> None:
        ir = self.ir["original"]
        for role in ("center", "rear"):
            for path, p in ir.walk():
                if path.startswith(f"drivetrain.differentials.{role}."):
                    self.assertEqual(p.status, "not_applicable", path)
        # el eje motriz (lsd en 35b84) conserva sus valores
        self.assertTrue(ir.get("drivetrain.differentials.front.kind").present)

    def test_awd_without_center_is_not_not_applicable(self) -> None:
        ir = self.ir["awd_sin_central"]
        self.assertEqual(ir.get("drivetrain.layout").value, "AWD")
        share = ir.get("drivetrain.front_torque_share")
        self.assertEqual(share.status, "missing")
        self.assertIn("sin diferencial central reconocido", share.reason)
        for path, p in ir.walk():
            if path.startswith("drivetrain.differentials.center."):
                self.assertEqual(p.status, "missing", path)
        self.assertEqual(ir.get("drivetrain.driven_axle").status, "missing")
        self.assertTrue(
            any(
                u.locator == "frontDiff" and isinstance(u.raw, dict) for u in ir.unknown
            )
        )
        facts = ir_to_facts(ir)
        self.assertNotIn("ir.drivetrain.front_torque_share", facts)
        self.assertNotIn("bng.awd.front_share", facts)
        # ninguna arquitectura de vehículo del catálogo encaja (no se supone una)
        self.assertIsNone(classify(ir)["vehicle"])

    def test_supercharger_counts_as_forced_induction(self) -> None:
        ir, pv = self.ir["compresor"], self.pv["compresor"]
        self.assertIs(ir.get("engine.forced_induction").value, True)
        self.assertIsNone(pv.engine.raw.turbo_section_name)
        self.assertEqual(pv.engine.raw.supercharger_section_name, "turbocharger")
        self.assertTrue(pv.engine.status["supercharger"].startswith("pendiente"))
        self.assertTrue(any("supercharger" in u.reason for u in ir.unknown))

    def test_naturally_aspirated_and_turbo(self) -> None:
        self.assertIs(
            self.ir["atmosferico"].get("engine.forced_induction").value, False
        )
        self.assertIs(self.ir["original"].get("engine.forced_induction").value, True)
        self.assertNotIn("supercharger", self.pv["original"].engine.status)

    def test_fuel_tank_found_by_device_not_by_name(self) -> None:
        for name in ("original", "deposito_renombrado"):
            with self.subTest(variant=name):
                self.assertEqual(self.ir[name].get("mass.fuel_capacity").value, 55)
        ir = self.ir["dos_depositos"]
        p = ir.get("mass.fuel_capacity")
        self.assertEqual(p.status, "missing")
        self.assertIn("varios depósitos", p.reason)
        self.assertNotIn("car.ini", self.pv["dos_depositos"].rendered)

    def test_variants_change_only_their_subsystem(self) -> None:
        """Las INI de motor de las variantes de depósito y tracción son las del
        original (cada variante cambia una sola cosa)."""
        base = self.pv["original"].rendered
        for name in ("awd_sin_central", "deposito_renombrado", "dos_depositos"):
            self.assertEqual(
                self.pv[name].rendered["power.lut"], base["power.lut"], name
            )
            self.assertEqual(
                self.pv[name].rendered["engine.ini"], base["engine.ini"], name
            )


@unittest.skipUnless(AUTIST.is_dir(), "falta samples_beamng")
class TestAwdWithCenter(unittest.TestCase):
    def test_awd_center_fields(self) -> None:
        pv = preview_physics(load_vehicle_dir(AUTIST, "b78dc.pc"), 70.0)
        ir = import_preview(pv, 70.0)
        self.assertEqual(_reasonless(ir), [])
        self.assertEqual(ir.get("drivetrain.driven_axle").status, "not_applicable")
        for path, p in ir.walk():
            if path.startswith("drivetrain.driven_axle_differential."):
                self.assertEqual(p.status, "not_applicable", path)
        self.assertEqual(
            ir.get("drivetrain.differentials.center.torque_share").status,
            "not_applicable",
        )
        self.assertTrue(ir.get("drivetrain.front_torque_share").present)


SAMPLES = ROOT / "samples_beamng"


@unittest.skipUnless(SAMPLES.is_dir(), "falta samples_beamng")
class TestRealSamplesReasons(unittest.TestCase):
    """Vehículos REALES (solo lectura): todo dato ausente, no aplicable o sin
    interpretar lleva motivo, también en los que el conversor no sabe convertir (los
    7 del exportador 200409 añadidos el 2026-11-01: motor ``combustionEngine``, cajas
    ``dctGearbox``/convertidor de par)."""

    @pytest.mark.ampliada
    def test_every_absent_has_reason(self) -> None:
        dirs = sorted(
            v
            for top in SAMPLES.iterdir()
            if (top / "vehicles").is_dir()
            for v in (top / "vehicles").iterdir()
            if len(list(v.glob("*.pc"))) == 1
        )
        self.assertTrue(dirs)
        for vdir in dirs:
            with self.subTest(vehicle=vdir.name):
                pc = next(vdir.glob("*.pc")).name
                pv = preview_physics(load_vehicle_dir(vdir, pc), 70.0)
                self.assertEqual(_reasonless(import_preview(pv, 70.0)), [])


class TestExplainMissing(unittest.TestCase):
    def _pv(self, stages: dict) -> SimpleNamespace:
        return SimpleNamespace(stages=stages)

    def test_failed_and_not_run_steps(self) -> None:
        ir = VehicleIR()
        _explain_missing(
            ir,
            self._pv(
                {
                    "engine_model": {"ok": False, "error": "motor X sin adaptador"},
                    "mass_geometry": {"ok": True},
                }
            ),
            is_automation=False,
        )
        self.assertEqual(
            ir.get("engine.idle_speed").reason,
            "paso 'engine_model' fallido: motor X sin adaptador",
        )
        self.assertIn("no ejecutado", ir.get("drivetrain.layout").reason)
        # paso correcto: no se inventa un motivo
        self.assertEqual(ir.get("mass.total").reason, "")
        self.assertIn("Automation", ir.get("formats.exporter_version").reason)

    def test_does_not_touch_present_or_existing_reasons(self) -> None:
        from src.ir.param import Param

        ir = VehicleIR()
        ir.engine.idle_speed = Param.of(900.0, "rpm", "extracted")
        ir.engine.inertia = Param.missing("kg·m²", "motivo previo")
        _explain_missing(
            ir, self._pv({"engine_model": {"ok": False, "error": "e"}}), True
        )
        self.assertTrue(ir.engine.idle_speed.present)
        self.assertEqual(ir.engine.inertia.reason, "motivo previo")
        self.assertEqual(ir.get("formats.exporter_version").reason, "")


if __name__ == "__main__":
    unittest.main()
