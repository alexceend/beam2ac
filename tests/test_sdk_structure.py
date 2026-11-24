"""Regresión de reglas estructurales confirmadas del SDK de Kunos.

Requisitos de ``docs/research/sdk_requisitos_matriz.md`` con confianza alta y sin
contradicción que afecte al conversor, que no tenían un test que los fijara:

* R05 — ``SUSP_xx`` en la misma posición que ``WHEEL_xx`` (Rev2.0 pág. 59).
  El plan da a ambos la misma matriz; se exige igualdad exacta.
* R02/R03 — ruedas tocando Y = 0 de AC y caja centrada en X/Z de AC (Rev2.0
  pág. 17), comprobado sin Blender sobre el plan ensamblado de la muestra real.
  La comprobación sobre el KN5 real sigue en la prueba opcional con Blender
  (``tests/test_assemble.py``, ``TestSampleAssemblyWithBlender``).

Ejes del plan: BeamNG (x, y, z = arriba). AC = (Bx, Bz, −By), así que Y de AC es
z de BeamNG y X/Z de AC son x/−y de BeamNG.

Fuera de alcance a propósito (fuentes contradictorias): ``DISC_xx``, ``RIM_xx``,
cristales, materiales y formatos de textura.

Solo lectura: no escribe nada.
"""

import unittest

from src.mesh.build import plan_for_vehicle
from src.mesh.plan import (
    add_cockpit_lr_nulls,
    add_steer_hr_diagnostic_nulls,
    kunos_origin_offset,
    translate_plan,
)
from src.safe_paths import PROJECT_ROOT

VEHICLE = PROJECT_ROOT / "sample_beamng" / "vehicles" / "seat_ibiza_acfr"
PC = VEHICLE / "35b84.pc"
TOL = 1e-9  # m: solo traslaciones exactas, sin aritmética acumulada relevante


def assembled(plan):
    """Las mismas transformaciones de plan que aplica ``src/ac/assemble.py``."""
    offset = kunos_origin_offset(plan)
    return (
        add_steer_hr_diagnostic_nulls(
            add_cockpit_lr_nulls(translate_plan(plan, offset))
        ),
        offset,
    )


def wheel_and_susp(plan):
    """``{sufijo: (WHEEL_xx, SUSP_xx | None)}`` para cada ``WHEEL_xx`` del plan."""
    empties = {e.name: e for e in plan.empties}
    out = {}
    for name, e in empties.items():
        if name.startswith("WHEEL_"):
            suffix = name[len("WHEEL_") :]
            out[suffix] = (e, empties.get(f"SUSP_{suffix}"))
    return out


class TestSuspMatchesWheel(unittest.TestCase):
    """R05 (Rev2.0 pág. 59; 1.03 pág. 10)."""

    @classmethod
    def setUpClass(cls):
        cls.plan = plan_for_vehicle(VEHICLE, PC)
        cls.assembled, _ = assembled(cls.plan)

    def _check(self, plan):
        pairs = wheel_and_susp(plan)
        # Todos los lados para los que hay datos: la muestra tiene 4 ruedas.
        self.assertEqual(sorted(pairs), ["LF", "LR", "RF", "RR"])
        susp_names = {e.name for e in plan.empties if e.name.startswith("SUSP_")}
        self.assertEqual(susp_names, {f"SUSP_{s}" for s in pairs}, "SUSP_ sin WHEEL_")
        for suffix, (wheel, susp) in pairs.items():
            with self.subTest(side=suffix):
                self.assertIsNotNone(susp, f"falta SUSP_{suffix}")
                self.assertEqual(susp.wheel, wheel.wheel)
                self.assertEqual(susp.matrix, wheel.matrix)

    def test_plan(self):
        self._check(self.plan)

    def test_after_assembly_transforms(self):
        self._check(self.assembled)


class TestAssembledOrigin(unittest.TestCase):
    """R02/R03 (Rev2.0 pág. 17) sobre el plan ensamblado de la muestra real."""

    @classmethod
    def setUpClass(cls):
        cls.plan = plan_for_vehicle(VEHICLE, PC)
        cls.moved, cls.offset = assembled(cls.plan)

    def test_wheels_touch_ac_ground(self):
        wheel_objects = [
            o for o in self.moved.objects if (o.parent or "").startswith("WHEEL_")
        ]
        self.assertTrue(wheel_objects)
        ground = min(o.bbox_beamng[0][2] for o in wheel_objects)  # z BeamNG = Y AC
        self.assertAlmostEqual(ground, 0.0, delta=TOL)
        # Ninguna malla de rueda queda por debajo del suelo.
        self.assertTrue(all(o.bbox_beamng[0][2] >= -TOL for o in wheel_objects))

    def test_bounding_box_centred_in_ac_x_and_z(self):
        lo = [min(o.bbox_beamng[0][i] for o in self.moved.objects) for i in range(3)]
        hi = [max(o.bbox_beamng[1][i] for o in self.moved.objects) for i in range(3)]
        self.assertAlmostEqual((lo[0] + hi[0]) / 2, 0.0, delta=TOL)  # X AC
        self.assertAlmostEqual((lo[1] + hi[1]) / 2, 0.0, delta=TOL)  # −Z AC

    def test_empties_move_with_meshes(self):
        before = {e.name: e.matrix for e in self.plan.empties}
        for e in self.moved.empties:
            if e.name not in before:
                continue  # nulos de cabina añadidos en el origen tras trasladar
            with self.subTest(empty=e.name):
                for i in range(3):
                    self.assertAlmostEqual(
                        e.matrix[i][3] - before[e.name][i][3], self.offset[i], delta=TOL
                    )
                    self.assertEqual(e.matrix[i][:3], before[e.name][i][:3])
        objs = {o.name: o for o in self.plan.objects}
        for o in self.moved.objects:
            for k in (0, 1):
                for i in range(3):
                    self.assertAlmostEqual(
                        o.bbox_beamng[k][i] - objs[o.name].bbox_beamng[k][i],
                        self.offset[i],
                        delta=TOL,
                    )


if __name__ == "__main__":
    unittest.main()
