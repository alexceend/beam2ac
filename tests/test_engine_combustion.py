"""Adaptador combustionEngine (solo lectura) y fusión de secciones como el juego."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from src.bench.service import default_beamng_root
from src.extract.engine_combustion import (
    HP_W,
    CombustionEngineError,
    _include_target,
    _table,
    catmull_rom_spline,
    create_curve,
    decode_csv,
    zip_resource_reader,
)
from src.jbeam.resolver import ResolvedConfig
from src.jbeam.unify import _unify, unify_dict_section

BNG = Path((default_beamng_root() / "content/vehicles").as_posix())
PESSIMA = BNG / "pessima.zip"


def _active(name: str, parent: str | None, data: dict) -> SimpleNamespace:
    # unify_dict_section solo usa name, parent y part.data
    return SimpleNamespace(
        name=name, parent=parent, part=SimpleNamespace(name=name, data=data)
    )


class TestCreateCurve(unittest.TestCase):
    def test_two_points_linear(self) -> None:
        c = create_curve([(0, 0.0), (10, 100.0)])
        self.assertEqual(sorted(c), list(range(11)))
        self.assertAlmostEqual(c[5], 50.0)
        self.assertAlmostEqual(c[10], 100.0)

    def test_catmull_rom_passes_through_points(self) -> None:
        pts = [(0.0, 0.0), (10.0, 50.0), (20.0, 80.0), (30.0, 60.0)]
        c = create_curve(pts)
        for x, y in pts:
            self.assertAlmostEqual(c[int(x)], y, places=9)

    def test_catmull_rom_is_not_linear(self) -> None:
        # entre puntos no alineados la spline se separa de la interpolación lineal
        c = catmull_rom_spline([(0.0, 0.0), (10.0, 50.0), (20.0, 80.0), (30.0, 60.0)])
        self.assertNotAlmostEqual(c[15], 65.0, places=3)

    def test_collinear_points_stay_linear(self) -> None:
        c = catmull_rom_spline([(0.0, 0.0), (10.0, 10.0), (20.0, 20.0), (30.0, 30.0)])
        # tramo interior: lineal
        for x in range(10, 21):
            self.assertAlmostEqual(c[x], float(x), places=9)
        # extremos: ve_utils.lua repite el punto extremo (p0 = p1), así que el primer
        # tramo
        # se curva aunque los datos sean colineales (comportamiento del juego, no un
        # error)
        self.assertAlmostEqual(c[1], 0.595, places=9)

    def test_needs_two_points(self) -> None:
        with self.assertRaises(CombustionEngineError):
            create_curve([(0.0, 1.0)])


class TestCsvInclude(unittest.TestCase):
    """Port de csvlib.decode e include('…csv') (expressionParser.lua:29)."""

    def test_decode_like_csvlib(self) -> None:
        text = '"rpm","torque"\r\n0,0\r\n500,170.5\r\n  -1 , "a""b"\r\n\r\n'
        self.assertEqual(
            decode_csv(text),
            [["rpm", "torque"], [0, 0], [500, 170.5], [-1, 'a"b']],
        )

    def test_decode_trailing_empty_fields_and_text(self) -> None:
        self.assertEqual(decode_csv("a,1,,\nx"), [["a", 1], ["x"]])

    def test_include_target(self) -> None:
        self.assertEqual(_include_target("$=include('v/a.csv')"), "v/a.csv")
        self.assertEqual(_include_target('$= include("v/a.csv") '), "v/a.csv")
        self.assertIsNone(_include_target("$=include('v/a.xlsx', 'Hoja', 'A1')"))
        self.assertIsNone(_include_target("$=round(include('a.csv'))"))
        self.assertIsNone(_include_target("include('a.csv')"))

    def test_table_from_zip_without_extracting(self) -> None:
        import tempfile
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            zp = Path(tmp) / "x.zip"
            with zipfile.ZipFile(zp, "w") as zf:
                zf.writestr(
                    "vehicles/common/c.csv", '"rpm","torque"\r\n0,0\r\n1000,100'
                )
            before = zp.read_bytes()
            reader = zip_resource_reader(zp)
            pts = _table("$=include('vehicles/common/c.csv')", {}, "torque", reader)
            self.assertEqual(pts, [(0.0, 0.0), (1000.0, 100.0)])
            self.assertEqual(zp.read_bytes(), before)
            with self.assertRaises(CombustionEngineError):
                _table("$=include('vehicles/common/no.csv')", {}, "torque", reader)
            with self.assertRaises(CombustionEngineError):  # sin lector: no se supone
                _table("$=include('vehicles/common/c.csv')", {}, "torque")
            with self.assertRaises(CombustionEngineError):
                _table("$=include('vehicles/common/c.xlsx')", {}, "torque", reader)


class TestUnify(unittest.TestCase):
    def test_child_overwrites_and_modifiers(self) -> None:
        target = {"a": 1, "b": 10, "c": 4, "d": 5, "e": 5}
        trace: dict[str, list[str]] = {}
        _unify(target, {"a": 2, "$+b": 5, "$*c": 2, "$<d": 3, "$>e": 9}, "hija", trace)
        self.assertEqual(target, {"a": 2, "b": 15, "c": 8, "d": 3, "e": 9})
        self.assertIn("sobrescribe", trace["a"][0])

    def test_modifier_without_base_is_kept(self) -> None:
        target: dict = {}
        _unify(target, {"$+x": 3}, "hija", {})
        self.assertEqual(target, {"$+x": 3})
        # un antepasado con valor base lo aplica después
        parent = {"x": 10}
        _unify(parent, target, "hija", {})
        self.assertEqual(parent["x"], 13)

    def test_modifier_over_modifier_combines(self) -> None:
        target = {"$+x": 3}
        _unify(target, {"$+x": 4}, "nieta", {})
        self.assertEqual(target, {"$+x": 7})

    def test_non_numeric_modifier_is_stored(self) -> None:
        target = {"x": 1}
        _unify(target, {"$+x": "$=1+1"}, "hija", {})
        self.assertEqual(target, {"x": 1, "$+x": "$=1+1"})

    def test_post_order_journal(self) -> None:
        # nieta se fusiona en hija antes de que hija se fusione en la raíz
        resolved = ResolvedConfig(
            root="r",
            active=[
                _active("r", None, {"eng": {"v": 100}}),
                _active("h", "r", {"eng": {"$*v": 2}}),
                _active("n", "h", {"eng": {"$*v": 3}}),
            ],
        )
        u = unify_dict_section(resolved, "eng")
        # $*v de la nieta se combina con el de la hija (2·3) y luego se aplica: 100·6
        self.assertEqual(u.values, {"v": 600})
        self.assertEqual(u.parts, ["r", "h", "n"])

    def test_sibling_order(self) -> None:
        resolved = ResolvedConfig(
            root="r",
            active=[
                _active("r", None, {"eng": {"v": 1}}),
                _active("a", "r", {"eng": {"v": 2}}),
                _active("b", "r", {"eng": {"v": 3}}),
            ],
        )
        self.assertEqual(unify_dict_section(resolved, "eng").values["v"], 3)

    def test_single_root_required(self) -> None:
        resolved = ResolvedConfig(
            root="r", active=[_active("a", None, {}), _active("b", None, {})]
        )
        with self.assertRaises(ValueError):
            unify_dict_section(resolved, "eng")


@unittest.skipUnless(PESSIMA.is_file(), "BeamNG (pessima.zip) no instalado")
class TestPessimaRegression(unittest.TestCase):
    """Regresión del adaptador: pessima DX_M (info: 124 hp, 170 N·m)."""

    @classmethod
    def setUpClass(cls) -> None:
        from src.extract.engine_combustion import read_combustion_engine
        from src.jbeam import resolve
        from src.pipeline.physics_preview import load_vehicle_zip

        cls.src = load_vehicle_zip(PESSIMA, BNG / "common.zip", "DX_M.pc")
        cls.e = read_combustion_engine(resolve(cls.src.index, cls.src.pc))

    def test_values(self) -> None:
        e = self.e
        self.assertEqual(e.device_name, "mainEngine")
        self.assertEqual(
            (e.idle_rpm, e.max_rpm, e.max_rpm_jbeam), (900.0, 6300.0, 6500.0)
        )
        self.assertEqual(e.rev_limiter_type, "timeBased")
        self.assertEqual(e.forced_induction, [])
        self.assertTrue(e.supported)
        self.assertEqual(e.peak_torque[0], 4575)
        self.assertAlmostEqual(e.peak_torque[1], 165.8727, places=3)
        self.assertAlmostEqual(e.peak_power[1] / HP_W, 124.1566, places=3)

    def test_power_matches_info(self) -> None:
        self.assertAlmostEqual(
            self.e.peak_power[1] / HP_W, self.src.info["Power"], delta=0.5
        )

    def test_torque_gap_is_documented(self) -> None:
        # 165,9 frente a 170: hueco abierto (combustion_engine.md); el test lo fija para
        # que
        # un cambio no lo cierre ni lo agrave en silencio.
        self.assertAlmostEqual(
            self.src.info["Torque"] - self.e.peak_torque[1], 4.13, delta=0.05
        )


if __name__ == "__main__":
    unittest.main()
