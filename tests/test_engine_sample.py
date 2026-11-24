"""Regresión del motor sobre la muestra real (solo lectura)."""

import hashlib
import unittest

from src.ac.engine import (
    fit_coast_ref,
    has_turbo_section,
    render_engine_ini,
    render_power_lut,
)
from src.extract.engine_camso import HP_W, build_engine_model, compare_with_info
from src.jbeam import PartIndex, load, load_pc, resolve
from src.jbeam.merge import merge_section
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
BASELINE = PROJECT_ROOT / "docs" / "baseline" / "power_curve_baseline.lut"


def _fingerprint():
    return {
        str(p.relative_to(VEHICLE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(VEHICLE.rglob("*"))
        if p.suffix in (".jbeam", ".pc", ".json")
    }


class TestSampleEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _fingerprint()
        cls.resolved = resolve(
            PartIndex.from_directories(VEHICLE), load_pc(VEHICLE / "35b84.pc")
        )
        cls.m = build_engine_model(cls.resolved)
        cls.checks = {
            c.quantity: c
            for c in compare_with_info(cls.m, load(VEHICLE / "info_35b84.json").value)
        }

    def test_merge_of_main_engine(self):
        merged = merge_section(self.resolved, "mainEngine")
        self.assertEqual(
            merged.parts,
            ["Camso_Engine_83f4c", "Camso_EngineManagement_83f4c", "Camso_Turbo_83f4c"],
        )
        self.assertEqual(list(merged.conflicts), ["hasRevLimiter"])
        self.assertEqual(merged.operators, [])
        self.assertEqual(merged.get("revLimiterRPM"), 10000)

    def test_peaks_match_info(self):
        self.assertEqual(self.m.peak_torque.rpm, 7000)
        self.assertAlmostEqual(self.m.peak_torque.net, 307.80, places=2)
        self.assertEqual(self.m.peak_power.rpm, 9300)
        self.assertAlmostEqual(self.m.peak_power.power_w / HP_W, 339.26, places=2)
        for name in ("Torque (Nm)", "TorquePeakRPM", "Power (hp)", "PowerPeakRPM"):
            self.assertIn("compatible", self.checks[name].explanation, name)
        implied = self.checks["Power implícita Weight/(Weight/Power) (hp)"]
        self.assertAlmostEqual(implied.reference, 339.408, places=3)
        self.assertLess(abs(implied.difference), 0.2)

    def test_limits(self):
        m = self.m
        self.assertEqual(m.rpm_ceiling, 10000)
        self.assertIsNone(m.has_cut_limiter)  # depende de un conflicto…
        self.assertIn("independiente", m.rpm_ceiling_basis)  # …pero no el techo
        self.assertEqual(m.raw.idle_rpm, 1400)
        self.assertAlmostEqual(m.raw.inertia, 0.104578, places=6)
        self.assertAlmostEqual(m.overrev_damage_rpm, 12600)
        self.assertEqual(m.raw.rev_limiter_rpm_drop, 100)
        self.assertIsNone(m.raw.rev_limiter_type)  # el Lua usa "rpmDrop" por defecto

    def test_turbo(self):
        self.assertAlmostEqual(self.m.max_boost_bar, 1.84, places=6)
        self.assertEqual(self.m.full_boost_rpm, 6600)
        self.assertAlmostEqual(self.m.raw.turbo["wastegateLimit"], 43.206168632695)

    def test_curve_against_baseline(self):
        # El baseline antiguo es throttle 100 bruto. La fila 99 difiere hasta un
        # 2,2 % (6,46 Nm a 6300 rpm, zona de entrada del boost); en el pico solo
        # un 0,07 %. Corrige la afirmación previa "< 0,3 %" (solo 4 puntos).
        baseline = {}
        for line in BASELINE.read_text(encoding="utf-8").splitlines():
            rpm, torque = line.split("|")
            baseline[float(rpm)] = float(torque)
        diffs = []
        for p in self.m.curve:
            self.assertAlmostEqual(p.net, p.gross - p.friction)
            if p.rpm in baseline and p.rpm >= 1400:
                diffs.append((abs(p.gross - baseline[p.rpm]) / baseline[p.rpm], p.rpm))
        worst, worst_rpm = max(diffs)
        self.assertAlmostEqual(worst, 0.0224, places=4)
        self.assertEqual(worst_rpm, 6300)

    def test_duplicate_and_coast(self):
        self.assertEqual(self.m.duplicate_rpms, [(200, [0.0, 27.43])])
        fit = fit_coast_ref(self.m)
        self.assertEqual(fit.rpm, 10000)
        self.assertAlmostEqual(fit.torque, 66.0825, places=4)
        self.assertAlmostEqual(dict(self.m.coast)[3000], 38.61, places=2)

    def test_ac_representation(self):
        lut = render_power_lut(self.m).splitlines()
        self.assertEqual(len(lut), 101)
        self.assertIn("7000|307.80", lut)
        self.assertEqual(lut[-1], "10000|236.39")
        self.assertFalse(
            has_turbo_section(render_engine_ini(self.m, fit_coast_ref(self.m)))
        )

    def test_samples_not_modified(self):
        self.assertEqual(_fingerprint(), self.before)


if __name__ == "__main__":
    unittest.main()
