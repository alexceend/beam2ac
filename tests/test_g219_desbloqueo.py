"""G2.19: desbloqueo integral del 200409.

Neumáticos, aerodinámica, nueve marchas y puntal. Pruebas unitarias (sin muestras) y
de integración sobre ``samples_beamng/`` (se omiten si no está). Ver
``docs/research/g219_desbloqueo_integral_200409.md``.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from src.attrs import transforms as T
from src.attrs.report import resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.extract import linkage as L
from src.extract.aero_bench import AeroBenchError, aero_tables, reduce_case
from src.importers import measurements as M
from src.importers.beamng import import_preview
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples_beamng"
LEGION = "2020_rpm_legion_grand_touring"
ATLANTA = "2020_rpm_atlanta_gt_3_6t"
ATACAMA = "2020_rpm_atacama_sport__4x4"
PORTLAND = "2021_rpm_portland_touring"
IBIZA_DIR = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"


def vdir(name: str) -> Path:
    return next(SAMPLES.glob(f"*/vehicles/{name}"))


def available() -> bool:
    return SAMPLES.is_dir() and any(SAMPLES.glob(f"*/vehicles/{LEGION}"))


_CACHE: dict = {}


def resolved(name: str, driver: float = 0.0):
    key = (name, driver)
    if key not in _CACHE:
        if name == "ibiza":
            src = load_vehicle_dir(IBIZA_DIR, "35b84.pc")
        else:
            src = load_vehicle_dir(vdir(name), f"{name}.pc")
        pv = preview_physics(src, driver)
        ir = import_preview(pv, driver)
        _, res = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", driver)
        _CACHE[key] = (pv, ir, {r.target: r for r in res})
    return _CACHE[key]


# ------------------------------------------------------------------ unitarias
def _case(F, M_rear, pitch=0.0, v=50.0, rho=1.225, wb=2.8):
    """Caso sintético del banco: fuerza F (adelante, izq., arriba) y momento usual
    M_rear en el centro del eje trasero; torque del juego = −momento."""
    axles = {
        "FL": [wb, 0.8, 0.3],
        "FR": [wb, -0.8, 0.3],
        "RL": [0, 0.8, 0.3],
        "RR": [0, -0.8, 0.3],
    }
    torque = {}
    for w, P in axles.items():
        r = [0 - P[0], 0 - P[1], 0.3 - P[2]]  # O_t − P (O_t = (0, 0, 0.3))
        extra = [
            r[1] * F[2] - r[2] * F[1],
            r[2] * F[0] - r[0] * F[2],
            r[0] * F[1] - r[1] * F[0],
        ]
        torque[w] = [-(a + b) for a, b in zip(M_rear, extra)]
    return {
        "v": v,
        "pitch": pitch,
        "yaw": 0.0,
        "rho": rho,
        "F": list(F),
        "axles": axles,
        "torque": torque,
    }


class TestAeroReduction(unittest.TestCase):
    def test_axle_split_and_wind_frame(self):
        q = 0.5 * 1.225 * 50**2
        # 0°: arrastre 0,5 m², carga 0,3 m² repartida 0,1 delante / 0,2 detrás
        F = [-0.5 * q, 0.0, -0.3 * q]
        M = [0.0, 2.8 * 0.1 * q, 0.0]  # carga delantera L_f a 2,8 m: M_izq = WB·L_f
        c = reduce_case(_case(F, M))
        self.assertAlmostEqual(c.cda, 0.5, places=9)
        self.assertAlmostEqual(c.cla, 0.3, places=9)
        self.assertAlmostEqual(c.cla_front, 0.1, places=9)
        self.assertAlmostEqual(c.cla_rear, 0.2, places=9)

    def test_pitch_rotates_to_wind_axes(self):
        q = 0.5 * 1.225 * 50**2
        p = math.radians(4)
        D, Lw = 0.5 * q, 0.3 * q
        # fuerza en ejes del viento → carrocería: arrastre −n, carga u_L
        F = [
            -D * math.cos(p) + Lw * math.sin(p),
            0.0,
            -D * math.sin(p) - Lw * math.cos(p),
        ]
        c = reduce_case(_case(F, [0.0, 0.0, 0.0], pitch=4.0))
        self.assertAlmostEqual(c.cda, 0.5, places=9)
        self.assertAlmostEqual(c.cla, 0.3, places=9)

    def test_tables_need_sweep(self):
        q = 0.5 * 1.225 * 50**2
        base = _case([-0.5 * q, 0, -0.3 * q], [0, 0, 0])
        with self.assertRaises(AeroBenchError):
            aero_tables({"cases": [base]})


class TestTransforms(unittest.TestCase):
    def test_axle_point(self):
        v = T.apply(
            "ac_axle_point",
            {"wheelbase": (2.8, "m"), "cg_location": (0.6, "1"), "basey": (-0.2, "m")},
            {"axle": "front"},
            "m",
        )
        self.assertEqual(v[0], 0.0)
        self.assertAlmostEqual(v[1], -0.2)
        self.assertAlmostEqual(v[2], 0.4 * 2.8)
        with self.assertRaises(T.TransformError):
            T.apply(
                "ac_axle_point",
                {"wheelbase": (2.8, "m"), "cg_location": (1.2, "1"), "basey": (0, "m")},
                {"axle": "rear"},
                "m",
            )

    def test_wing_table(self):
        out = T.apply(
            "ac_wing_table",
            {
                "table": ([[-2, 0.4], [2, 0.6]], "table:deg->m²"),
                "chord": (2.0, "m"),
                "span": (0.5, "m"),
            },
            {},
            "table:deg->1",
        )
        self.assertEqual(out, [[-2, 0.4], [2, 0.6]])

    def test_strut_axis(self):
        args = {"x": (1000.0, "N/m"), "strut_mr": (0.5, "1")}
        self.assertAlmostEqual(
            T.apply("ac_strut_axis", args, {"quantity": "rate"}, "N/m"), 4000.0
        )
        args = {"x": (0.1, "m"), "strut_mr": (0.5, "1")}
        self.assertAlmostEqual(
            T.apply("ac_strut_axis", args, {"quantity": "length"}, "m"), 0.05
        )
        with self.assertRaises(T.TransformError):
            T.apply(
                "ac_strut_axis",
                {"x": (1.0, "m"), "strut_mr": (0.0, "1")},
                {"quantity": "length"},
                "m",
            )


class TestRailKnuckle(unittest.TestCase):
    """MacPherson sintético: el raíl + 3 barras da rango 5 y el STRUT de AC (eje fijo en
    el buje por STRUT_CAR) reproduce la misma trayectoria."""

    def knuckle(self, aux_axis: bool) -> L.Knuckle:
        pts = {
            "_C": (0.0, 0.0, 0.0),
            "o": (-0.1, 0.0, 0.0),
            "i": (0.1, 0.0, 0.0),
            "bot": (0.05, -0.15, 0.0),
            "slide": (0.08, 0.15, 0.0),
            "rod": (0.06, -0.1, -0.15),
        }
        top = (0.15, 0.5, -0.02)
        if aux_axis:  # AC: punto auxiliar en el eje STRUT_TYRE → STRUT_CAR
            d = [a - b for a, b in zip(pts["slide"], top)]
            n = math.sqrt(sum(x * x for x in d))
            pts["aux"] = tuple(a + 0.1 * c / n for a, c in zip(pts["slide"], d))
            rails = [("slide", "aux", top)]
        else:  # BeamNG: raíl del nodo inferior de la mangueta al apoyo superior
            # (el nodo deslizante está en la recta por construcción)
            t = 0.4
            pts["bot"] = tuple(a + (a - b) * t for a, b in zip(pts["slide"], top))
            rails = [("slide", "bot", top)]
        links = [
            ((0.4, -0.12, 0.2), "bot"),
            ((0.4, -0.12, -0.2), "bot"),
            ((0.45, -0.1, -0.17), "rod"),
        ]
        return L.Knuckle(pts, "_C", ("o", "i"), links, rails=rails), pts["bot"]

    def test_rank_and_equivalence(self):
        kb, bot = self.knuckle(False)
        self.assertEqual(L.rank(L.constraint_eigen(kb)), 5)
        ka, _ = self.knuckle(True)
        ka.points["bot"] = bot
        ka.lengths = [
            math.dist(c, bot) if k == "bot" else ka.lengths[j]
            for j, (c, k) in enumerate(ka.links)
        ]
        self.assertEqual(L.rank(L.constraint_eigen(ka)), 5)
        for dy in (-0.05, 0.05):
            pb, rb = L.solve(kb, dy)
            pa, ra = L.solve(ka, dy)
            self.assertLess(max(rb, ra), 1e-9)
            for x, y in zip(L.wheel_angles(kb, pb), L.wheel_angles(ka, pa)):
                self.assertAlmostEqual(x, y, places=6)

    def test_no_rails_unchanged(self):
        kn = L.Knuckle(
            {"_C": (0, 0, 0), "a": (0.1, 0, 0)}, "_C", ("_C", "a"), [((1.0, 0, 0), "a")]
        )
        self.assertEqual(kn.constraint_errors([0.0] * 6), kn.link_errors([0.0] * 6))


class TestTyreSeverity(unittest.TestCase):
    """Un tyres.ini con solo dimensiones no puede pasar por completo: sin agarre de
    referencia, rigidez vertical ni inercia de giro, AC lee 0 (G2.19)."""

    def test_grip_keys_are_errors(self):
        from src.project.export import ZERO_IS_WRONG_KEYS, _blocker_severity

        for axle in ("FRONT", "REAR"):
            for key in ("ANGULAR_INERTIA", "RATE", "DX0", "DY0"):
                t = f"ac:tyres.ini/{axle}/{key}"
                self.assertIn(t, ZERO_IS_WRONG_KEYS)
                self.assertEqual(_blocker_severity({"target": t}, {}), "error")
            # una clave secundaria sigue siendo advertencia
            t = f"ac:tyres.ini/{axle}/FALLOFF_SPEED"
            self.assertEqual(_blocker_severity({"target": t}, {}), "warning")


class TestMeasurements(unittest.TestCase):
    def test_find_by_content_not_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class Src:  # fuente mínima con el mismo contenido
                class index:
                    parts = {}

                class pc:
                    parts = {"a": "b"}
                    vars = {}

            fp = M.source_fingerprint(Src)
            rec = {"schema": M.SCHEMA, "kind": "k", "source_fingerprint": fp}
            (root / "otro_nombre.json").write_text(json.dumps(rec), "utf-8")
            hit = M.find(Src, "k", root)
            self.assertIsNotNone(hit)
            self.assertIsNone(M.find(Src, "otro", root))
            (root / "dup.json").write_text(json.dumps(rec), "utf-8")
            with self.assertRaises(M.MeasurementError):
                M.find(Src, "k", root)


# ------------------------------------------------------------- integración
@unittest.skipUnless(available(), "sin samples_beamng")
class TestTyresGearsAero(unittest.TestCase):
    def test_tyre_dimensions(self):
        _, ir, res = resolved(LEGION)
        for axle, sec in (("front", "FRONT"), ("rear", "REAR")):
            for key, field in (
                ("RADIUS", "radius"),
                ("RIM_RADIUS", "hub_radius"),
                ("WIDTH", "width"),
                ("PRESSURE_STATIC", "pressure"),
            ):
                r = res[f"ac:tyres.ini/{sec}/{key}"]
                self.assertEqual(r.rule, f"tyre.{axle}.{key.lower()}_200409@1")
                self.assertEqual(r.value, ir.get(f"tyres.axles.{axle}.{field}").value)
        # G2.20: con el modelo SCTM medido (VERSION 10) DY0 ya no se lee:
        # «no aplica» (app.tyres.front.dy0_200409@1); el agarre va en DY_REF
        self.assertEqual(res["ac:tyres.ini/FRONT/DY0"].status, "not_applicable")
        self.assertEqual(
            res["ac:tyres.ini/FRONT/DY_REF"].rule, "tyre.front.dy_ref_200409@1"
        )

    def test_gear_9(self):
        _, _, res = resolved(LEGION)
        self.assertEqual(res["ac:drivetrain.ini/GEARS/GEAR_9"].status, "not_applicable")
        _, ir, res = resolved(PORTLAND)
        self.assertEqual(ir.get("drivetrain.gearbox.forward_count").value, 9)
        self.assertEqual(res["ac:drivetrain.ini/GEARS/GEAR_9"].status, "inferred")

    def test_aero_ir_and_rules(self):
        _, ir, res = resolved(LEGION)
        cda = ir.get("aero.cda_vs_aoa")
        self.assertEqual(
            (cda.status, cda.kind, cda.unit), ("present", "measured", "table:deg->m²")
        )
        self.assertEqual([r[0] for r in cda.value], [-4.0, -2.0, 0.0, 2.0, 4.0])
        lut = res["ac:aero.ini/WING_0/LUT_AOA_CD/table"]
        self.assertEqual(lut.rule, "aero.wing0_lut_aoa_cd_200409@1")
        self.assertEqual(lut.value, cda.value)  # CHORD·SPAN = 1 m²
        pos = res["ac:aero.ini/WING_1/POSITION"].value
        wb = res["ac:suspensions.ini/BASIC/WHEELBASE"].value
        cg = res["ac:suspensions.ini/BASIC/CG_LOCATION"].value
        self.assertAlmostEqual(pos[2], (1 - cg) * wb)
        self.assertEqual(res["ac:aero.ini/WING_0/CD_GAIN"].value, 1.0)

    def test_aero_positions_need_cg(self):
        # G2.45: con piloto el CG lo da la regla *_driver y las posiciones se resuelven
        _, _, res = resolved(LEGION, 70.0)
        self.assertIn(res["ac:aero.ini/WING_1/POSITION"].status, ("known", "inferred"))
        _, _, res = resolved(
            ATACAMA
        )  # G2.44: el eje rígido (AXLE) ya tiene CG_LOCATION
        self.assertIn(res["ac:aero.ini/WING_0/POSITION"].status, ("known", "inferred"))

    def test_modified_source_has_no_measurement(self):
        """Una copia con UN número cambiado ya no recibe la medida (huella)."""
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / "vehicles" / LEGION
            shutil.copytree(
                vdir(LEGION), dst, ignore=shutil.ignore_patterns("*.dae", "*.dds")
            )
            f = dst / "wheels_front.jbeam"
            f.write_text(
                f.read_text("utf-8").replace('"dragCoef":5', '"dragCoef":6'), "utf-8"
            )
            pv = preview_physics(load_vehicle_dir(dst, f"{LEGION}.pc"), 0.0)
            ir = import_preview(pv, 0.0)
            p = ir.get("aero.cda_vs_aoa")
            self.assertEqual(p.status, "missing")
            self.assertIn("huella", p.reason)

    def test_ibiza_untouched(self):
        _, ir, res = resolved("ibiza", 70.0)
        # G2.29: cefaero (analítico) llena el campo como tabla plana calculada; ninguna
        # regla lo usa en el 38022: la resolución del Ibiza no cambia
        p = ir.get("aero.cda_vs_aoa")
        self.assertEqual((p.status, p.kind), ("present", "computed"))
        self.assertEqual(p.value, [[-90.0, 0.582], [90.0, 0.582]])
        self.assertEqual(res["ac:aero.ini/WING_0/CHORD"].status, "manual_required")
        self.assertEqual(res["ac:tyres.ini/FRONT/RADIUS"].rule, "tyre.front.radius@2")
        self.assertEqual(res["ac:drivetrain.ini/GEARS/GEAR_9"].status, "not_applicable")


@unittest.skipUnless(available(), "sin samples_beamng")
class TestStrut(unittest.TestCase):
    def test_atlanta_strut(self):
        pv, ir, res = resolved(ATLANTA)
        ax = ir.suspension.axles["front"]
        self.assertEqual(ax.linkage.value, "strut_rail")
        s = ax.strut_motion_ratio.value
        self.assertTrue(0.9 < s < 0.97)
        self.assertEqual(res["ac:suspensions.ini/FRONT/TYPE"].value, "STRUT")
        k = res["ac:suspensions.ini/FRONT/SPRING_RATE"]
        self.assertEqual(k.rule, "susp.spring_rate_front_strut_200409@1")
        self.assertAlmostEqual(k.value, ax.wheel_rate.value / s**2, places=6)
        rl = res["ac:suspensions.ini/FRONT/ROD_LENGTH"].value
        self.assertAlmostEqual(rl, ax.spring_preload_travel.value * s, places=9)
        self.assertEqual(
            res["ac:suspensions.ini/FRONT/STRUT_TYRE"].value,
            ax.anchors["strut_upright"].value,
        )
        # las claves del DWB no aplican y ninguna regla DWB se usa delante
        self.assertEqual(
            res["ac:suspensions.ini/FRONT/WBTYRE_TOP"].status, "not_applicable"
        )
        cat = Catalog.load(REPO_CATALOG)
        dwb_gated = {
            k
            for k, rule in cat.rules.items()
            if any(
                c.fact == "ir.suspension.axles.front.linkage"
                and "dwb_5link" in (c.value or [])
                for c in rule.conditions
            )
        }
        used = {r.rule for t, r in res.items() if r.source == "rule"}
        self.assertFalse(used & dwb_gated, used & dwb_gated)

    def test_strut_mirrors_dwb_frame(self):
        """Los 7 puntos usan el marco de los del DWB (X hacia dentro, rueda derecha)."""
        _, _, res = resolved(ATLANTA)
        car = res["ac:suspensions.ini/FRONT/WBCAR_BOTTOM_FRONT"].value
        tyre = res["ac:suspensions.ini/FRONT/WBTYRE_BOTTOM"].value
        self.assertGreater(car[0], tyre[0])  # el chasis está más hacia dentro
        self.assertGreater(res["ac:suspensions.ini/FRONT/STRUT_CAR"].value[1], 0.3)

    def test_rear_dwb_unchanged(self):
        _, _, res = resolved(ATLANTA)
        self.assertEqual(res["ac:suspensions.ini/REAR/TYPE"].value, "DWB")
        self.assertEqual(
            res["ac:suspensions.ini/REAR/SPRING_RATE"].rule,
            "susp.spring_rate_rear_200409@1",
        )


if __name__ == "__main__":
    unittest.main()
