"""G2.12: tracción total del exportador 200409 (IR, reglas, laboratorio).

Vehículos reales de ``samples_beamng/`` (solo lectura) y variantes SINTÉTICAS del IR
(copias modificadas en memoria) para los casos que el corpus no tiene: central con
reparto variable o bloqueado, tipo desconocido, datos ausentes, versión o dispositivo
incorrectos. Informe: ``docs/research/g212_awd_2026-11-02.md``.
"""

from __future__ import annotations

import copy
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.extract.drivetrain import Device
from src.extract.traction import DiffSetup
from src.importers.beamng import import_preview
from src.ir.param import Param
from src.lab import awd_lab as L
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
TYPE_T = "ac:drivetrain.ini/TRACTION/TYPE"
FS_T = "ac:drivetrain.ini/AWD/FRONT_SHARE"
LOCK_T = [
    f"ac:drivetrain.ini/AWD/{s}_DIFF_{k}"
    for s in ("FRONT", "REAR", "CENTRE")
    for k in ("POWER", "COAST", "PRELOAD")
]
NEW_RULES = {"dt.traction_awd@1", "dt.awd_front_share@1"} | {
    f"dt.awd_{r}_diff_{k}_open@1"
    for r in ("front", "rear", "center")
    for k in ("power", "coast", "preload")
}


def _vehicle_dir(name: str) -> Path:
    return SAMPLES / name / "vehicles" / name


@lru_cache(maxsize=None)
def _ir(name: str):
    vd = _vehicle_dir(name)
    src = load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])
    return import_preview(preview_physics(src, 70.0), 70.0)


def _resolve(ir) -> dict:
    s = ConversionSession.from_ir(ir)
    return {r.target: r for r in s.resolve()}


def _ibiza_dir() -> Path:
    return ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"


HAS_SAMPLES = (_vehicle_dir("2020_rpm_atlanta_gt_3_6t")).is_dir()


class TestLab(unittest.TestCase):
    """Ports mínimos de los dos códigos (src/lab/awd_lab.py)."""

    def test_front_share_is_fixed_torque_split(self):
        for share in (0.3, 0.5, 0.45):
            f, r = L.ac_engine_block_axle_torques(300.0, 10.0, share, 1.5, 1 / 333)
            self.assertAlmostEqual(f / (f + r), share, places=12)
            self.assertAlmostEqual(f + r, 3000.0, places=9)

    def test_lsd_factor_two_matches_beamng_transfer(self):
        t = 1000.0
        bng = L.beamng_lsd_split_mu(t, 50.0, 0.2)
        self.assertAlmostEqual(
            bng.torque_to_grip_wheel, t / 2 + 50 + 0.2 * t, delta=1.0
        )
        pre, power = L.proposed_ac_lock(50.0, 0.2)
        self.assertEqual((pre, power), (100.0, 0.4))
        ac2 = L.ac_axle_split_mu(t, pre, power)
        self.assertAlmostEqual(
            ac2.torque_to_grip_wheel, bng.torque_to_grip_wheel, delta=2.0
        )
        ac1 = L.ac_axle_split_mu(t, 50.0, 0.2)  # factor 1: transmite claramente menos
        self.assertLess(ac1.torque_to_grip_wheel, bng.torque_to_grip_wheel - 100.0)

    def test_centre_preload_scales_with_final_drive(self):
        self.assertEqual(L.proposed_ac_lock(50.0, 0.2, 3.38), (2 * 50.0 * 3.38, 0.4))

    def test_ac_lock_needs_slip_beamng_does_not(self):
        """Por debajo del límite, el LSD de BeamNG no desliza (muelle angular) y el de
        AC
        sí (ventana de 0,1 rad/s): dinámicas distintas con el mismo límite."""
        t = 400.0  # traslado pedido 400/2 − 180 = 20 N·m ≪ límite (50 + 0,2·400 = 130)
        bng = L.beamng_lsd_split_mu(t, 50.0, 0.2, ice_grip=180.0)
        ac = L.ac_axle_split_mu(t, 100.0, 0.4, ice_grip=180.0)
        self.assertLess(abs(bng.slip_speed), 1e-3)
        self.assertGreater(abs(ac.slip_speed), 1e-3)


class TestDiffDefaults(unittest.TestCase):
    def test_lsd_without_keys_uses_lua_defaults(self):
        d = DiffSetup.from_device(
            Device("differential", "c", "gearbox", 1, "p", {"diffType": "lsd"})
        )
        self.assertEqual(
            d.lsd_effective("preload"),
            (50.0, True, "lua/vehicle/powertrain/differential.lua:333"),
        )
        self.assertEqual(d.lsd_effective("lock_coef")[:2], (0.2, True))
        self.assertEqual(d.lsd_effective("rev_lock_coef")[:2], (0.2, True))

    def test_rev_lock_follows_explicit_lock_coef(self):
        d = DiffSetup.from_device(
            Device(
                "differential",
                "c",
                "gearbox",
                1,
                "p",
                {"diffType": "lsd", "lsdLockCoef": 0.4},
            )
        )
        self.assertEqual(d.lsd_effective("lock_coef")[:2], (0.4, False))
        self.assertEqual(d.lsd_effective("rev_lock_coef")[:2], (0.4, True))

    def test_explicit_values_kept(self):
        d = DiffSetup.from_device(
            Device(
                "differential",
                "c",
                "gearbox",
                1,
                "p",
                {
                    "diffType": "lsd",
                    "lsdPreload": 10,
                    "lsdLockCoef": 0.4,
                    "lsdRevLockCoef": 0.1,
                },
            )
        )
        self.assertEqual(
            [d.lsd_effective(k)[:2] for k in ("preload", "lock_coef", "rev_lock_coef")],
            [(10, False), (0.4, False), (0.1, False)],
        )

    def test_not_lsd_has_no_defaults(self):
        for kind in ("open", "locked", "viscous", ["open", "locked"], "desconocido"):
            d = DiffSetup.from_device(
                Device("differential", "c", "gearbox", 1, "p", {"diffType": kind})
            )
            self.assertIsNone(d.lsd_effective("preload"))


class TestCatalogRules(unittest.TestCase):
    """Rechazo de reglas cuando faltan condiciones (invariante de G2.12)."""

    def test_new_rules_have_explicit_conditions(self):
        cat = Catalog.load(REPO_CATALOG)
        for key in NEW_RULES:
            r = cat.rules[key]
            conds = {(c.fact, c.op, str(c.value)) for c in r.conditions}
            self.assertIn(
                ("ir.formats.exporter_version", "in", "['200409']"), conds, key
            )
            self.assertIn(
                ("ir.engine.device_type", "eq", "combustionEngine"), conds, key
            )
            self.assertIn(("ir.drivetrain.layout", "eq", "AWD"), conds, key)
            self.assertIn(
                ("ir.drivetrain.differentials.center.kind", "in", "['open', 'lsd']"),
                conds,
                key,
            )
            self.assertEqual(r.scope.family, "automation_export")
            self.assertNotEqual(r.status, "validated", key)  # sin prueba en AC

    def test_no_rule_for_lsd_locks(self):
        cat = Catalog.load(REPO_CATALOG)
        for t in LOCK_T:
            for r in cat.rules_for(t):
                if r.key in NEW_RULES:
                    kinds = [
                        c.value
                        for c in r.conditions
                        if c.fact.endswith(".kind") and c.op == "eq"
                    ]
                    self.assertEqual(kinds, ["open"], r.key)

    def test_old_traction_rule_not_widened(self):
        cat = Catalog.load(REPO_CATALOG)
        r = cat.rules["dt.traction@3"]
        conds = {(c.fact, str(c.value)) for c in r.conditions}
        self.assertIn(("ir.formats.exporter_version", "['38022', '40340']"), conds)


@unittest.skipUnless(HAS_SAMPLES, "samples_beamng no disponible")
class TestSevenVehicles(unittest.TestCase):
    def test_lsd_defaults_in_ir(self):
        ir = _ir("2020_rpm_atlanta_gt_3_6t")
        for role in ("center", "front", "rear"):
            d = ir.drivetrain.differentials[role]
            self.assertEqual(d.kind.value, "lsd")
            for field, value in (
                ("preload", 50.0),
                ("lock_coef", 0.2),
                ("rev_lock_coef", 0.2),
            ):
                p = getattr(d, field)
                self.assertTrue(p.present, (role, field))
                self.assertEqual((p.value, p.kind), (value, "computed"))
                self.assertIn("differential.lua", p.note)

    def test_fixed_awd_50(self):
        res = _resolve(_ir("2020_rpm_atlanta_gt_3_6t"))
        self.assertEqual(
            (res[TYPE_T].value, res[TYPE_T].rule), ("AWD", "dt.traction_awd@1")
        )
        self.assertEqual(
            (res[FS_T].value, res[FS_T].rule), (50.0, "dt.awd_front_share@1")
        )
        # lsd: sin valor de regla en G2.12 (D-G212-LSD). G2.23 activó las 9 reglas LSD
        # (tests/test_g223_lsd_rules.py): se comprueba sobre el catálogo de antes.
        cat = Catalog.load(REPO_CATALOG)
        for key in [k for k in cat.rules if "_lsd_200409@" in k]:
            cat.rules.pop(key)
        old = {
            r.target: r
            for r in ConversionSession(_ir("2020_rpm_atlanta_gt_3_6t"), cat).resolve()
        }
        for t in LOCK_T:
            self.assertNotEqual(old[t].source, "rule", t)
            self.assertIsNone(old[t].value, t)

    def test_fixed_awd_30_front(self):
        res = _resolve(_ir("2020_rpm_prothean_s"))
        self.assertAlmostEqual(res[FS_T].value, 30.0, places=9)

    def test_open_diffs_zero(self):
        res = _resolve(_ir("2021_rpm_portland_touring"))
        self.assertEqual(res[TYPE_T].value, "AWD")
        self.assertEqual(res[FS_T].value, 50.0)
        for t in LOCK_T:
            self.assertEqual((res[t].value, res[t].source), (0, "rule"), t)

    def test_open_diff_has_no_lsd_defaults(self):
        d = _ir("2021_rpm_portland_touring").drivetrain.differentials["front"]
        self.assertFalse(d.preload.present)

    def test_locked_centre_vehicles_still_without_rules(self):
        # G2.12 fijaba «sin tracción en el IR» porque la geometría fallaba. G2.13 la
        # desbloquea (reglas de nodeOffset e ids repetidos del juego): la tracción
        # total llega al IR, pero el central es `locked` (D-G212-LOCK) y ninguna regla
        # AWD lo acepta: TYPE y FRONT_SHARE siguen sin resolver por regla.
        for name in ("2020_rpm_atacama_sport__4x4", "2020_rpm_patagonia_xt_v8"):
            ir = _ir(name)
            self.assertEqual(ir.drivetrain.layout.value, "AWD", name)
            self.assertEqual(
                ir.drivetrain.differentials["center"].kind.value, "locked", name
            )
            # G2.44 (D-G212-LOCK) añadió las reglas `*_lockc@1`/`*_openmode@1`: la afirmación
            # original se evalúa sobre el catálogo de antes (precedente G2.23).
            cat = Catalog.load(REPO_CATALOG)
            for key in [k for k in cat.rules if "_lockc@" in k or "_openmode@" in k]:
                cat.rules.pop(key)
            res = {
                r.target: r for r in ConversionSession(copy.deepcopy(ir), cat).resolve()
            }
            self.assertNotEqual(res[TYPE_T].source, "rule", name)
            self.assertIsNone(res[FS_T].value, name)
            now = _resolve(ir)
            self.assertEqual(now[TYPE_T].rule, "dt.traction_awd_lockc@1", name)

    # --- variantes sintéticas del IR -------------------------------------------------
    def _variant(self, name: str = "2020_rpm_atlanta_gt_3_6t"):
        return copy.deepcopy(_ir(name))

    def _set(self, ir, path: str, value, unit: str = "enum"):
        ir.set(path, Param.of(value, unit, "extracted"))

    def test_variable_or_locked_centre_rejected(self):
        for kind in ("viscous", "locked", "activeLock", "torqueVectoring"):
            ir = self._variant()
            self._set(ir, "drivetrain.differentials.center.kind", kind)
            res = _resolve(ir)
            self.assertNotEqual(res[TYPE_T].source, "rule", kind)
            self.assertIsNone(res[FS_T].value, kind)
            rejected = {x["rule"] for x in res[TYPE_T].rejected}
            self.assertIn("dt.traction_awd@1", rejected, kind)

    def test_unknown_axle_diff_type_not_zeroed(self):
        ir = self._variant("2021_rpm_portland_touring")
        self._set(ir, "drivetrain.differentials.front.kind", "superDiff")
        res = _resolve(ir)
        for k in ("POWER", "COAST", "PRELOAD"):
            self.assertIsNone(res[f"ac:drivetrain.ini/AWD/FRONT_DIFF_{k}"].value)
            self.assertEqual(res[f"ac:drivetrain.ini/AWD/REAR_DIFF_{k}"].value, 0)

    def test_missing_split_not_invented(self):
        ir = self._variant()
        ir.set(
            "drivetrain.front_torque_share", Param.missing("1", "sintético: ausente")
        )
        res = _resolve(ir)
        self.assertIsNone(res[FS_T].value)
        self.assertEqual(res[TYPE_T].value, "AWD")

    def test_ambiguous_centre_kind_missing(self):
        ir = self._variant()
        ir.set(
            "drivetrain.differentials.center.kind", Param.missing("enum", "sintético")
        )
        res = _resolve(ir)
        self.assertNotEqual(res[TYPE_T].source, "rule")

    def test_unsupported_exporter_versions(self):
        for version in ("38022", "40340", "999999"):
            ir = self._variant()
            self._set(ir, "formats.exporter_version", version, "text")
            res = _resolve(ir)
            for t in [TYPE_T, FS_T] + LOCK_T:
                self.assertNotIn(res[t].rule, NEW_RULES, (version, t))

    def test_wrong_engine_device(self):
        ir = self._variant()
        self._set(ir, "engine.device_type", "camsoEngine", "enum")
        res = _resolve(ir)
        for t in [TYPE_T, FS_T] + LOCK_T:
            self.assertNotIn(res[t].rule, NEW_RULES, t)


AUTIST = SAMPLES / "thershow_autist_cuanto" / "vehicles" / "autist_cuanto"


class TestCamsoUnchanged(unittest.TestCase):
    """camsoEngine (38022/40340): ninguna regla de G2.12 se aplica."""

    @unittest.skipUnless(_ibiza_dir().is_dir(), "Ibiza no disponible")
    def test_ibiza_traction_still_from_old_rule(self):
        src = load_vehicle_dir(_ibiza_dir(), "35b84.pc")
        res = _resolve(import_preview(preview_physics(src, 70.0), 70.0))
        self.assertEqual(
            (res[TYPE_T].value, res[TYPE_T].rule), ("FWD", "dt.traction@3")
        )
        for t in [FS_T] + LOCK_T:
            self.assertNotIn(res[t].rule, NEW_RULES, t)

    @unittest.skipUnless(AUTIST.is_dir(), "autist_cuanto no disponible")
    def test_camso_awd_40340_untouched(self):
        src = load_vehicle_dir(AUTIST, sorted(p.name for p in AUTIST.glob("*.pc"))[0])
        ir = import_preview(preview_physics(src, 70.0), 70.0)
        self.assertEqual(ir.engine.device_type.value, "camsoEngine")
        self.assertEqual(ir.drivetrain.layout.value, "AWD")
        # valores explícitos del JBeam: extraídos, no valores por defecto del Lua
        c = ir.drivetrain.differentials["center"]
        self.assertEqual((c.preload.value, c.preload.kind), (10, "extracted"))
        res = _resolve(ir)
        # G2.30: tiene sus PROPIAS reglas (40340 + camsoEngine), nunca las del 200409
        for t in [TYPE_T, FS_T] + LOCK_T:
            self.assertNotIn(res[t].rule, NEW_RULES, t)
            self.assertTrue((res[t].rule or "").endswith("_40340@1"), t)


if __name__ == "__main__":
    unittest.main()
