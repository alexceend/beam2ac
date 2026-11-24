"""Regresión del modelo de atributos con el Ibiza (muestra) y spicy_mandala (mod del
usuario).

Valores de referencia documentados en docs/research/modelo_atributos.md §6 (2026-10-26).
Las pruebas de spicy_mandala se omiten si el mod o common.zip no están instalados.
"""

import hashlib
import json
import unittest
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs.compare import same_as_written
from src.attrs.report import build_report, resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    parse_ini_text,
    preview_physics,
    rendered_keys,
)

ROOT = Path(__file__).resolve().parents[1]
IBIZA = ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
BASE = (
    ROOT
    / "output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25/seat_ibiza_acfr/data"
)
SPICY = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods/spicy_mandala.zip"
COMMON = Path(
    (default_beamng_root() / "content/vehicles/common.zip").as_posix()
)
_CACHE: dict = {}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ibiza():
    if "ibiza" not in _CACHE:
        pv = preview_physics(load_vehicle_dir(IBIZA, "35b84.pc"), 70.0)
        cat = Catalog.load(REPO_CATALOG)
        ctx, res = resolve_vehicle(pv, cat)
        _CACHE["ibiza"] = (pv, cat, ctx, {r.target: r for r in res}, res)
    return _CACHE["ibiza"]


def spicy():
    if "spicy" not in _CACHE:
        before = (sha(SPICY), sha(COMMON))
        pv = preview_physics(load_vehicle_zip(SPICY, COMMON), 70.0)
        cat = Catalog.load(REPO_CATALOG)
        ctx, res = resolve_vehicle(pv, cat)
        _CACHE["spicy"] = (pv, cat, ctx, {r.target: r for r in res}, res, before)
    return _CACHE["spicy"]


# Diferencias deliberadas con el generador antiguo (docs/research/modelo_atributos.md
# §5):
# TOTALMASS sigue la base aprobada (AC suma HUB_MASS), el generador escribía masa +
# piloto.
# G2.39 (D-G227-IBIZA): el LSD 2WD del 38022 se exporta x2 (AC: diferencia = lim, no lim/2);
# el generador antiguo y la base v18bu escriben el valor de BeamNG (Ibiza 100, spicy 70).
EXPECTED_DIFFERENCES = {
    "ac:car.ini/BASIC/TOTALMASS",
    "ac:drivetrain.ini/DIFFERENTIAL/PRELOAD",
    # G2.44 (D-G231-COAST): camso 47437d5c usa la pendiente por mínimos cuadrados
    "ac:engine.ini/COAST_REF/RPM",
    "ac:engine.ini/COAST_REF/TORQUE",
}


def assert_rule_values_match_generators(test, pv, results):
    """Cada valor que sale de una regla o propuesta es el que escribe el generador
    antiguo,
    salvo las diferencias deliberadas y documentadas de EXPECTED_DIFFERENCES."""
    keys = rendered_keys(pv)
    checked = 0
    for r in results.values():
        if (
            r.source not in ("rule", "proposal")
            or r.value is None
            or r.target == "ac:power.lut"
        ):
            continue
        if r.target in EXPECTED_DIFFERENCES:
            if r.target.endswith("DIFFERENTIAL/PRELOAD"):  # G2.39: exactamente x2
                file, _, key = r.target[3:].partition("/")
                w = keys.get(file, {}).get(key)
                if w is not None:
                    test.assertAlmostEqual(r.value, 2 * float(w))
            continue
        file, _, key = r.target[3:].partition("/")
        written = keys.get(file, {}).get(key)
        if written is None:
            continue
        with test.subTest(r.target):
            test.assertTrue(
                same_as_written(written, r.value), f"{r.target}: {written} vs {r.value}"
            )
        checked += 1
    lut = [
        line.split("|") for line in pv.rendered["power.lut"].splitlines() if "|" in line
    ]
    test.assertEqual(
        results["ac:power.lut"].value, [[float(a), float(b)] for a, b in lut]
    )
    return checked


@unittest.skipUnless(
    IBIZA.is_dir() and BASE.is_dir(),
    "muestra del Ibiza o base congelada no disponibles",
)
class TestIbizaResolution(unittest.TestCase):
    def test_preview_is_valid(self):
        pv = ibiza()[0]
        self.assertTrue(pv.ok, pv.failed_stages)

    def test_reference_counts(self):
        res = ibiza()[4]
        counts = {}
        for r in res:
            counts[r.status] = counts.get(r.status, 0) + 1
        self.assertEqual(
            counts,
            {
                "ambiguous": 1,
                "format_requirement": 6,
                "inferred": 172,  # G2.39: +3 (INERTIA, POWER, COAST; reglas 38022)
                "known": 23,
                # B4: 5 claves no leídas por su TYPE. Fase B4 de la aplicación:
                # +4 tablas LUT (…/table) sin decisión en el catálogo (V1: proyecto);
                # fase C5: +33 atributos de alas de aero.ini (WING_0..2 y HEADER) sin
                # decisión
                "manual_required": 43,
                # 2026-10-30: +10 claves [AWD] (solo aplican con TYPE=AWD); G2.19: +1
                # GEAR_9 (app.gears.gear_9@1: 6 marchas); G2.20: +2 CX_MULT (VERSION 7:
                # app.tyres.<eje>.cx_mult@1, solo se lee con VERSION ≥ 10);
                # 2026-11-10: +1 [GEARBOX] INERTIA (app.gearbox.inertia@1: solo 200409);
                # G2.27: +2 [DIFFERENTIAL] POWER/COAST (app.diff2wd.*: solo 40340)
                "not_applicable": 45,  # G2.50: +4 [ABS] (app.abs.*: el Ibiza queda fuera); G2.39: -3 (INERTIA/POWER/COAST); G2.44: +21 atributos de AXLE/HEADER/J0-J7 «no aplica»
            },
        )

    def test_rule_values_equal_previous_generators(self):
        pv, _, _, results, _ = ibiza()
        self.assertGreaterEqual(
            assert_rule_values_match_generators(self, pv, results), 35
        )

    def test_approved_physics_base_is_reproduced(self):
        results = ibiza()[3]
        n = 0
        for ini in sorted(BASE.glob("*.ini")):
            for (sec, key), (written, _c) in parse_ini_text(
                ini.read_text(encoding="utf-8")
            ).items():
                r = results[f"ac:{ini.name}/{sec}/{key}"]
                with self.subTest(r.target):
                    if r.target == "ac:drivetrain.ini/DIFFERENTIAL/PRELOAD":
                        # G2.39: x2 deliberado (D-G227-IBIZA)
                        self.assertAlmostEqual(r.value, 2 * float(written))
                        n += 1
                        continue
                    self.assertTrue(
                        same_as_written(written, r.value),
                        f"{r.target}: {written} vs {r.value}",
                    )
                n += 1
        self.assertEqual(n, 198)

    def test_totalmass_rule_uses_hub_masses(self):
        pv, _, _, results, _ = ibiza()
        r = results["ac:car.ini/BASIC/TOTALMASS"]
        self.assertEqual((r.source, r.rule), ("rule", "car.totalmass@2"))  # G2.4
        self.assertAlmostEqual(
            r.value, 1226.27029 + 70 - 2 * 49.15 - 2 * 39.2, places=4
        )
        # diferencia deliberada con el generador antiguo: exactamente las masas de los
        # bujes
        old = float(rendered_keys(pv)["car.ini"]["BASIC/TOTALMASS"])
        self.assertAlmostEqual(old - r.value, 2 * 49.15 + 2 * 39.2, delta=0.05)

    def test_known_values(self):
        results = ibiza()[3]
        for target in (
            "ac:power.lut",
            "ac:drivetrain.ini/TRACTION/TYPE",
            "ac:drivetrain.ini/GEARS/FINAL",
        ):
            with self.subTest(target):
                self.assertEqual(results[target].status, "known")
        self.assertEqual(results["ac:drivetrain.ini/TRACTION/TYPE"].confidence, "E4")
        self.assertEqual(results["ac:brakes.ini/DATA/FRONT_SHARE"].status, "ambiguous")

    def test_ibiza_validations(self):
        ctx = ibiza()[2]
        res = {v.id: v.result for v in ctx.validations}
        self.assertEqual(res["seat_ibiza_acfr.35b84.engine_curve"], "pass")
        self.assertEqual(res["seat_ibiza_acfr.35b84.mass"], "pass")


@unittest.skipUnless(
    SPICY.is_file() and COMMON.is_file(), "spicy_mandala o common.zip no instalados"
)
class TestSpicyResolution(unittest.TestCase):
    def test_reference_counts(self):
        res = spicy()[4]
        counts = {}
        for r in res:
            counts[r.status] = counts.get(r.status, 0) + 1
        self.assertEqual(
            counts,
            {
                # G2.44: spicy (38022) recibe las 207 reglas clonadas del 40340 (suspensión,
                # neumático, aero, frenos, masa): FRONT_SHARE ya no es ambiguo
                "format_requirement": 17,
                "inferred": 150,  # G2.50: +2 [ABS] PRESENT/ACTIVE (sin ABS); G2.45: +7 con piloto de 70 kg; +38 por la huella sin common.zip
                # (spicy se carga con common.zip: ya recibe las medidas del banco)
                "known": 9,
                # +4 tablas LUT (fase B4); +33 atributos de alas de aero.ini (fase C5);
                # G2.20: +2 CX_MULT (sin VERSION de tyres.ini resuelta: aplicabilidad
                # indeterminada)
                "manual_required": 62,
                # 2026-10-30: +10 claves [AWD] (solo aplican con TYPE=AWD); G2.19: +1
                # GEAR_9 (app.gears.gear_9@1: 7 marchas);
                # 2026-11-10: +1 [GEARBOX] INERTIA (app.gearbox.inertia@1);
                # G2.27: +2 [DIFFERENTIAL] POWER/COAST (app.diff2wd.*: solo 40340)
                "not_applicable": 52,  # G2.50: +2 [ABS] límite y frecuencia (sin ABS); G2.44: aplicabilidad AXLE/DWB de las reglas clonadas
            },
        )

    def test_known_are_only_family_rules_validated_here(self):
        cat, results = spicy()[1], spicy()[3]
        known = sorted(t for t, r in results.items() if r.status == "known")
        family = [
            t for t in known if cat.rules[results[t].rule].scope.level == "family"
        ]
        self.assertEqual(family, ["ac:drivetrain.ini/TRACTION/TYPE", "ac:power.lut"])
        # 2026-10-30: el resto de «known» son relaciones de marchas de las reglas de
        # arquitectura (2WD, caja manual/secuencial); ninguna regla del Ibiza
        for t in set(known) - set(family):
            self.assertEqual(cat.rules[results[t].rule].scope.level, "architecture")
            self.assertTrue(t.startswith("ac:drivetrain.ini/GEARS/"))
        self.assertEqual(results["ac:drivetrain.ini/TRACTION/TYPE"].value, "RWD")

    def test_ibiza_rules_are_only_proposals(self):
        cat, results = spicy()[1], spicy()[3]
        for r in results.values():
            if (
                r.rule
                and cat.rules[r.rule].scope.level == "vehicle"
                and r.status != "not_applicable"
            ):
                with self.subTest(r.target):
                    self.assertEqual(r.source, "proposal")
                    self.assertTrue(r.requires_approval)
                    self.assertFalse(r.promotion["applied"])
                    self.assertNotEqual(r.status, "known")

    def test_proposal_values_equal_previous_generators(self):
        pv, _, _, results, _, _ = spicy()
        self.assertGreaterEqual(
            assert_rule_values_match_generators(self, pv, results), 25
        )

    def test_documented_cases(self):
        results = spicy()[3]
        for k in (5, 6, 7, 8):
            self.assertEqual(
                results[f"ac:drivetrain.ini/GEARS/GEAR_{k}"].status, "not_applicable"
            )
        tm = results["ac:car.ini/BASIC/TOTALMASS"]
        self.assertEqual(tm.status, "inferred")  # G2.44: regla 38022 de masa
        ft = results["ac:suspensions.ini/FRONT/TYPE"]
        self.assertEqual(ft.status, "inferred")  # G2.44: regla 38022 clonada
        # G2.7: la tabla está en la regla (text_contains_map sobre la etiqueta del IR)
        # G2.16: las reglas de TYPE por estructura del 200409 también aparecen
        # rechazadas (por la versión del exportador); se busca el motivo, no la posición
        self.assertTrue(
            any(
                "text_contains_map: sin correspondencia" in r["reason"]
                for r in ft.rejected
            )
        )
        self.assertEqual(
            results["ac:brakes.ini/DATA/FRONT_SHARE"].status, "inferred"
        )  # G2.44: regla 38022 clonada (antes ambiguo)
        preload = results["ac:drivetrain.ini/DIFFERENTIAL/PRELOAD"]
        # G2.39: regla de familia 38022 (factor 2 medido: B0/ib2), antes propuesta ×1
        self.assertEqual(preload.value, 140)
        # dt.preload@2 (2026-10-26): la unidad ya no se «interpreta» (N·m confirmado en
        # differential.lua:57), pero el factor de conversión sigue SIN RESOLVER y el
        # resultado debe seguir diciéndolo; el estado no sube de «inferred»
        self.assertEqual(preload.status, "inferred")
        self.assertFalse(
            any("FACTOR DE CONVERSIÓN SIN RESOLVER" in a for a in preload.assumptions)
        )

    def test_validations_and_facts(self):
        ctx = spicy()[2]
        res = {v.id: v.result for v in ctx.validations}
        self.assertEqual(
            res,
            {
                "spicy_mandala.56a86.engine_curve": "pass",
                "spicy_mandala.56a86.engine_curve_in_game": "pass",  # G2.44
                "spicy_mandala.56a86.mass": "fail",
                "spicy_mandala.56a86.mass_in_game": "pass",  # G2.44
                "spicy_mandala.56a86.traction": "pass",
            },
        )
        self.assertIn("automation_export", ctx.families)
        self.assertLess(ctx.facts["bng.aero.target_front"].value, 0)  # sustentación
        # unidad corregida el 2026-10-26: differential.lua:57 lo trata como par
        # (el rótulo «N/m» de BeamNG es un error); el valor no cambia
        self.assertEqual(ctx.facts["bng.diff.lsd_preload"].unit, "N·m")
        self.assertEqual(ctx.facts["bng.diff.lsd_preload"].value, 70.0)

    def test_sources_unchanged_and_report_deterministic(self):
        pv, cat, ctx, _, res, before = spicy()
        a = json.dumps(build_report(pv, ctx, res, cat), sort_keys=True, default=str)
        ctx2, res2 = resolve_vehicle(pv, Catalog.load(REPO_CATALOG))
        b = json.dumps(build_report(pv, ctx2, res2, cat), sort_keys=True, default=str)
        self.assertEqual(a, b)
        self.assertEqual(before, (sha(SPICY), sha(COMMON)))


if __name__ == "__main__":
    unittest.main()
