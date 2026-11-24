"""G2.45: piloto (masa y posición estimada) en CG_LOCATION, BASEY e inercia."""

import pytest
import unittest
from pathlib import Path

from src.attrs import transforms
from src.extract import driver_position as dp

ROOT = Path(__file__).resolve().parents[1]
CG, BF, BR, INERTIA, TM = (
    "ac:suspensions.ini/BASIC/CG_LOCATION",
    "ac:suspensions.ini/FRONT/BASEY",
    "ac:suspensions.ini/REAR/BASEY",
    "ac:car.ini/EXPLICIT_INERTIA/INERTIA",
    "ac:car.ini/BASIC/TOTALMASS",
)


def _scene():
    """Coche sintético de masas puntuales: carrocería + 4 bujes en las ruedas."""
    wb, tf, tr = 2.6, 1.5, 1.5
    hd, ht = 40.0, 35.0
    zf, zr = 1.2, -1.4  # ejes en z AC
    yw = 0.3
    # CdG en x = 0, como en los coches reales (el conversor lo supone en ±TRACK/2)
    body = [  # (masa, posición AC)
        (500.0, (0.0, 0.5, 0.9)),
        (450.0, (0.3, 0.8, -0.1)),
        (300.0, (-0.45, 0.6, -0.9)),
        (120.0, (0.0, 1.0, 0.4)),
    ]
    hubs = [(hd, (s * tf / 2, yw, zf)) for s in (1, -1)] + [
        (ht, (s * tr / 2, yw, zr)) for s in (1, -1)
    ]
    return wb, tf, tr, hd, ht, zf, zr, yw, body, hubs


def _mass_cog_inertia(points):
    m = sum(p[0] for p in points)
    cog = [sum(p[0] * p[1][i] for p in points) / m for i in range(3)]
    inertia = [
        sum(
            p[0]
            * (sum((p[1][j] - cog[j]) ** 2 for j in range(3)) - (p[1][i] - cog[i]) ** 2)
            for p in points
        )
        for i in range(3)
    ]
    return m, cog, inertia


def _inputs(driver_mass, driver_rel):
    wb, tf, tr, hd, ht, zf, zr, yw, body, hubs = _scene()
    m, cog, inertia = _mass_cog_inertia(body + hubs)  # vehículo SIN piloto
    ins = {
        "total": (m, "kg"),
        "front_fraction": ((cog[2] - zr) / wb, "1"),
        "hub_front": (hd, "kg"),
        "hub_rear": (ht, "kg"),
        "height_front": (yw - cog[1], "m"),
        "height_rear": (yw - cog[1], "m"),
        "wheelbase": (wb, "m"),
        "track_front": (tf, "m"),
        "track_rear": (tr, "m"),
        "inertia": (inertia, "kg·m²"),
    }
    drv = {"driver_mass": (driver_mass, "kg"), "driver_position": (driver_rel, "m")}
    return ins, drv, cog


def _brute(driver_mass, driver_rel):
    """Cuerpo de AC (todo menos los bujes) calculado con masas puntuales."""
    wb, tf, tr, hd, ht, zf, zr, yw, body, hubs = _scene()
    _, cog, _ = _mass_cog_inertia(body + hubs)
    pd = tuple(c + d for c, d in zip(cog, driver_rel))
    pts = body + ([(driver_mass, pd)] if driver_mass else [])
    mc, g, inertia = _mass_cog_inertia(pts)
    cg_loc = (g[2] - zr) / wb
    return cg_loc, yw - g[1], inertia


class TestTransforms(unittest.TestCase):
    REL = (0.32, 0.2, -0.45)

    def apply(self, name, ins, params, out):
        return transforms.apply(name, ins, params, out)

    def run_all(self, mass):
        ins, drv, _ = _inputs(mass, self.REL)
        cgi = {k: ins[k] for k in ("total", "front_fraction", "hub_front", "hub_rear")}
        out = {}
        out["cg"] = self.apply(
            "ac_body_cg",
            {**cgi, **drv, "wheelbase": ins["wheelbase"]},
            {"output": "cg_location", "driver": True},
            "1",
        )
        for k in ("front", "rear"):
            out[k] = self.apply(
                "ac_body_cg",
                {
                    **cgi,
                    **drv,
                    "height_front": ins["height_front"],
                    "height_rear": ins["height_rear"],
                },
                {"output": f"basey_{k}", "driver": True},
                "m",
            )
        out["inertia"] = self.apply(
            "ac_body_inertia", {**ins, **drv}, {"driver": True}, "kg·m²"
        )
        return out

    def test_matches_brute_force_point_masses(self) -> None:
        for mass in (0.0, 50.0, 70.0, 100.0, 120.0):
            with self.subTest(mass=mass):
                o = self.run_all(mass)
                cg, by, inertia = _brute(mass, self.REL)
                self.assertAlmostEqual(o["cg"], cg, places=9)
                self.assertAlmostEqual(o["front"], by, places=9)
                self.assertAlmostEqual(o["rear"], by, places=9)
                for a, b in zip(o["inertia"], inertia):  # redondeo a 0,1 kg·m²
                    self.assertAlmostEqual(a, b, delta=0.06)

    def test_zero_driver_equals_the_nodriver_form(self) -> None:
        ins, drv, _ = _inputs(0.0, self.REL)
        base = {k: ins[k] for k in ("total", "front_fraction", "hub_front", "hub_rear")}
        self.assertEqual(
            self.apply(
                "ac_body_cg",
                {**base, **drv, "wheelbase": ins["wheelbase"]},
                {"output": "cg_location", "driver": True},
                "1",
            ),
            self.apply("ac_body_cg", base, {"output": "cg_location"}, "1"),
        )
        self.assertEqual(
            self.apply("ac_body_inertia", {**ins, **drv}, {"driver": True}, "kg·m²"),
            self.apply("ac_body_inertia", ins, {}, "kg·m²"),
        )

    def test_stable_and_monotonic_in_driver_mass(self) -> None:
        prev = None
        for mass in (0, 30, 60, 70, 90, 120):
            o = self.run_all(float(mass))
            if prev:
                self.assertLess(o["cg"], prev["cg"])  # el piloto está detrás del CdG
                self.assertLess(
                    o["front"], prev["front"]
                )  # y por encima: bajan los ejes
                self.assertTrue(
                    all(a > b for a, b in zip(o["inertia"], prev["inertia"]))
                )
            self.assertLess(abs(o["cg"] - self.run_all(0.0)["cg"]), 0.02)
            prev = o

    def test_inputs_are_strict(self) -> None:
        ins, drv, _ = _inputs(70.0, self.REL)
        base = {k: ins[k] for k in ("total", "front_fraction", "hub_front", "hub_rear")}
        with self.assertRaises(transforms.TransformError):  # falta wheelbase
            self.apply(
                "ac_body_cg",
                {**base, **drv},
                {"output": "cg_location", "driver": True},
                "1",
            )
        with self.assertRaises(transforms.TransformError):  # piloto sin declarar
            self.apply(
                "ac_body_cg",
                {**base, "wheelbase": ins["wheelbase"]},
                {"output": "cg_location", "driver": True},
                "1",
            )
        bad = dict(drv, driver_mass=(-1.0, "kg"))
        with self.assertRaises(transforms.TransformError):
            self.apply(
                "ac_body_cg",
                {**base, **bad, "wheelbase": ins["wheelbase"]},
                {"output": "cg_location", "driver": True},
                "1",
            )


class TestPositionEstimator(unittest.TestCase):
    NODES = [(0.0, y / 10, 0.0) for y in range(0, 20)] * 6
    ZR, ZF = -1.3, 1.2

    def test_default_without_camera_uses_roof_and_wheelbase_only(self) -> None:
        p = dp.estimate(None, self.NODES, self.ZR, self.ZF)
        self.assertEqual(p.source, "default")
        self.assertAlmostEqual(p.eyes[0], dp.SEAT_X)
        self.assertAlmostEqual(
            p.eyes[2], self.ZR + dp.SEAT_Z_FRAC * (self.ZF - self.ZR)
        )
        self.assertAlmostEqual(p.eyes[1], p.roof_y - dp.ROOF_MINUS_EYES)
        self.assertAlmostEqual(p.com[1], p.eyes[1] + dp.EYES_TO_COM[1])

    def test_plausible_camera_is_used_and_implausible_is_not(self) -> None:
        good = (0.33, 1.65, -0.1)
        self.assertEqual(
            dp.estimate(good, self.NODES, self.ZR, self.ZF).source, "camera"
        )
        for bad in ((0.0, 1.65, -0.1), (0.33, 0.2, -0.1), (0.33, 1.65, 3.0)):
            with self.subTest(camera=bad):
                p = dp.estimate(bad, self.NODES, self.ZR, self.ZF)
                self.assertEqual(p.source, "default")
                self.assertIn("no plausible", p.note)

    def test_degenerate_input_is_an_error_not_a_default(self) -> None:
        with self.assertRaises(ValueError):
            dp.estimate(None, [], self.ZR, self.ZF)
        with self.assertRaises(ValueError):
            dp.estimate(None, self.NODES, 1.0, 1.0)


CARS = {  # coche → origen de los ojos esperado
    "2020_rpm_atlanta_gt_3_6t": "default",  # 200409: sin cockpitCamera
    "2020_rpm_atacama_sport__4x4": "default",  # 200409, eje rígido
    "thershow_hyacinth_sage": "camera",  # 40340, cámara plausible
    "thershow_quail_mirabilis": "camera",  # 40340, cámara plausible
    "thershow_autist_cuanto": "default",  # 40340, cámara por defecto de Automation
    "thershow_sucette_poubelle": "default",  # 40340, cámara en x = 0
}
_IR: dict = {}


def _ir_of(name):
    """IR a 70 kg de un coche del corpus (carpeta samples_beamng/<name>)."""
    from src.app.diagnostics import find_source
    from src.convert.service import ConversionSession

    if name not in _IR:
        src = ROOT / "samples_beamng" / name
        if not src.is_dir():
            return None
        _, vd, _, cfgs = find_source(str(src))
        _IR[name] = ConversionSession.from_source(vd, None, None, 70.0).ir
    return _IR[name]


class TestRealVehicles(unittest.TestCase):
    def resolve(self, ir, mass):
        from src.convert.service import ConversionSession
        from src.ir.param import Param

        ir.set("mass.driver", Param.of(mass, "kg", "manual"))
        s = ConversionSession.from_ir(ir)
        return {r.target: r for r in s.resolve()}

    @pytest.mark.ampliada
    def test_cars_and_masses(self) -> None:
        ran = 0
        for name, origin in CARS.items():
            ir = _ir_of(name)
            if ir is None:
                with self.subTest(car=name):
                    self.skipTest("muestra no disponible")
                continue
            ran += 1
            pos = ir.mass.driver_position
            self.assertEqual(pos.kind, "estimated", name)
            self.assertIn(f"({origin})", pos.note, name)  # procedencia visible
            ref = self.resolve(ir, 0.0)
            tm0 = float(ref[TM].value)
            self.assertTrue(ref[CG].rule.split("@")[0].endswith("_nodriver"), name)
            for mass in (0.0, 35.0, 70.0, 100.0, 120.0):
                with self.subTest(car=name, mass=mass):
                    res = self.resolve(ir, mass)
                    for key in (CG, BF, BR, INERTIA, TM):
                        self.assertEqual(res[key].status, "inferred", (key, res[key]))
                        self.assertIsNotNone(res[key].value)
                    fam = "_driver" if mass > 0 else "_nodriver"
                    self.assertTrue(res[CG].rule.split("@")[0].endswith(fam))
                    # el piloto entra exactamente una vez, en TOTALMASS
                    self.assertAlmostEqual(float(res[TM].value) - tm0, mass, places=6)
                    cg = float(res[CG].value)
                    self.assertTrue(0.3 < cg < 0.8)
                    self.assertLess(abs(cg - float(ref[CG].value)), 0.02)
                    for key in (BF, BR):
                        d = abs(float(res[key].value) - float(ref[key].value))
                        # el CdG sube como mucho ~0,6 m·(m_piloto/masa total)
                        self.assertLess(d, 0.6 * mass / float(res[TM].value) + 0.002)
                    self.assertTrue(all(v > 0 for v in res[INERTIA].value))
        if not ran:
            self.skipTest("muestras no disponibles")

    def test_independent_of_car_id(self) -> None:
        # ninguna regla nueva menciona un vehículo concreto
        from src.attrs.store import REPO_CATALOG, Catalog

        cat = Catalog.load(REPO_CATALOG)
        mine = [
            r for r in cat.rules.values() if r.id.endswith("_driver") and r.version == 1
        ]
        self.assertEqual(len(mine), 16)
        for r in mine:
            self.assertEqual(r.scope.level, "family")
            self.assertFalse(r.scope.vehicle)
            self.assertFalse([c for c in r.conditions if "vehicle_id" in c.fact])
            self.assertTrue(
                any(c.fact == "ir.mass.driver" and c.op == "gt" for c in r.conditions)
            )

    def test_ibiza_decisions_win_and_rule_is_close(self) -> None:
        """El Ibiza protegido conserva sus decisiones; la regla genérica queda cerca."""
        from pathlib import Path

        from src.convert.service import ConversionSession

        ibiza = (
            Path(__file__).resolve().parents[1]
            / "sample_beamng/vehicles/seat_ibiza_acfr"
        )
        if not ibiza.is_dir():
            self.skipTest("muestra del Ibiza no disponible")
        s = ConversionSession.from_source(ibiza, "35b84.pc", None, 70.0)
        res = {r.target: r for r in s.resolve()}
        self.assertEqual(float(res[CG].value), 0.5676)
        self.assertEqual(res[INERTIA].value, [1638.5, 1812.2, 370.3])
        self.assertEqual(res[CG].source, "decision")
        alt = {
            a["rule"]: float(a["value"]) for a in res[CG].alternatives if "rule" in a
        }
        self.assertAlmostEqual(
            alt["susp.cg_location_38022_driver@1"], 0.5676, delta=0.01
        )


if __name__ == "__main__":
    unittest.main()
