"""G2.44: eje rígido trasero (Atacama) → ``TYPE=AXLE`` de AC por regla."""

import unittest

from src.attrs import transforms
from tests.test_g211_transmissions import _real, _session, have

S = "ac:suspensions.ini/"
ATACAMA = "2020_rpm_atacama_sport__4x4"


class TestRigidAxle(unittest.TestCase):
    def test_rear_hub_is_axle_counts_the_hub_once(self) -> None:
        ins = {
            "total": (2000.0, "kg"),
            "front_fraction": (0.5, "1"),
            "hub_front": (50.0, "kg"),
            "hub_rear": (200.0, "kg"),
        }
        per_wheel = transforms.apply("ac_body_cg", ins, {"output": "cg_location"}, "1")
        axle = transforms.apply(
            "ac_body_cg", ins, {"output": "cg_location", "rear_hub_is_axle": True}, "1"
        )
        # eje entero de 200 kg == 100 kg por rueda (mismo cuerpo que 2 ruedas de 100)
        ins2 = dict(ins, hub_rear=(100.0, "kg"))
        self.assertAlmostEqual(
            axle, transforms.apply("ac_body_cg", ins2, {"output": "cg_location"}, "1")
        )
        self.assertNotAlmostEqual(axle, per_wheel)

    def test_atacama_axle_ir_and_resolution(self) -> None:
        if not have(ATACAMA):
            self.skipTest("muestras 200409 no disponibles")
        _, ir = _real(ATACAMA)
        rear = ir.suspension.axles["rear"]
        self.assertEqual(rear.linkage.value, "rigid_axle")
        self.assertEqual(rear.link_count.value, 5)
        self.assertAlmostEqual(rear.attach_rel_pos.value, 1.0, places=3)
        for i in range(5):
            self.assertTrue(rear.anchors[f"J{i}_CAR"].present)
        by = {r.target: r for r in _session(ir).results}
        self.assertEqual(by[S + "REAR/TYPE"].value, "AXLE")
        self.assertEqual(by[S + "AXLE/LINK_COUNT"].value, 5)
        self.assertEqual(by[S + "HEADER/VERSION"].value, 4)
        hub = by[S + "REAR/HUB_MASS"]
        self.assertEqual(hub.rule, "susp.hub_mass_rear_axle@1")
        self.assertAlmostEqual(hub.value, 2 * rear.unsprung_mass_per_wheel.value)
        # AC cuenta el HUB_MASS del AXLE una vez: masa AC = TOTALMASS + 2·HUB_del +
        # HUB_eje = masa BeamNG
        tm = by["ac:car.ini/BASIC/TOTALMASS"]
        self.assertEqual(tm.rule, "car.totalmass_axle@1")
        hub_f = by[S + "FRONT/HUB_MASS"].value
        self.assertAlmostEqual(
            tm.value + 2 * hub_f + hub.value,
            ir.mass.total.value + ir.mass.driver.value,
            places=6,
        )
        for k in ("WBCAR_BOTTOM_FRONT", "STRUT_CAR", "WBTYRE_TOP", "WBCAR_STEER"):
            self.assertEqual(by[S + f"REAR/{k}"].status, "not_applicable", k)

    def test_independent_axles_keep_their_rules(self) -> None:
        for name in ("2020_rpm_atlanta_gt_3_6t", "2020_rpm_legion_grand_touring"):
            if not have(name):
                self.skipTest("muestras 200409 no disponibles")
            _, ir = _real(name)
            by = {r.target: r for r in _session(ir).results}
            with self.subTest(name):
                self.assertEqual(
                    by[S + "REAR/HUB_MASS"].rule, "susp.hub_mass_rear_200409@2"
                )
                self.assertEqual(
                    by["ac:car.ini/BASIC/TOTALMASS"].rule, "car.totalmass_200409@2"
                )
                self.assertEqual(by[S + "AXLE/J0_CAR"].status, "not_applicable")
                self.assertEqual(by[S + "REAR/TYPE"].value in ("DWB", "STRUT"), True)


if __name__ == "__main__":
    unittest.main()


class TestAxleValidator(unittest.TestCase):
    GOOD = (
        "[HEADER]\nVERSION=4\n[REAR]\nTYPE=AXLE\n[AXLE]\n"
        "LINK_COUNT=3\nATTACH_REL_POS=1.0\n"
        + "".join(f"J{i}_CAR=0.1,0.2,0.3\nJ{i}_AXLE=0.1,0.2,0.4\n" for i in range(3))
    )

    def _check(self, text: str):
        import tempfile
        from pathlib import Path

        from src.ac.car_validate import axle_problem

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "suspensions.ini").write_text(text, "utf-8")
            return axle_problem(d)

    def test_valid_and_defective_axle_packages(self) -> None:
        self.assertTrue(self._check(self.GOOD)[1])
        for bad in (
            self.GOOD.replace("VERSION=4", "VERSION=3"),
            self.GOOD.replace("LINK_COUNT=3", "LINK_COUNT=2"),
            self.GOOD.replace("ATTACH_REL_POS=1.0\n", ""),
            self.GOOD.replace("J2_AXLE=0.1,0.2,0.4\n", ""),
        ):
            self.assertFalse(self._check(bad)[1], bad)
        self.assertIsNone(self._check("[REAR]\nTYPE=DWB\n"))


class TestThinning(unittest.TestCase):
    def test_dependent_kept_and_sixth_independent_dropped(self) -> None:
        from types import SimpleNamespace as N

        from src.extract.rigid_axle import _thin_to_five_dof

        axle = {
            "a%d" % i: N(pos=p)
            for i, p in enumerate(
                [
                    (0.5, 0, 0),
                    (-0.5, 0, 0),
                    (0, 0.5, 0),
                    (0, -0.5, 0),
                    (0, 0, 0.5),
                    (0.3, 0.3, 0.3),
                ]
            )
        }
        far = {
            f"c{i}": N(pos=p)
            for i, p in enumerate(
                [(1, 1, 1), (-1, 2, 1), (2, -1, 3), (-2, -1, 1), (1, 3, -2), (3, 1, 2)]
            )
        }
        nodes = {**axle, **far}
        links = [(f"c{i}", f"a{i}") for i in range(6)]
        kept = _thin_to_five_dof(nodes, links, (0.0, 0.0, 0.0))
        self.assertEqual(len(kept), 5)
        self.assertEqual(kept, links[:5])
        self.assertEqual(_thin_to_five_dof(nodes, [], (0.0, 0.0, 0.0)), [])
