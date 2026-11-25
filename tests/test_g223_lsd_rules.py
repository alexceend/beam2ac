"""G2.23: las 9 reglas LSD del [AWD] (exportador 200409) ya están en el catálogo.

Comprueba con el motor de reglas REAL (no con la herramienta):

* integridad: exactamente 9 reglas ``dt.awd_*_lsd_200409@1``, iguales a las que
  construye ``tools/catalog/add_lsd_awd_200409_rules.py`` con el veredicto de G2.22,
  que pasa la puerta;
* valores: ×2 en PRELOAD/POWER/COAST de eje y en POWER/COAST del central; PRELOAD del
  central = 2·lsdPreload·FINAL (``scale_product``) en los 4 coches LSD;
* ámbito (casos negativos sobre hechos reales modificados): 38022, 40340,
  ``camsoEngine``, 2WD, diferencial abierto o bloqueado, coeficiente > 1, hechos
  ausentes o no comparables: ninguna regla LSD se aplica y el destino nunca recibe 0;
* límites admitidos: coeficiente = 1 y valores 0.

Informe: ``docs/research/g223_activacion_reglas_lsd_awd_2026-11-09.md``.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs import schema as S
from src.attrs.engine import Fact, ResolutionEngine
from src.attrs.report import context_from_facts
from src.attrs.store import REPO_CATALOG, Catalog
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
TOOL = ROOT / "tools" / "catalog" / "add_lsd_awd_200409_rules.py"
VERDICT = ROOT / "output" / "g222_2026-11-07" / "repetibilidad" / "veredicto.json"
VERDICT_SHA = "817abbc2285a9d41e143a79468c53a10a3bc8cf4a7f633387948e5d6223bdf98"

ROLES = {"front": "FRONT", "rear": "REAR", "center": "CENTRE"}
KEYS = {"power": "POWER", "coast": "COAST", "preload": "PRELOAD"}
LSD_RULES = {
    f"ac:drivetrain.ini/AWD/{sec}_DIFF_{ak}": f"dt.awd_{role}_diff_{k}_lsd_200409@1"
    for role, sec in ROLES.items()
    for k, ak in KEYS.items()
}
LOCK_T = sorted(LSD_RULES)
LSD_CARS = {  # vehículo → FINAL (G2.21, cuatro_lsd.md)
    "2020_rpm_atlanta_gt_3_6t": 3.38,
    "2020_rpm_legion_grand_touring": 3.2,
    "2020_rpm_prothean_s": 3.33,
    "rpm_omega_one_concept": 3.55,
}
HAS_SAMPLES = SAMPLES.is_dir()


def _load_tool():
    spec = importlib.util.spec_from_file_location("add_lsd_awd_200409_rules", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@lru_cache(maxsize=None)
def _catalog() -> Catalog:
    return Catalog.load(REPO_CATALOG)


@lru_cache(maxsize=None)
def _source_facts(name: str):
    vd = next(SAMPLES.glob(f"*/vehicles/{name}"))
    src = load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])
    facts = ir_to_facts(import_preview(preview_physics(src, 0.0), 0.0))
    return src, facts


def _resolve(name: str, changes: dict | None = None, drop: tuple = ()) -> dict:
    """Resuelve los 9 destinos con los hechos reales de ``name`` modificados."""
    src, base = _source_facts(name)
    facts = dict(base)
    for k in drop:
        facts.pop(k)
    for k, v in (changes or {}).items():
        old = base.get(k)  # misma unidad y tipo de evidencia que el hecho real
        facts[k] = Fact(k, v, old.unit if old else "", old.kind if old else "extracted")
    cat = _catalog()
    ctx = context_from_facts(src.vehicle_id, src.variant, facts, cat)
    eng = ResolutionEngine(cat, ctx)
    return {t: eng.resolve(t) for t in LOCK_T}


def _expected(final: float) -> dict:
    out = {}
    for t in LOCK_T:
        if t.endswith("CENTRE_DIFF_PRELOAD"):
            out[t] = 2.0 * 50.0 * final
        elif t.endswith("_PRELOAD"):
            out[t] = 100.0
        else:
            out[t] = 0.4
    return out


class TestCatalogIntegrity(unittest.TestCase):
    def test_exactly_nine_lsd_rules_with_expected_targets(self):
        cat = _catalog()
        # solo las del [AWD]; las 2WD del 40340 (dt.diff_*_2wd_lsd_*, G2.27) van aparte
        lsd = {
            k: r
            for k, r in cat.rules.items()
            if "_lsd_" in k
            and k.startswith("dt.awd_")
            and "_lsd_40340@" not in k
            and "_viscc_40340@" not in k
        }  # (G2.30: las derivadas para 40340 + camsoEngine tienen su prueba en test_g230_rear_fitted)
        self.assertEqual(sorted(lsd), sorted(LSD_RULES.values()))
        self.assertEqual({r.target: r.key for r in lsd.values()}, LSD_RULES)

    def test_rules_equal_tool_output_with_real_verdict(self):
        tool = _load_tool()
        v, sha = tool.check_verdict(VERDICT)  # la puerta acepta el veredicto
        self.assertEqual(sha, VERDICT_SHA)
        ref = tool.ev(
            "file",
            VERDICT.relative_to(ROOT).as_posix(),
            "params",
            sha,
            "banco de AC",
        )
        for r in tool.build(ref):
            on_disk = json.loads(
                (REPO_CATALOG / "rules" / f"{r.key}.json").read_text("utf-8")
            )
            self.assertEqual(S.to_dict(r), on_disk, r.key)

    def test_status_and_evidence(self):
        for key in LSD_RULES.values():
            r = _catalog().rules[key]
            self.assertEqual(r.status, "provisional", key)  # sin prueba de cada coche
            self.assertTrue(any(e.sha256 == VERDICT_SHA for e in r.evidence), key)
            conds = {(c.fact, c.op, json.dumps(c.value)) for c in r.conditions}
            for c in (
                ("ir.formats.exporter_version", "in", '["200409"]'),
                ("ir.engine.device_type", "eq", '"combustionEngine"'),
                ("ir.drivetrain.layout", "eq", '"AWD"'),
                ("ir.drivetrain.differentials.center.kind", "in", '["open", "lsd"]'),
            ):
                self.assertIn(c, conds, key)

    def test_transforms(self):
        cat = _catalog()
        for t, key in LSD_RULES.items():
            tr = cat.rules[key].transform
            if t.endswith("CENTRE_DIFF_PRELOAD"):
                self.assertEqual(tr.id, "scale_product")
                self.assertEqual(tr.params, {"factor": 2.0, "base": "x"})
                self.assertEqual(tr.input_units, {"x": "N·m", "final": "1"})
                self.assertEqual(
                    cat.rules[key].inputs["final"], "ir.drivetrain.final_ratio"
                )
            else:
                self.assertEqual((tr.id, tr.params), ("scale", {"factor": 2.0}))
            self.assertEqual(tr.output_unit, "N·m" if "PRELOAD" in t else "1")

    def test_no_2wd_or_other_rule_touched(self):
        names = {p.name for p in (REPO_CATALOG / "rules").glob("dt.preload@*.json")}
        self.assertEqual(
            names,
            {
                "dt.preload@1.json",
                "dt.preload@2.json",
                "dt.preload@3.json",
                "dt.preload@4.json",
            },  # @4: G2.39
        )
        for key in LSD_RULES.values():
            self.assertTrue(
                _catalog().rules[key].target.startswith("ac:drivetrain.ini/AWD/")
            )


@unittest.skipUnless(HAS_SAMPLES, "samples_beamng no disponible")
class TestPositive(unittest.TestCase):
    def test_four_lsd_cars_get_nine_values(self):
        for name, final in LSD_CARS.items():
            res = _resolve(name)
            for t, want in _expected(final).items():
                with self.subTest(car=name, target=t):
                    r = res[t]
                    self.assertEqual((r.source, r.rule), ("rule", LSD_RULES[t]))
                    self.assertAlmostEqual(r.value, want, places=9)
                    self.assertEqual(r.status, "inferred")

    def test_centre_preload_follows_final(self):
        res = _resolve("2020_rpm_atlanta_gt_3_6t", {"ir.drivetrain.final_ratio": 4.0})
        self.assertAlmostEqual(
            res["ac:drivetrain.ini/AWD/CENTRE_DIFF_PRELOAD"].value, 400.0
        )
        self.assertAlmostEqual(
            res["ac:drivetrain.ini/AWD/FRONT_DIFF_PRELOAD"].value, 100.0
        )

    def test_limits_admitted(self):
        p = "ir.drivetrain.differentials."
        res = _resolve(
            "2020_rpm_atlanta_gt_3_6t",
            {
                p + "front.lock_coef": 1.0,
                p + "rear.rev_lock_coef": 0.0,
                p + "center.preload": 0.0,
                p + "front.preload": 0.0,
            },
        )
        A = "ac:drivetrain.ini/AWD/"
        self.assertAlmostEqual(res[A + "FRONT_DIFF_POWER"].value, 2.0)
        self.assertEqual(res[A + "REAR_DIFF_COAST"].value, 0.0)
        self.assertEqual(res[A + "CENTRE_DIFF_PRELOAD"].value, 0.0)
        self.assertEqual(res[A + "FRONT_DIFF_PRELOAD"].value, 0.0)
        for t in (
            A + "FRONT_DIFF_POWER",
            A + "REAR_DIFF_COAST",
            A + "CENTRE_DIFF_PRELOAD",
        ):
            self.assertEqual(res[t].rule, LSD_RULES[t])


@unittest.skipUnless(HAS_SAMPLES, "samples_beamng no disponible")
class TestNegative(unittest.TestCase):
    CAR = "2020_rpm_atlanta_gt_3_6t"

    def assertNoLsd(self, res: dict, targets=LOCK_T, why: str = ""):
        for t in targets:
            r = res[t]
            self.assertNotIn("_lsd_", r.rule or "", (why, t))
            self.assertIsNone(r.value, (why, t))  # nunca 0 por defecto

    def test_other_exporters(self):
        for ver in ("38022", "40340"):
            self.assertNoLsd(
                _resolve(self.CAR, {"ir.formats.exporter_version": ver}), why=ver
            )

    def test_camso_engine(self):
        self.assertNoLsd(
            _resolve(self.CAR, {"ir.engine.device_type": "camsoEngine"}), why="camso"
        )

    def test_2wd(self):
        for lay in ("FWD", "RWD"):
            self.assertNoLsd(_resolve(self.CAR, {"ir.drivetrain.layout": lay}), why=lay)

    def test_locked_centre(self):
        self.assertNoLsd(
            _resolve(self.CAR, {"ir.drivetrain.differentials.center.kind": "locked"}),
            why="locked",
        )

    def test_open_axle_diff_gets_open_rule_not_lsd(self):
        res = _resolve(self.CAR, {"ir.drivetrain.differentials.front.kind": "open"})
        for k in ("POWER", "COAST", "PRELOAD"):
            r = res[f"ac:drivetrain.ini/AWD/FRONT_DIFF_{k}"]
            self.assertEqual(r.rule, f"dt.awd_front_diff_{k.lower()}_open@1")
            self.assertEqual(r.value, 0)
        for t in LOCK_T:  # los otros dos siguen con su LSD
            if "FRONT" not in t:
                self.assertEqual(res[t].rule, LSD_RULES[t])

    def test_open_centre_keeps_axle_lsd_but_not_centre_lsd(self):
        res = _resolve(self.CAR, {"ir.drivetrain.differentials.center.kind": "open"})
        for t in LOCK_T:
            if "CENTRE" in t:
                self.assertNotIn("_lsd_", res[t].rule or "", t)
            else:
                self.assertEqual(res[t].rule, LSD_RULES[t])

    def test_coef_above_one_not_applied(self):
        p = "ir.drivetrain.differentials."
        res = _resolve(
            self.CAR, {p + "rear.lock_coef": 1.0001, p + "center.rev_lock_coef": 3.0}
        )
        self.assertNoLsd(
            res,
            [
                "ac:drivetrain.ini/AWD/REAR_DIFF_POWER",
                "ac:drivetrain.ini/AWD/CENTRE_DIFF_COAST",
            ],
            "coef > 1",
        )
        self.assertEqual(
            res["ac:drivetrain.ini/AWD/REAR_DIFF_COAST"].rule,
            LSD_RULES["ac:drivetrain.ini/AWD/REAR_DIFF_COAST"],
        )

    def test_missing_facts_never_zero(self):
        p = "ir.drivetrain.differentials."
        res = _resolve(
            self.CAR,
            drop=(
                p + "front.preload",
                p + "rear.lock_coef",
                "ir.drivetrain.final_ratio",
            ),
        )
        for t in (
            "ac:drivetrain.ini/AWD/FRONT_DIFF_PRELOAD",
            "ac:drivetrain.ini/AWD/REAR_DIFF_POWER",
            "ac:drivetrain.ini/AWD/CENTRE_DIFF_PRELOAD",
        ):
            self.assertIsNone(res[t].value, t)
            self.assertEqual(res[t].status, "manual_required", t)

    def test_missing_scope_fact(self):
        self.assertNoLsd(
            _resolve(self.CAR, drop=("ir.formats.exporter_version",)), why="sin versión"
        )

    def test_non_comparable_value(self):
        res = _resolve(self.CAR, {"ir.drivetrain.differentials.front.lock_coef": "x"})
        self.assertNoLsd(res, ["ac:drivetrain.ini/AWD/FRONT_DIFF_POWER"], "texto")


@unittest.skipUnless(HAS_SAMPLES, "samples_beamng no disponible")
class TestOtherVehicles(unittest.TestCase):
    """Ningún otro vehículo del corpus recibe una regla LSD."""

    def test_non_lsd_200409_and_2wd(self):
        for name in (
            "2021_rpm_portland_touring",  # AWD abierto
            "2020_rpm_patagonia_xt_v8",  # central bloqueado
            "2020_rpm_atacama_sport__4x4",
            "quail_mirabilis",  # 40340 RWD
            "hyacinth_sage",  # 40340 FWD
        ):  # (autist, 40340 AWD camsoEngine, recibe desde G2.30 las dt.awd_*_lsd_40340)
            res = _resolve(name)
            for t in LOCK_T:
                with self.subTest(car=name, target=t):
                    self.assertNotIn("_lsd_", res[t].rule or "")

    def test_autist_gets_only_its_own_40340_rules(self):
        res = _resolve("autist_cuanto")
        for t in LOCK_T:
            self.assertTrue((res[t].rule or "").endswith("_lsd_40340@1"), t)


if __name__ == "__main__":
    unittest.main()
