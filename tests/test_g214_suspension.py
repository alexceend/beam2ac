"""G2.14: suspensión por propiedades, reparto no suspendido, IR y reglas del 200409.

Casos sintéticos (vigas sueltas) para la clasificación y el cruce con los nombres
antiguos, y vehículos reales del corpus (solo lectura; se omiten si faltan). Informe:
``docs/research/g214_suspension_2026-11-03.md``.
"""

from __future__ import annotations

import pytest

import json
import os
import unittest
from functools import lru_cache
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.extract.components import Beam
from src.extract.mass_geometry import compute_mass_geometry
from src.extract.suspension import (
    SuspensionDataError,
    _pick,
    build_suspension,
    classify_beam,
)
from src.importers.beamng import import_preview
from src.jbeam import resolve
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
BNG = Path((default_beamng_root() / "content/vehicles").as_posix())
MODS = Path(os.environ.get("LOCALAPPDATA", "")) / "BeamNG/BeamNG.drive/current/mods"
ATACAMA = "2020_rpm_atacama_sport__4x4"
INDEPENDENT = [
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
]
SEVEN = INDEPENDENT + [ATACAMA]
NAMED_DIRS = [
    "autist_cuanto",
    "hyacinth_sage",
    "quail_mirabilis",
    "sucette_poubelle",
    "sucette_poubelle_turbo",
]
NAMED_ZIPS = [
    "porsche_gt3_rs",
    "spicy_mandala",
    "rune_susui_espol_vtec",
    "composed_fox",
]
HUB_F, HUB_R = "ac:suspensions.ini/FRONT/HUB_MASS", "ac:suspensions.ini/REAR/HUB_MASS"
TOTAL = "ac:car.ini/BASIC/TOTALMASS"
CGL = "ac:suspensions.ini/BASIC/CG_LOCATION"
BASEY = ("ac:suspensions.ini/FRONT/BASEY", "ac:suspensions.ini/REAR/BASEY")
NEW_RULES = {
    HUB_F: "susp.hub_mass_front_200409@1",
    HUB_R: "susp.hub_mass_rear_200409@2",
    TOTAL: "car.totalmass_200409@2",
}


def _dir_of(name: str) -> Path | None:
    for top in SAMPLES.glob("*"):
        vd = top / "vehicles" / name
        if vd.is_dir():
            return vd
    return None


def _have(name: str) -> bool:
    if name == "ibiza":
        return IBIZA.is_dir()
    if name in NAMED_ZIPS:
        return (MODS / f"{name}.zip").is_file() and (BNG / "common.zip").is_file()
    return _dir_of(name) is not None


@lru_cache(maxsize=None)
def _source(name: str):
    if name == "ibiza":
        return load_vehicle_dir(IBIZA, "35b84.pc")
    if name in NAMED_ZIPS:
        return load_vehicle_zip(MODS / f"{name}.zip", BNG / "common.zip")
    vd = _dir_of(name)
    return load_vehicle_dir(vd, sorted(p.name for p in vd.glob("*.pc"))[0])


@lru_cache(maxsize=None)
def _suspension(name: str):
    src = _source(name)
    r = resolve(src.index, src.pc)
    return build_suspension(r, compute_mass_geometry(r))


@lru_cache(maxsize=None)
def _pv(name: str):
    return preview_physics(_source(name), 70.0)


@lru_cache(maxsize=None)
def _ir(name: str):
    return import_preview(_pv(name), 70.0)


@lru_cache(maxsize=None)
def _res(name: str) -> dict:
    return {r.target: r for r in ConversionSession.from_ir(_ir(name)).resolve()}


def _beam(nodes=("a", "b"), name=None, **props) -> Beam:
    return Beam("p", tuple(nodes), name, props)


class TestClassifyBeam(unittest.TestCase):
    """Semántica de stage2.lua: clasificación por propiedades, nunca por nombre."""

    def test_roles(self):
        cases = [
            (
                dict(beamType="|NORMAL", beamSpring=43200, precompressionRange=0.1),
                {"spring"},
            ),
            (  # muelle progresivo (Porsche): BOUNDED con beamLimitSpring
                dict(
                    beamType="|BOUNDED",
                    beamSpring=12000,
                    beamDamp=0,
                    precompressionRange=0.18,
                    beamLimitSpring=18000,
                ),
                {"spring"},
            ),
            (dict(beamType="|BOUNDED", beamSpring=0, beamDamp=4320), {"damper"}),
            (
                dict(
                    beamType="| BOUNDED",
                    beamSpring=0,
                    beamDamp=0,
                    longBoundRange=0.08,
                    shortBoundRange=0.08,
                ),
                {"bumpstop"},
            ),
            (dict(beamType="|NORMAL", beamSpring=1e7, beamDamp=500), set()),  # brazo
            (dict(beamType="|SUPPORT", beamSpring=2e6, beamLongBound=1), set()),
            (
                dict(beamType="|NORMAL", beamSpring=43200),
                set(),
            ),  # sin precompressionRange
            (
                dict(beamType="|BOUNDED", beamDamp=4320),
                set(),
            ),  # beamSpring no declarado
        ]
        for props, want in cases:
            with self.subTest(props=props):
                self.assertEqual(classify_beam(_beam(**props), {}), want)

    def test_name_is_ignored(self):
        b = _beam(name="spring_FR", beamType="|NORMAL", beamSpring=1e7)
        self.assertEqual(classify_beam(b, {}), set())

    def test_named_beams_must_match_property_identification(self):
        a = _beam(("x", "y"), beamType="|NORMAL", beamSpring=1, precompressionRange=0)
        b = _beam(("x", "z"), name="spring_FR")
        self.assertEqual(_pick("spring", "FR", [a], []), [a])
        self.assertEqual(_pick("spring", "FR", [a], [a]), [a])
        with self.assertRaisesRegex(SuspensionDataError, "no son las identificadas"):
            _pick("spring", "FR", [a], [b])


class TestNamedCorpusUnchanged(unittest.TestCase):
    """Los 10 vehículos con nombres spring_/damper_: la identificación por propiedades
    coincide con los nombres (``_pick`` lo exige) y los valores leídos no cambian."""

    @pytest.mark.ampliada
    def test_build_succeeds_and_matches_names(self):
        for name in ["ibiza"] + NAMED_DIRS + NAMED_ZIPS:
            if not _have(name):
                continue
            with self.subTest(vehicle=name):
                s = _suspension(name)
                self.assertEqual(len(s.axles), 2)
                for ax in s.axles:
                    self.assertEqual(len(ax.corners), 2)
                    for c in ax.corners:
                        self.assertGreater(c.spring_rate, 0)
                        self.assertIsNotNone(c.damper)

    @unittest.skipUnless(_have("ibiza"), "Ibiza no disponible")
    def test_ibiza_values(self):
        front, rear = _suspension("ibiza").axles
        self.assertEqual(front.corners[0].spring_rate, 60000.0)
        self.assertEqual(front.ac_type, "STRUT")


@unittest.skipUnless(all(_have(n) for n in SEVEN), "muestras 200409 no disponibles")
class TestAutomation200409(unittest.TestCase):
    def test_chassis_step_passes(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                self.assertTrue(_pv(name).stages["chassis"]["ok"])

    def test_values_read_from_coilovers(self):
        # valores tal cual en los JBeam (suspension_F/R.jbeam, piezas Coilovers)
        expect = {
            "2020_rpm_atlanta_gt_3_6t": (43200.0, 0.106062, 43200.0, 0.084502),
            ATACAMA: (31200.0, 0.175536, 31200.0, 0.13472),
        }
        for name, (kf, pf, kr, pr) in expect.items():
            front, rear = _suspension(name).axles
            for ax, k, pre in ((front, kf, pf), (rear, kr, pr)):
                for c in ax.corners:
                    self.assertEqual(c.spring_rate, k)
                    self.assertAlmostEqual(c.spring_preload_range, pre, places=9)
                    self.assertIsNotNone(c.damper)
                    self.assertIsNotNone(c.bumpstop_travel)
        d = _suspension("2020_rpm_atlanta_gt_3_6t").axles[0].corners[0].damper
        self.assertEqual(
            (d.bump, d.rebound, d.bump_fast, d.rebound_fast), (4320, 4320, 1440, 2160)
        )
        self.assertEqual(d.velocity_split, 0.15)

    def test_ir_suspension_present(self):
        for name in SEVEN:
            ir = _ir(name)
            for pos in ("front", "rear"):
                with self.subTest(vehicle=name, axle=pos):
                    p = ir.get(f"suspension.axles.{pos}.spring_rate_beam")
                    self.assertEqual((p.status, p.unit), ("present", "N/m"))
                    self.assertEqual(
                        ir.get(f"suspension.axles.{pos}.motion_ratio_hyp").kind,
                        "estimated",
                    )


class TestUnsprungPartition(unittest.TestCase):
    def test_mass_is_conserved(self):
        for name in ["ibiza", "porsche_gt3_rs", "composed_fox"] + SEVEN:
            if not _have(name):
                continue
            with self.subTest(vehicle=name):
                src = _source(name)
                r = resolve(src.index, src.pc)
                g = compute_mass_geometry(r)
                p = build_suspension(r, g).partition
                total = (
                    p.sprung_node_mass
                    + sum(p.mechanism.values())
                    + sum(b.mass for b in p.bodies)
                )
                self.assertAlmostEqual(total, g.total_mass, places=6)
                self.assertEqual(
                    sorted(w for b in p.bodies for w in b.wheels),
                    sorted(w.name for w in g.wheels),
                )

    @unittest.skipUnless(_have("ibiza"), "Ibiza no disponible")
    def test_reproduces_ibiza_approved_front_hub_mass(self):
        # HUB_MASS = 49,15 en la base física aprobada (decisión del Ibiza)
        front, rear = _suspension("ibiza").axles
        self.assertEqual([round(b.mass, 2) for b in front.unsprung], [49.15, 49.15])
        self.assertEqual(len(rear.unsprung), 1)
        self.assertTrue(rear.unsprung[0].coupled)  # eje torsional

    @unittest.skipUnless(_have("porsche_gt3_rs"), "Porsche no disponible")
    def test_reproduces_porsche_c5(self):
        front, rear = _suspension("porsche_gt3_rs").axles
        self.assertEqual({round(b.mass, 2) for b in front.unsprung}, {48.68})
        self.assertEqual({round(b.mass, 2) for b in rear.unsprung}, {57.16})

    @unittest.skipUnless(all(_have(n) for n in SEVEN), "muestras 200409 no disponibles")
    def test_200409_architectures(self):
        for name in INDEPENDENT:
            front, rear = _suspension(name).axles
            with self.subTest(vehicle=name):
                self.assertEqual(
                    [b.coupled for b in front.unsprung + rear.unsprung], [False] * 4
                )
                # trasera independiente: ningún nodo de mecanismo unido al buje
                self.assertTrue(all(not b.adjacent_mechanism for b in rear.unsprung))
        front, rear = _suspension(ATACAMA).axles
        self.assertEqual(len(rear.unsprung), 1)
        self.assertEqual(rear.unsprung[0].wheels, ("RL", "RR"))
        self.assertAlmostEqual(
            sum(m for _, m in rear.unsprung[0].adjacent_mechanism), 30.221, places=3
        )


@unittest.skipUnless(all(_have(n) for n in SEVEN), "muestras 200409 no disponibles")
class TestIrAndRules(unittest.TestCase):
    def test_ir_unsprung(self):
        for name in INDEPENDENT:
            for pos in ("front", "rear"):
                p = _ir(name).get(f"suspension.axles.{pos}.unsprung_mass_per_wheel")
                with self.subTest(vehicle=name, axle=pos):
                    self.assertEqual(
                        (p.status, p.kind, p.unit), ("present", "measured", "kg")
                    )
                    self.assertGreater(p.value, 0)
        p = _ir(ATACAMA).get("suspension.axles.rear.unsprung_mass_per_wheel")
        # G2.44: el eje rígido con barras (AXLE) tiene valor equivalente por rueda (mitad del eje)
        self.assertEqual((p.status, p.kind), ("present", "computed"))
        self.assertGreater(p.value, 0)

    def test_hub_mass_and_totalmass_by_new_rules(self):
        for name in INDEPENDENT:
            res, ir = _res(name), _ir(name)
            with self.subTest(vehicle=name):
                for t, rule in NEW_RULES.items():
                    self.assertEqual((res[t].source, res[t].rule), ("rule", rule))
                    self.assertEqual(res[t].status, "inferred")  # nunca known
                # masa física de AC = TOTALMASS + ΣHUB = masa BeamNG + piloto
                phys = res[TOTAL].value + 2 * res[HUB_F].value + 2 * res[HUB_R].value
                self.assertAlmostEqual(
                    phys,
                    ir.get("mass.total").value + ir.get("mass.driver").value,
                    places=6,
                )
                self.assertEqual(
                    res[HUB_F].value,
                    ir.get("suspension.axles.front.unsprung_mass_per_wheel").value,
                )

    def test_atacama_live_axle_blocks_rear_and_totalmass(self):
        res = _res(ATACAMA)
        self.assertEqual(
            (res[HUB_F].source, res[HUB_F].rule), ("rule", NEW_RULES[HUB_F])
        )
        # G2.44: resuelto por las reglas de eje rígido (TYPE=AXLE; tests/test_g244_rigid_axle.py)
        self.assertEqual(res[HUB_R].rule, "susp.hub_mass_rear_axle@1")
        self.assertEqual(res[TOTAL].rule, "car.totalmass_axle@1")

    def test_cg_location_and_basey_resolved_with_driver(self):
        # G2.45: con piloto de 70 kg las resuelven las reglas *_driver (posición del
        # piloto estimada); antes quedaban «manual_required»
        for name in SEVEN:
            res = _res(name)
            for t in (CGL,) + BASEY:
                with self.subTest(vehicle=name, target=t):
                    self.assertEqual(res[t].status, "inferred")
                    self.assertTrue(res[t].rule.split("@")[0].endswith("_driver"))
                    self.assertIsNotNone(res[t].value)


class TestRuleDefinitions(unittest.TestCase):
    def test_limited_to_200409_and_provisional(self):
        cat = Catalog.load(REPO_CATALOG)
        for key in NEW_RULES.values():
            rule = cat.rules[key]  # clave id@versión
            with self.subTest(rule=key):
                self.assertEqual(rule.status, "provisional")
                self.assertEqual(rule.open_issues, [])
                self.assertEqual(
                    [
                        (c.fact, c.op, c.value)
                        for c in rule.conditions
                        if c.fact
                        != "ir.suspension.axles.rear.linkage"  # G2.44: ne rigid_axle
                    ],
                    [("ir.formats.exporter_version", "in", ["200409"])],
                )
                self.assertEqual(rule.scope.family, "automation_export")

    @pytest.mark.ampliada
    def test_not_applied_outside_200409(self):
        for name in ["ibiza", "porsche_gt3_rs", "composed_fox"]:
            if not _have(name):
                continue
            res = {
                r.target: r
                for r in ConversionSession.from_ir(
                    import_preview(preview_physics(_source(name), 70.0), 70.0)
                ).resolve()
            }
            for t, rule in NEW_RULES.items():
                with self.subTest(vehicle=name, target=t):
                    self.assertNotEqual(res[t].rule, rule)

    @unittest.skipUnless(_have("ibiza"), "Ibiza no disponible")
    def test_ibiza_mass_values_unchanged(self):
        res = {r.target: r for r in ConversionSession.from_ir(_ir("ibiza")).resolve()}
        self.assertEqual(res[HUB_F].value, 49.15)
        self.assertEqual(res[HUB_R].value, 39.2)
        self.assertEqual(
            (res[TOTAL].rule, round(res[TOTAL].value, 2)), ("car.totalmass@2", 1119.57)
        )


class TestEvidenceFiles(unittest.TestCase):
    EV = ROOT / "output/g214_suspension_2026-11-03"

    @unittest.skipUnless((EV / "ab_suspension.json").is_file(), "sin evidencia A/B")
    def test_ab_identical_where_old_worked(self):
        d = json.loads((self.EV / "ab_suspension.json").read_text("utf-8"))
        for name, v in d.items():
            if v.get("old_error") is None and "load_error" not in v:
                with self.subTest(vehicle=name):
                    self.assertIs(v["identical"], True)


if __name__ == "__main__":
    unittest.main()
