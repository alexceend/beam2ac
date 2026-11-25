"""G2.27 (paralelo): clones al 40340 de reglas del 200409 que resuelven con sus datos.

Lista cerrada en ``tools/catalog/clone_200409_rules_to_40340.py``. Cada clon es idéntico
a su regla de origen salvo la versión del exportador, el estado y un supuesto; no cambia
nada en 200409 ni en 38022.
"""

from __future__ import annotations

import json
import unittest
from functools import lru_cache
from pathlib import Path

from src.attrs.store import REPO_CATALOG, Catalog

ROOT = Path(__file__).resolve().parents[1]
RULES = ROOT / "catalog" / "rules"
HYACINTH = ROOT / "samples_beamng/thershow_hyacinth_sage/vehicles/hyacinth_sage"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
VERSION = "ir.formats.exporter_version"


@lru_cache(maxsize=None)
def resolved(vehicle_dir: Path):
    from src.attrs.report import resolve_vehicle
    from src.pipeline.physics_preview import load_vehicle_dir, preview_physics

    pc = (
        "35b84.pc" if vehicle_dir == IBIZA else sorted(vehicle_dir.glob("*.pc"))[0].name
    )
    pv = preview_physics(load_vehicle_dir(vehicle_dir, pc), 0.0)
    _, results = resolve_vehicle(pv, Catalog.load(REPO_CATALOG), "ac_physics", 0.0)
    return {r.target: r for r in results}


class TestClones(unittest.TestCase):
    def test_clones_differ_only_in_version_status_and_provenance(self):
        # el LSD 2WD (dt.diff_*) es nuevo y el embrague se clona del Ibiza: aparte
        clones = sorted(
            p
            for p in RULES.glob("*_40340*@1.json")
            if not p.name.startswith(("dt.diff_", "dt.clutch_max_torque_"))
            # G2.29: frenos con codo y aerodinámica cefaero cambian también condiciones
            and "_knee_40340" not in p.name and "_cefaero_40340" not in p.name
            # G2.30: trasera dwb_fitted (otra condición de estructura y supuestos) y
            # AWD de camsoEngine
            # (también cambian el tipo de motor/caja): tienen su propia prueba en
            # test_g230_*
            and "_fitted_40340" not in p.name
            and p.name
            not in ("dt.traction_awd_40340@1.json", "dt.awd_front_share_40340@1.json")
            and "_awd_manual_40340" not in p.name
            # bisexual_aspiration (2026-11-16): AWD con central bloqueado (40340 +
            # camsoEngine; ejes torqueVectoring): prueba propia en
            # test_bisexual_aspiration
            and "_lockc_40340" not in p.name
            and "_openmode_40340" not in p.name
            and "_openmode_lsdc_40340" not in p.name  # central lsd (2026-11-17)
            and "_viscous_40340"
            not in p.name  # AWD viscoso (agus, 2026-11-22): prueba propia
            and "_dct_40340"
            not in p.name  # DCT (vendeta_romaigna, 2026-11-22): la 200409 es dt.*_dct
            and "_viscc_40340"
            not in p.name  # ejes con central viscoso (autist_quantic, 2026-11-22)
        )
        self.assertGreaterEqual(len(clones), 60)
        for p in clones:
            c = json.loads(p.read_text("utf-8"))
            # la regla de origen vigente puede no ser la @1 (p. ej. damp_*@2)
            stem = p.name.replace("_40340", "_200409").rsplit("@", 1)[0]
            origins = list(RULES.glob(f"{stem}@*.json"))
            if (
                not origins
            ):  # regla 40340 sin gemela 200409 (p. ej. *_coupled, *_viscc): no es clon
                continue
            src = max(origins)
            s = json.loads(src.read_text("utf-8"))
            # G2.29: la aplicabilidad de app.tyres.* es de AC [C] y conserva su estado
            expected = s["status"] if c["id"].startswith("app.") else "provisional"
            self.assertEqual(c["status"], expected, p.name)
            self.assertEqual(c["target"], s["target"], p.name)
            if c["id"] != "dt.gearbox_inertia_40340":
                self.assertEqual(c["transform"], s["transform"], p.name)
                self.assertEqual(c.get("inputs"), s.get("inputs"), p.name)
            conds = c["conditions"] + c["scope"].get("conditions", [])
            self.assertEqual(
                [x["value"] for x in conds if x["fact"] == VERSION], [["40340"]], p.name
            )

    @unittest.skipUnless(HYACINTH.is_dir(), "sin samples_beamng")
    def test_40340_resolves_with_clones(self):
        r = resolved(HYACINTH)
        for target, rule in (
            ("ac:suspensions.ini/FRONT/TYPE", "susp.type_dwb_front_40340@1"),
            ("ac:car.ini/BASIC/TOTALMASS", "car.totalmass_40340@2"),
            (
                "ac:car.ini/EXPLICIT_INERTIA/INERTIA",
                "car.explicit_inertia_40340_nodriver@2",
            ),
            ("ac:drivetrain.ini/GEARBOX/INERTIA", "dt.gearbox_inertia_40340@1"),
        ):
            self.assertEqual(r[target].rule, rule, target)
            self.assertIsNotNone(r[target].value, target)
        # masa: cuerpo + bujes frente a la ficha de Automation (Weight 1428 kg)
        total = r["ac:car.ini/BASIC/TOTALMASS"].value + 2 * (
            r["ac:suspensions.ini/FRONT/HUB_MASS"].value
            + r["ac:suspensions.ini/REAR/HUB_MASS"].value
        )
        self.assertAlmostEqual(total, 1428, delta=15)
        # G2.31: neumático medido y ajustado con la referencia de giro libre (@2)
        self.assertIsNotNone(r["ac:tyres.ini/FRONT/DY_REF"].value)
        self.assertEqual(
            r["ac:tyres.ini/FRONT/DY_REF"].rule, "tyre.front.dy_ref_40340@2"
        )

    @unittest.skipUnless(HYACINTH.is_dir(), "sin samples_beamng")
    def test_lsd_2wd_is_twice_the_source(self):
        """B0 (G2.27): diferencia entre ruedas de AC = lim; BeamNG = 2·lsdTorque."""
        r = resolved(HYACINTH)
        sec = "ac:drivetrain.ini/DIFFERENTIAL/"
        # origen: lsdPreload 100, lsdLockCoef 0,25, lsdRevLockCoef 0,025
        for key, value in (("PRELOAD", 200.0), ("POWER", 0.5), ("COAST", 0.05)):
            self.assertEqual(r[sec + key].value, value, key)
            self.assertEqual(
                r[sec + key].rule, f"dt.diff_{key.lower()}_2wd_lsd_40340@1", key
            )
        rule = Catalog.load(REPO_CATALOG).rules["dt.diff_power_2wd_lsd_40340@1"]
        self.assertEqual(rule.status, "provisional")

    @unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
    def test_38022_diff_unchanged(self):
        r = resolved(IBIZA)
        sec = "ac:drivetrain.ini/DIFFERENTIAL/"
        # G2.39: decisión del usuario (D-G227-IBIZA): variante ib2, 200 / 0,5 / 0,05
        self.assertEqual(r[sec + "PRELOAD"].value, 200.0)
        self.assertEqual(r[sec + "POWER"].value, 0.5)
        self.assertEqual(r[sec + "COAST"].value, 0.05)

    @unittest.skipUnless(IBIZA.is_dir(), "sin sample_beamng")
    def test_38022_untouched(self):
        r = resolved(IBIZA)
        self.assertFalse([t for t, x in r.items() if x.rule and "_40340" in x.rule])
        # G2.39: el 38022 recibe su propia regla (decisión D-INV-GBI)
        self.assertEqual(
            r["ac:drivetrain.ini/GEARBOX/INERTIA"].rule, "dt.gearbox_inertia_38022@1"
        )


if __name__ == "__main__":
    unittest.main()
