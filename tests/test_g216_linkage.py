"""G2.16: arquitectura de suspensión medida (enlaces), DWB de AC y dirección.

Sintéticas (mangueta con 5 y 8 barras, precompresión, simetría) y vehículos reales del
corpus (solo lectura; se omiten si faltan). Informe:
``docs/research/g216_suspension_steering_2026-11-04.md``.
"""

from __future__ import annotations

import math
import unittest
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.bench.service import default_beamng_root
from src.attrs import transforms, units
from src.attrs.store import REPO_CATALOG, Catalog
from src.convert.service import ConversionSession
from src.extract import linkage as L
from src.importers.beamng import import_preview
from src.ir.legacy_facts import ir_to_facts
from src.lab import kinematics as K
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
COMMON = Path(
    (default_beamng_root() / "content/vehicles/common.zip").as_posix()
)
SEVEN = [
    "2020_rpm_atacama_sport__4x4",
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
]
FRONT_DWB = [n for n in SEVEN if n != "2020_rpm_atlanta_gt_3_6t"]
REAR_DWB = [n for n in SEVEN if n != "2020_rpm_atacama_sport__4x4"]
ANCHOR_AC = {
    "upper_chassis_front": "WBCAR_TOP_FRONT",
    "upper_chassis_rear": "WBCAR_TOP_REAR",
    "lower_chassis_front": "WBCAR_BOTTOM_FRONT",
    "lower_chassis_rear": "WBCAR_BOTTOM_REAR",
    "upper_upright": "WBTYRE_TOP",
    "lower_upright": "WBTYRE_BOTTOM",
    "rod_chassis": "WBCAR_STEER",
    "rod_upright": "WBTYRE_STEER",
}


# ----------------------------------------------------------------- sintéticos
@dataclass
class N:
    id: str
    pos: tuple[float, float, float]


@dataclass
class B:
    nodes: tuple[str, str]
    props: dict[str, Any] = field(default_factory=dict)
    part: str = "p"

    def value(self, key, variables=None, default=None):
        return self.props.get(key, default)


def _corner(eight=False, skew=0.0, prec=None, side=-1):
    """Rueda derecha (side −1) o su reflejo izquierdo, en ejes BeamNG."""
    s = -side  # +1 derecha: x negativa
    P = {
        "w1": (-0.80, 0.0, 0.30),
        "w2": (-0.70, 0.0, 0.30),
        "kb": (-0.66, 0.0, 0.15),
        "kr": (-0.68, 0.12, 0.25),
        "cf1": (-0.40, -0.20, 0.47),
        "cr1": (-0.40, 0.20, 0.47),
        "cf2": (-0.30, -0.20, 0.13),
        "cr2": (-0.30, 0.20, 0.13 + skew),
        "rc": (-0.30, 0.13, 0.22),
        "sp": (-0.55, 0.0, 0.60),
    }
    if eight:
        P.update({"kt1": (-0.68, -0.05, 0.45), "kt2": (-0.68, 0.05, 0.45)})
        P.update({"kb1": (-0.66, -0.05, 0.15), "kb2": (-0.66, 0.05, 0.15)})
        P.pop("kb")
        P.pop("kr")
        P.pop("rc")
        knuckle = ["kt1", "kt2", "kb1", "kb2"]
        links = [("cf1", "kt1"), ("cr1", "kt1"), ("cf1", "kt2"), ("cr1", "kt2")]
        links += [("cf2", "kb1"), ("cr2", "kb1"), ("cf2", "kb2"), ("cr2", "kb2")]
        spring_lo = "kb1"
    else:
        P["kt"] = (-0.68, 0.0, 0.45)
        knuckle = ["kt", "kb", "kr"]
        links = [("cf1", "kt"), ("cr1", "kt"), ("cf2", "kb"), ("cr2", "kb")]
        links += [("rc", "kr")]
        spring_lo = "kb"
    nodes = {k: N(k, (v[0] if s > 0 else -v[0], v[1], v[2])) for k, v in P.items()}
    hub = ["w1", "w2"]
    body = hub + knuckle
    beams = [B((a, b)) for i, a in enumerate(body) for b in body[i + 1 :]]
    for c, k in links:
        props = {}
        if prec and (c, k) in prec:
            props["beamPrecompression"] = prec[(c, k)]
        beams.append(B((c, k), props))
    spring = B((spring_lo, "sp"), {"beamSpring": 50000, "precompressionRange": 0.05})
    damper = B((spring_lo, "sp"), {"beamType": "|BOUNDED", "beamDamp": 3000})
    return nodes, beams, spring, damper


def _analyse(**kw):
    side = kw.get("side", -1)
    nodes, beams, spring, damper = _corner(**kw)
    rigid = L.rigid_link_beams(beams, {})
    return L.analyse_corner(
        wheel="RR" if side < 0 else "RL",
        hub=("w1", "w2"),
        side=side,
        nodes=nodes,
        rigid_beams=rigid,
        spring=spring,
        damper=damper,
        variables={},
    )


class TestKinematicsCopy(unittest.TestCase):
    """La cinemática de producción da lo mismo que la del laboratorio (sin pesos)."""

    def test_same_as_lab(self):
        pts = {
            "_C": (-0.75, 0.30, 0.0),
            "o": (-0.80, 0.30, 0.0),
            "i": (-0.70, 0.30, 0.0),
            "kt": (-0.68, 0.45, 0.0),
            "kb": (-0.66, 0.15, 0.0),
            "kr": (-0.68, 0.25, -0.12),
        }
        links = [
            ((-0.40, 0.47, 0.20), "kt"),
            ((-0.40, 0.47, -0.20), "kt"),
            ((-0.30, 0.13, 0.20), "kb"),
            ((-0.30, 0.13, -0.20), "kb"),
            ((-0.30, 0.22, -0.13), "kr"),
        ]
        lab = K.Knuckle(dict(pts), "_C", ("o", "i"), list(links))
        prod = L.Knuckle(dict(pts), "_C", ("o", "i"), list(links))
        spring = ((-0.55, 0.60, 0.0), "kb")
        for dy in (-0.05, 0.0, 0.03):
            pl, _ = K.solve(lab, dy)
            pp, _ = L.solve(prod, dy)
            for a, b in zip(K.wheel_angles(lab, pl), L.wheel_angles(prod, pp)):
                self.assertAlmostEqual(a, b, places=7)
        self.assertAlmostEqual(
            K.motion_ratio(lab, spring), L.motion_ratio(prod, spring, [0.0] * 6), 7
        )


class TestSyntheticLinkage(unittest.TestCase):
    def test_five_link_dwb(self):
        r = _analyse()
        self.assertEqual(r.kind, "dwb_5link")
        self.assertEqual(r.rank, 5)
        # anclajes = nodo − centro en ejes AC (lateral hacia dentro, arriba, adelante)
        self.assertEqual(r.anchor_nodes["upper_upright"], "kt")
        for k, v in zip(r.anchors["upper_upright"], (0.07, 0.15, 0.0)):
            self.assertAlmostEqual(k, v, places=9)
        self.assertEqual(r.anchor_nodes["rod_chassis"], "rc")
        # +Y de BeamNG es hacia atrás: la barra (y = 0,12) va detrás del eje
        self.assertFalse(r.rod_ahead_of_axle)
        self.assertAlmostEqual(r.spring_rest_compression, 0.05, places=9)
        self.assertGreater(r.rod_shift_per_toe_deg, 0)
        self.assertLess(r.deviation_deg, 1e-9)

    def test_left_is_mirror_of_right(self):
        r, l_ = _analyse(), _analyse(side=1)
        self.assertEqual(r.kind, l_.kind)
        for k in L.ANCHORS:
            for a, b in zip(r.anchors[k], l_.anchors[k]):
                self.assertAlmostEqual(a, b, places=9)
        self.assertAlmostEqual(r.spring_motion_ratio, l_.spring_motion_ratio, places=9)

    def test_precompression_sets_rest_pose(self):
        """L·beamPrecompression es la longitud de reposo: acortar el triángulo superior
        mete la rótula hacia dentro (caída más negativa)."""
        base = _analyse()
        prec = {("cf1", "kt"): 0.99, ("cr1", "kt"): 0.99}
        r = _analyse(prec=prec)
        self.assertEqual(r.kind, "dwb_5link")
        self.assertLess(r.rest_camber_deg, base.rest_camber_deg - 0.5)
        lk = next(x for x in r.links if (x.chassis, x.upright) == ("cf1", "kt"))
        self.assertAlmostEqual(lk.rest_length, lk.jbeam_length * 0.99, places=12)
        got = math.dist(
            tuple(c + 0 for c in r.anchors["upper_chassis_front"]),
            r.anchors["upper_upright"],
        )
        self.assertAlmostEqual(got, lk.rest_length, places=7)

    def test_eight_compatible_links_have_an_equivalent_dwb(self):
        r = _analyse(eight=True)
        self.assertEqual(len(r.links), 8)
        self.assertEqual(r.rank, 5)
        self.assertEqual(r.kind, "dwb_equivalent")
        self.assertLess(r.deviation_deg, L.EQUIV_DEG)

    def test_eight_incompatible_links_are_fitted(self):
        """G2.30: rango 6 sin subconjunto equivalente: DWB con los puntos del chasis
        ajustados a la trayectoria elástica del conjunto completo + barras redundantes.
        """
        r = _analyse(eight=True, skew=0.03)
        self.assertEqual(r.kind, "dwb_fitted")
        self.assertTrue(r.is_dwb)
        self.assertLessEqual(r.deviation_deg, L.EQUIV_DEG * L.FIT_ACCEPT)
        self.assertLessEqual(r.deviation_m, L.EQUIV_M * L.FIT_ACCEPT)
        self.assertEqual(len(r.fit_displacement), 5)
        self.assertLess(max(r.fit_displacement), L.FIT_MAX_MOVE)
        self.assertGreater(r.redundant_link_rate, 0)
        self.assertIsNotNone(r.redundant_link_force)
        self.assertIn("ajustados", r.reason)

    def test_compatible_links_have_no_redundant_stiffness(self):
        self.assertIsNone(_analyse(eight=True).redundant_link_rate)

    def test_eight_incompatible_links_stay_overconstrained_if_fit_is_rejected(self):
        old = L.FIT_MAX_MOVE
        L.FIT_MAX_MOVE = 0.0  # ningún desplazamiento admitido: el ajuste se rechaza
        try:
            r = _analyse(eight=True, skew=0.03)
        finally:
            L.FIT_MAX_MOVE = old
        self.assertEqual(r.kind, "overconstrained")
        self.assertFalse(r.is_dwb)
        self.assertIn("decisión", r.reason)
        self.assertGreater(r.deviation_deg, L.EQUIV_DEG)

    def test_coupled_and_underconstrained(self):
        nodes, beams, spring, damper = _corner()
        # un enlace a la mangueta del otro lado (eje rígido). G2.29: acopla porque el
        # reflejado del nodo lejano pertenece a esta mangueta y se mueve con la rueda;
        # un nodo del chasis al otro lado (sin reflejado en la mangueta) ya no acopla
        x, y, z = nodes["kb"].pos
        nodes["x0"] = N("x0", (-x, y, z))
        beams.append(B(("x0", "kb")))
        r = L.analyse_corner(
            wheel="RR",
            hub=("w1", "w2"),
            side=-1,
            nodes=nodes,
            rigid_beams=L.rigid_link_beams(beams, {}),
            spring=spring,
            damper=damper,
            variables={},
        )
        self.assertEqual(r.kind, "coupled")
        nodes, beams, spring, damper = _corner()
        beams = [b for b in beams if b.nodes not in (("rc", "kr"), ("cr2", "kb"))]
        r = L.analyse_corner(
            wheel="RR",
            hub=("w1", "w2"),
            side=-1,
            nodes=nodes,
            rigid_beams=L.rigid_link_beams(beams, {}),
            spring=spring,
            damper=damper,
            variables={},
        )
        self.assertEqual(r.kind, "underconstrained")
        self.assertLess(r.rank, 5)


class TestTransforms(unittest.TestCase):
    def test_vector_identity(self):
        v = transforms.apply(
            "vector_identity", {"x": ([0.1, 0.2, 0.3], "m")}, {"length": 3}, "m"
        )
        self.assertEqual(v, [0.1, 0.2, 0.3])
        with self.assertRaises(transforms.TransformError):
            transforms.apply("vector_identity", {"x": ([0.1], "m")}, {"length": 3}, "m")
        with self.assertRaises(transforms.TransformError):
            transforms.apply("vector_identity", {"x": (0.1, "m")}, {"length": 3}, "m")

    def test_as_undocumented(self):
        p = {"unit": "m/deg", "semantics": "m por grado"}
        self.assertEqual(
            transforms.apply(
                "as_undocumented", {"x": (0.003, "m/deg")}, p, "undocumented"
            ),
            0.003,
        )
        with self.assertRaises(transforms.TransformError):
            transforms.apply("as_undocumented", {"x": (0.003, "m")}, p, "undocumented")
        with self.assertRaises(transforms.TransformError):
            transforms.apply(
                "as_undocumented",
                {"x": (0.003, "m/deg")},
                {"unit": "m/deg"},
                "undocumented",
            )

    def test_m_per_deg(self):
        self.assertAlmostEqual(units.convert(1.0, "m/deg", "m/rad"), 180 / math.pi)


# ----------------------------------------------------------------- corpus real
def _dir_of(name: str) -> Path | None:
    if name == "ibiza":
        return IBIZA if IBIZA.is_dir() else None
    for top in SAMPLES.glob("*"):
        vd = top / "vehicles" / name
        if vd.is_dir():
            return vd
    return None


def _have(name: str) -> bool:
    if name == "porsche_gt3_rs":
        return (MODS / "porsche_gt3_rs.zip").is_file() and COMMON.is_file()
    return _dir_of(name) is not None


@lru_cache(maxsize=None)
def _ir(name: str):
    if name == "porsche_gt3_rs":
        src = load_vehicle_zip(MODS / "porsche_gt3_rs.zip", COMMON)
    else:
        vd = _dir_of(name)
        pc = (
            "35b84.pc"
            if name == "ibiza"
            else sorted(p.name for p in vd.glob("*.pc"))[0]
        )
        src = load_vehicle_dir(vd, pc)
    return import_preview(preview_physics(src, 70.0), 70.0)


@lru_cache(maxsize=None)
def _res(name: str) -> dict:
    return {r.target: r for r in ConversionSession.from_ir(_ir(name)).resolve()}


def need(*names):
    return unittest.skipUnless(all(_have(n) for n in names), "corpus no disponible")


class TestCorpusLinkage(unittest.TestCase):
    @need(*SEVEN)
    def test_architectures_by_structure(self):
        want = {n: ("dwb_5link", "dwb_equivalent") for n in SEVEN}
        # G2.19: el puntal de atlanta (raíl + brazo + bieleta) se mide como
        # strut_rail (antes underconstrained: el modelo no representaba el raíl)
        want["2020_rpm_atlanta_gt_3_6t"] = ("strut_rail", "dwb_equivalent")
        want["2020_rpm_atacama_sport__4x4"] = ("dwb_5link", "rigid_axle")  # G2.44: eje de barras
        for name, (f, r) in want.items():
            with self.subTest(vehicle=name):
                ir = _ir(name)
                self.assertEqual(ir.get("suspension.axles.front.linkage").value, f)
                self.assertEqual(ir.get("suspension.axles.rear.linkage").value, r)

    @need("eagle_inca_p395_dale_earnhardt_edition")
    def test_eagle_rear_h_arm_is_strut_with_redundant_bar(self):
        """Trasera MacPherson con brazo en H (4 barras, una redundante): strut_rail y
        las claves traseras de STRUT resuelven en el 40340 (antes ``unsupported``)."""
        name = "eagle_inca_p395_dale_earnhardt_edition"
        ir = _ir(name)
        self.assertEqual(ir.get("suspension.axles.rear.linkage").value, "strut_rail")
        res = _res(name)
        for key in ("REAR/TYPE", "REAR/SPRING_RATE", "REAR/WBCAR_STEER", "REAR/STRUT_CAR"):
            with self.subTest(key=key):
                self.assertIn(res[f"ac:suspensions.ini/{key}"].status, ("inferred", "known"))

    @need("porsche_gt3_rs")
    def test_38022_rear_is_fitted(self):
        """G2.30: antes ``overconstrained`` (sin anclajes ni rigidez); ahora DWB
        ajustado, con
        la rigidez de las barras redundantes sumada y los anclajes como valor calculado.
        """
        ir = _ir("porsche_gt3_rs")
        self.assertEqual(ir.get("suspension.axles.front.linkage").value, "dwb_5link")
        rear = ir.get("suspension.axles.rear.linkage")
        self.assertEqual(rear.value, "dwb_fitted")
        wr = ir.get("suspension.axles.rear.wheel_rate")
        self.assertEqual((wr.status, wr.kind), ("present", "computed"))
        self.assertIn("barras redundantes", wr.note)
        anchors = ir.suspension.axles["rear"].anchors
        self.assertEqual(anchors["upper_chassis_front"].kind, "computed")
        self.assertEqual(anchors["upper_upright"].kind, "measured")

    @need(*SEVEN)
    def test_derived_values(self):
        for name in FRONT_DWB:
            with self.subTest(vehicle=name):
                ax = _ir(name).suspension.axles["front"]
                mr = ax.kinematic_motion_ratio.value
                self.assertAlmostEqual(mr, 0.9858, places=3)
                k = ax.spring_rate_beam.value
                self.assertAlmostEqual(ax.wheel_rate.value, k * mr * mr, places=1)
                self.assertEqual(ax.wheel_rate.kind, "computed")
                self.assertEqual(len(ax.anchors), 8)
                # convergencia en reposo por la precompresión de los enlaces (≈ +0,08°)
                self.assertAlmostEqual(ax.rest_toe.value, 0.078, delta=0.003)
        for name in REAR_DWB:
            with self.subTest(vehicle=name, axle="rear"):
                ax = _ir(name).suspension.axles["rear"]
                self.assertAlmostEqual(
                    ax.kinematic_motion_ratio.value, 0.9676, places=3
                )
                self.assertAlmostEqual(ax.rest_camber.value, -1.349, delta=0.002)

    @need(*SEVEN)
    def test_no_new_bng_facts(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                facts = ir_to_facts(_ir(name))
                self.assertFalse(
                    [f for f in facts if f.startswith("bng.") and "linkage" in f]
                )
                self.assertFalse(
                    [f for f in facts if "anchors" in f and f.startswith("bng.")]
                )


class TestCorpusRules(unittest.TestCase):
    @need(*SEVEN)
    def test_dwb_rules_resolve_only_dwb_axles(self):
        for name in SEVEN:
            res = _res(name)
            for axle, AX, ok in (
                ("front", "FRONT", name in FRONT_DWB),
                ("rear", "REAR", name in REAR_DWB),
            ):
                with self.subTest(vehicle=name, axle=axle):
                    t = res[f"ac:suspensions.ini/{AX}/TYPE"]
                    if ok:
                        self.assertEqual(
                            (t.rule, t.value, t.status),
                            (f"susp.type_dwb_{axle}_200409@1", "DWB", "inferred"),
                        )
                        ir = _ir(name).suspension.axles[axle]
                        for nm, key in ANCHOR_AC.items():
                            r = res[f"ac:suspensions.ini/{AX}/{key}"]
                            self.assertEqual(r.status, "inferred")
                            self.assertEqual(r.value, ir.anchors[nm].value)
                        self.assertEqual(
                            res[f"ac:suspensions.ini/{AX}/SPRING_RATE"].value,
                            ir.wheel_rate.value,
                        )
                    else:
                        self.assertNotEqual(t.rule, f"susp.type_dwb_{axle}_200409@1")
                        self.assertNotEqual(t.value, "DWB")

    @need(*SEVEN)
    def test_steering(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                ir, res = _ir(name), _res(name)
                sr = res["ac:car.ini/CONTROLS/STEER_RATIO"]
                lsrr = res["ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO"]
                strut = ir.get("suspension.axles.front.linkage").value == "strut_rail"
                if name not in FRONT_DWB and not strut:
                    self.assertIsNone(sr.value)
                    self.assertEqual(ir.steering.steering_ratio.status, "missing")
                    continue
                st = ir.steering
                self.assertEqual(st.tie_rod_position.value, "behind")
                want = (
                    450.0
                    * st.rack_displacement_per_wheel_degree.value
                    / (st.rack_travel.value)
                )
                # el IR redondea el desplazamiento a 1e-8 m/°
                self.assertAlmostEqual(st.steering_ratio.value, want, delta=1e-4)
                # barra detrás del eje ⇒ negativo (convención de Kunos; Ibiza en AC)
                # G2.19: con el puntal en raíl (atlanta), la regla clonada del puntal
                self.assertEqual(
                    sr.rule,
                    (
                        "ctrl.steer_ratio_rod_behind_strut_200409@1"
                        if strut
                        else "ctrl.steer_ratio_rod_behind_200409@1"
                    ),
                )
                self.assertAlmostEqual(sr.value, -st.steering_ratio.value, places=9)
                self.assertEqual(sr.status, "inferred")
                # G2.17 (D-G216-LSRR): la unidad m/° se documenta en la regla @2
                # (antes @1 con unidad 'undocumented' ⇒ E0, manual_required); el valor
                # no cambia
                self.assertEqual(
                    lsrr.rule,
                    (
                        "ctrl.linear_steer_rod_ratio_strut_200409@1"
                        if strut
                        else "ctrl.linear_steer_rod_ratio_200409@2"
                    ),
                )
                self.assertEqual(lsrr.status, "inferred")
                self.assertEqual(lsrr.unit, "m/deg")
                self.assertAlmostEqual(
                    lsrr.value, st.rack_displacement_per_wheel_degree.value
                )

    @need(*SEVEN)
    def test_dct_clutch(self):
        for name in SEVEN:
            with self.subTest(vehicle=name):
                ir, res = _ir(name), _res(name)
                r = res["ac:drivetrain.ini/CLUTCH/MAX_TORQUE"]
                if ir.drivetrain.gearbox.kind.value == "dctGearbox":
                    self.assertEqual(
                        (r.rule, r.status),
                        ("dt.clutch_max_torque_dct_200409@1", "inferred"),
                    )
                    self.assertEqual(
                        r.value, ir.drivetrain.clutch.lock_torque_default.value
                    )
                else:
                    self.assertNotEqual(r.rule, "dt.clutch_max_torque_dct_200409@1")
                    self.assertIsNone(r.value)

    @need("porsche_gt3_rs")
    def test_not_applied_outside_200409(self):
        res = _res("porsche_gt3_rs")
        new = {
            r.key
            for r in Catalog.load(REPO_CATALOG).rules.values()
            if any(h.date == "2026-11-04" for h in r.history)
        }
        self.assertTrue(new)
        for t, r in res.items():
            with self.subTest(target=t):
                self.assertNotIn(r.rule, new)

    @need("ibiza")
    def test_ibiza_unchanged(self):
        res = _res("ibiza")
        t = res["ac:suspensions.ini/FRONT/TYPE"]
        self.assertEqual((t.rule, t.value), ("susp.type_front@2", "STRUT"))
        sr = res["ac:car.ini/CONTROLS/STEER_RATIO"]
        self.assertEqual((sr.source, sr.value), ("decision", -12.14))
        # G2.19: el extractor (común) mide ahora el puntal en raíl del Ibiza como
        # strut_rail (antes underconstrained). Ninguna regla del 200409 se le aplica:
        # TYPE y STEER_RATIO, arriba, no cambian.
        self.assertEqual(
            _ir("ibiza").get("suspension.axles.front.linkage").value, "strut_rail"
        )


if __name__ == "__main__":
    unittest.main()
