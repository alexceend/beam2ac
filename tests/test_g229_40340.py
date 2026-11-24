"""G2.29: exportador 40340 (frenos con codo, cefaero, brazo semitirado, masa medida)."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from src.attrs.engine import ResolutionEngine
from src.extract.aero import Aero, AeroDataError, DownforcePoint, axle_equivalent

SAMPLES = Path(__file__).parents[1] / "samples_beamng"


def session(car: str):
    from src.convert.service import ConversionSession

    vd = SAMPLES / f"thershow_{car}" / "vehicles" / car
    s = ConversionSession.from_source(vd, None, None, 0.0)
    return s, {r.target: r for r in s.resolve()}


class TestMeasuredBeatsDeclared(unittest.TestCase):
    def vals(self, *items):
        ctx = NS(
            vehicle="v",
            variant="x",
            validations=[
                NS(id=i, vehicle="v", variant="x", covers=["f"], method=m, result=r)
                for i, (m, r) in enumerate(items)
            ],
        )
        return [v.id for v in ResolutionEngine._vehicle_validations(NS(ctx=ctx), "f")]

    def test_failed_declared_value_is_dropped_only_with_a_passed_measurement(self):
        self.assertEqual(self.vals(("info_reference", "fail")), [0])
        self.assertEqual(
            self.vals(("info_reference", "fail"), ("in_game_value", "pass")), [1]
        )
        # una medida fallida no tapa nada, y una medida fallida nunca se descarta
        self.assertEqual(
            self.vals(("info_reference", "fail"), ("in_game_value", "fail")), [0, 1]
        )


class TestCefaeroAxles(unittest.TestCase):
    def aero(self, **kw):
        base = dict(
            cda=0.8,
            drag_mod_lateral=1.25,
            drag_mod_vertical=1.5,
            drag_node_ac=(0.0, 0.5, 0.0),
            target_front_n=0.0,
            target_rear_n=0.0,
            target_speed_kmh=200.0,
            target_air_density_unused=None,
            cla_front=0.3,
            cla_rear=0.5,
            constant_front_n=0.0,
            constant_rear_n=0.0,
            points=[
                DownforcePoint(True, "a", "b", (0.0, 1.0, 1.5)),
                DownforcePoint(False, "c", "d", (0.0, 1.0, -1.0)),
            ],
        )
        base.update(kw)
        return Aero(**base)

    GEO = NS(
        wheelbase=2.5,
        axles=[
            NS(longitudinal_ac=1.5, wheels=("FL",)),
            NS(longitudinal_ac=-1.0, wheels=("RL",)),
        ],
        wheels=[
            NS(name="FL", center_ac=(0.7, 0.3, 1.5)),
            NS(name="RL", center_ac=(0.7, 0.3, -1.0)),
        ],
    )

    def test_same_resultant_and_pitch_moment(self):
        cda, f, r = axle_equivalent(self.aero(), self.GEO)
        self.assertEqual(cda, 0.8)
        self.assertAlmostEqual(f + r, 0.8)  # carga total
        # el arrastre, 0,2 m por encima del eje, descarga el eje delantero
        self.assertAlmostEqual(f, 0.3 - 0.2 * 0.8 / 2.5)

    def test_constant_force_is_refused(self):
        with self.assertRaises(AeroDataError):
            axle_equivalent(self.aero(constant_front_n=10.0), self.GEO)


@unittest.skipUnless(
    (SAMPLES / "thershow_sucette_poubelle").is_dir(), "sin samples_beamng"
)
class TestVehicles(unittest.TestCase):
    def test_sucette_resolves_with_measured_structure(self):
        s, res = session("sucette_poubelle")
        f = s.facts()
        self.assertEqual(f["ir.suspension.axles.rear.linkage"].value, "dwb_equivalent")
        rules = {
            "ac:brakes.ini/DATA/MAX_TORQUE": "brk.max_torque_knee_40340@1",
            "ac:aero.ini/WING_0/CD_GAIN": "aero.wing0_cd_gain_cefaero_40340@1",
            "ac:suspensions.ini/REAR/TYPE": "susp.type_dwb_rear_40340@1",
            "ac:tyres.ini/FRONT/DY_REF": "tyre.front.dy_ref_40340@2",
            "ac:car.ini/BASIC/TOTALMASS": "car.totalmass_40340@2",
        }
        for target, rule in rules.items():
            self.assertEqual(res[target].rule, rule, target)
            self.assertNotIn(
                res[target].status, ("ambiguous", "manual_required"), target
            )
        from src.app import diagnostics

        vd = SAMPLES / "thershow_sucette_poubelle"
        self.assertTrue(diagnostics.inspect_vehicle(str(vd))["exportable_today"])

    def test_overconstrained_rear_is_fitted(self):
        """G2.30: la trasera de rango 6 se resuelve por la regla del DWB ajustado."""
        s, res = session("hyacinth_sage")
        self.assertEqual(
            s.facts()["ir.suspension.axles.rear.linkage"].value, "dwb_fitted"
        )
        self.assertEqual(res["ac:suspensions.ini/REAR/TYPE"].value, "DWB")
        self.assertEqual(
            res["ac:suspensions.ini/REAR/SPRING_RATE"].rule,
            "susp.spring_rate_rear_fitted_40340@1",
        )
        self.assertEqual(
            res["ac:brakes.ini/DATA/FRONT_SHARE"].rule, "brk.front_share_knee_40340@1"
        )


if __name__ == "__main__":
    unittest.main()
