"""G2.12.1: validación experimental del bloqueo LSD (D-G212-LSD).

Dos bloques:

* Evidencia del banco en BeamNG (``output/g2121_lsd_validation_2026-11-02/
  resultados.json``; se omite si no existe): la mitad BeamNG del factor ×2 está MEDIDA
  en el juego (diferencia de bloqueo = 2·lsdTorque, valores por defecto instanciados,
  ``open`` sin transferencia, repetibilidad).
* Producción: la mitad AC NO está medida, así que el factor no se aplica: los 9
  bloqueos de los 4 vehículos LSD siguen en ``manual_required`` sin regla.

Informe: ``docs/research/g2121_lsd_validation_2026-11-02.md``.
"""

from __future__ import annotations

import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.importers.beamng import import_preview
from src.lab import awd_lab as L
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "output/g2121_lsd_validation_2026-11-02/resultados.json"
SAMPLES = ROOT / "samples_beamng"
LSD_CARS = (
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_prothean_s",
    "rpm_omega_one_concept",
)
LOCK_T = [
    f"ac:drivetrain.ini/AWD/{s}_DIFF_{k}"
    for s in ("FRONT", "REAR", "CENTRE")
    for k in ("POWER", "COAST", "PRELOAD")
]
TOL = 1e-6  # N·m (doble precisión; ver el informe §3)


@lru_cache(maxsize=None)
def _evidence() -> dict:
    return json.loads(EVID.read_text("utf-8"))


def _catalog_before_g223() -> Catalog:
    """Catálogo sin las 9 reglas LSD que G2.23 activó (estado que fija esta fase)."""
    cat = Catalog.load(REPO_CATALOG)
    # (G2.30: también las 9 derivadas para 40340 + camsoEngine; esta fase fija el estado anterior)
    # (y las familias AWD con «lsd» en el nombre posteriores: _lsd_viscc_, _openmode_lsdc_, …)
    for key in [
        k
        for k in cat.rules
        if "_lsd_200409@" in k
        or "_lsd_40340@" in k
        or (k.startswith("dt.awd_") and "lsd" in k.split("@")[0])
    ]:
        cat.rules.pop(key)
    return cat


@lru_cache(maxsize=None)
def _resolved(name: str) -> tuple:
    vd = SAMPLES / name / "vehicles" / name
    pv = preview_physics(
        load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0]), 70.0
    )
    ir = import_preview(pv, 70.0)
    session = ConversionSession(ir, _catalog_before_g223())
    return ir, {r.target: r for r in session.resolve()}


@unittest.skipUnless(EVID.is_file(), "evidencia del banco de G2.12.1 no disponible")
class TestBeamNGMeasured(unittest.TestCase):
    def test_two_sessions_complete_and_repeatable(self):
        sm = _evidence()["summary"]
        self.assertEqual(sm["sessions"], ["s1", "s2"])
        self.assertEqual(sm["missing"], [])
        self.assertGreater(sm["repeatability"]["pairs"], 0)
        self.assertEqual(sm["repeatability"]["max_abs_diff_out1_out2"], 0.0)

    def test_game_instantiates_lua_defaults(self):
        devs = _evidence()["summary"]["devices"]
        for car in LSD_CARS:
            for dev in ("centreDiff", "frontDiff", "rearDiff"):
                for s in ("s1", "s2"):
                    p = devs[f"{car}/{dev}"][s]
                    self.assertEqual(
                        (
                            p["mode"],
                            p["lsdPreload"],
                            p["lsdLockCoef"],
                            p["lsdRevLockCoef"],
                        ),
                        ("lsd", 50, 0.2, 0.2),
                        (car, dev, s),
                    )
                    self.assertTrue(p["params_unchanged"])  # la sombra no tocó el real
                    self.assertGreater(
                        p["lockTorque"], 1e4
                    )  # el segundo tope no limita

    def test_saturated_lock_is_lua_model_and_difference_is_twice(self):
        rows = [
            r
            for r in _evidence()["rows"]
            if r["mode"] == "lsd" and r.get("saturated_expected")
        ]
        self.assertGreater(len(rows), 600)
        for r in rows:
            self.assertLess(abs(r["err_lock_abs"]), TOL, r)
            self.assertAlmostEqual(
                2 * abs(r["lock_med"]) / r["lsd_torque_model"], 2.0, places=9
            )

    def test_power_and_coast_coefficients_are_separate(self):
        """Referencia controlada: (preload 0, lock 0,5, rev 0,1)."""
        for r in _evidence()["rows"]:
            if (
                r["override"]
                == {
                    "lsdPreload": 0.0,
                    "lsdLockCoef": 0.5,
                    "lsdRevLockCoef": 0.1,
                }
                and r["mode"] == "lsd"
            ):
                coef = 0.5 if r["T"] > 0 else 0.1
                self.assertAlmostEqual(
                    abs(r["lock_med"]), coef * abs(r["t_in_med"]), delta=TOL
                )

    def test_open_diffs_transfer_nothing(self):
        opn = [r for r in _evidence()["rows"] if r["mode"] == "open"]
        self.assertGreater(len(opn), 0)
        self.assertTrue(all(r["transfer_med"] == 0 for r in opn))

    def test_lab_beamng_port_agrees_with_measurement(self):
        """El port de BeamNG de src/lab/awd_lab.py da la misma transferencia que el
        juego (diferencia 2·lsdTorque al saturar)."""
        t = 1000.0
        bng = L.beamng_lsd_split_mu(t, 50.0, 0.2)
        self.assertAlmostEqual(bng.torque_to_grip_wheel - t / 2, 50 + 0.2 * t, delta=1)


@unittest.skipUnless(SAMPLES.is_dir(), "samples_beamng no disponible")
class TestFactorNotApplied(unittest.TestCase):
    """En G2.12.1 la mitad AC no estaba medida: el factor ×2 no pasaba a producción.

    G2.22 la midió y G2.23 activó las 9 reglas: estas aserciones se evalúan sobre el
    catálogo de antes de G2.23 (precedente G2.5d); el estado nuevo está en
    ``tests/test_g223_lsd_rules.py``."""

    def test_no_lsd_lock_rule_in_catalog(self):
        cat = _catalog_before_g223()
        for t in LOCK_T:
            for r in cat.rules_for(t):
                kinds = [c.value for c in r.conditions if c.fact.endswith(".kind")]
                self.assertNotIn("lsd", kinds, r.key)

    def test_four_lsd_cars_keep_manual_required(self):
        for car in LSD_CARS:
            ir, res = _resolved(car)
            for t in LOCK_T:
                self.assertEqual(res[t].status, "manual_required", (car, t))
                self.assertIsNone(res[t].value, (car, t))
            for role in ("center", "front", "rear"):
                d = ir.drivetrain.differentials[role]
                for field, value, line in (
                    ("preload", 50.0, "333"),
                    ("lock_coef", 0.2, "334"),
                    ("rev_lock_coef", 0.2, "335"),
                ):
                    p = getattr(d, field)
                    self.assertEqual((p.value, p.kind), (value, "computed"))
                    self.assertTrue(
                        any(
                            r.path.endswith("differential.lua") and line in r.locator
                            for r in p.refs
                        ),
                        (car, role, field),
                    )


if __name__ == "__main__":
    unittest.main()
