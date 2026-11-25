"""Procedencia de Automation y familia ``automation_export`` (2026-11-02).

Decisiones D-P1…D-P4 de ``docs/research/procedencia_automation_2026-11-01.md`` §5.4;
pruebas de su §7. Cada clase dice si usa exportaciones REALES del corpus o casos
SINTÉTICOS (hechos construidos a mano o una copia reducida de una muestra real).
Las pruebas con muestras o instalaciones ausentes se omiten.
"""

from __future__ import annotations

import pytest

import functools
import types
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.bench.service import default_beamng_root
from src.attrs import schema as S
from src.attrs.engine import ResolutionEngine, VehicleContext, compute_families
from src.attrs.facts import Fact
from src.attrs.report import resolve_vehicle
from src.attrs.store import REPO_CATALOG, Catalog
from src.catalog import ac_refs, pairing
from src.importers import automation
from src.importers.beamng import import_preview
from src.jbeam.lax_json import loads
from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/catalog"))

FAMILY = "automation_export"
TRACTION = "ac:drivetrain.ini/TRACTION/TYPE"
BNG = Path((default_beamng_root() / "content/vehicles").as_posix())
COMMON = BNG / "common.zip"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
IBIZA = ROOT / "sample_beamng/vehicles/seat_ibiza_acfr"
SAMPLES = ROOT / "samples_beamng"
NEW_200409 = {
    "2020_rpm_atacama_sport__4x4",
    "2020_rpm_atlanta_gt_3_6t",
    "2020_rpm_legion_grand_touring",
    "2020_rpm_patagonia_xt_v8",
    "2020_rpm_prothean_s",
    "2021_rpm_portland_touring",
    "rpm_omega_one_concept",
}
V40340 = {
    "agus_1_nrt_1",
    "eagle_inca_p395_dale_earnhardt_edition",
    "turbot_aster",
    "autist_cuanto",
    "hyacinth_sage",
    "quail_mirabilis",
    "sucette_poubelle",
    "sucette_poubelle_turbo",
}
MOD_ZIPS = {  # zip -> id del vehículo (exportador 38022)
    "seat_ibiza_acfr": "seat_ibiza_acfr",
    "porsche_gt3_rs": "porsche_gt3_rs",
    "spicy_mandala": "spicy_mandala",
    "rune_susui_espol_vtec": "rune_susui",
    "composed_fox": "composed_fox",
}
OLD_MEMBERSHIP = [  # hasta 2026-11-02 (G2.5b)
    ("ir.identity.source_tool", "eq", "Automation"),
    ("ir.formats.code_versions.camsoEngine", "exists", None),
]
NEW_MEMBERSHIP = [
    ("ir.identity.source_tool", "eq", "Automation"),
    ("ir.identity.author", "eq", "Camshaft Software LTD"),
    ("ir.formats.exporter_version", "in", ["38022", "40340", "200409"]),
]
PROTECTING = ("ir.formats.exporter_version", "ir.engine.device_type")


@functools.lru_cache(maxsize=1)
def catalog() -> Catalog:
    return Catalog.load(REPO_CATALOG)


def family_with(membership) -> S.Family:
    return S.Family(
        id=FAMILY,
        description="x",
        membership=[S.Condition(fact=f, op=o, value=v) for f, o, v in membership],
    )


def members(fam: S.Family, facts: dict[str, Fact]) -> bool:
    # compute_families solo lee ``families``: no hace falta recargar el catálogo (1,3 s)
    cat = types.SimpleNamespace(families={fam.id: fam})
    ctx = VehicleContext("v", "", facts)
    compute_families(cat, ctx)
    return fam.id in ctx.families


def facts_from_info(info: dict, camso: str | None = None) -> dict[str, Fact]:
    """Hechos de identidad que el importador saca de ``info.json`` (sintético)."""
    out: dict[str, Fact] = {}
    for key, fid in (
        ("Type", "ir.identity.source_tool"),
        ("Author", "ir.identity.author"),
    ):
        if key in info:
            out[fid] = Fact(fid, info[key], "text", "extracted")
    if "ExporterVersion" in info:
        fid = "ir.formats.exporter_version"
        out[fid] = Fact(fid, str(info["ExporterVersion"]), "text", "extracted")
    if camso is not None:
        fid = "ir.formats.code_versions.camsoEngine"
        out[fid] = Fact(fid, camso, "text", "extracted")
    return out


# --- corpus real ---------------------------------------------------------------------


def corpus_sources() -> list[tuple[str, str, object]]:
    """(etiqueta, versión esperada, cargador) de las exportaciones REALES presentes."""
    out: list[tuple[str, str, object]] = []
    if IBIZA.is_dir():
        out.append(
            (
                "dir/seat_ibiza_acfr",
                "38022",
                lambda: load_vehicle_dir(IBIZA, "35b84.pc"),
            )
        )
    if SAMPLES.is_dir():
        for top in sorted(SAMPLES.iterdir()):
            for vd in sorted((top / "vehicles").iterdir()):
                pc = sorted(p.name for p in vd.glob("*.pc"))[0]
                ver = (
                    "40340"
                    if vd.name in V40340
                    else "200409" if vd.name in NEW_200409 else "?"
                )
                out.append(
                    (
                        f"dir/{vd.name}",
                        ver,
                        lambda vd=vd, pc=pc: load_vehicle_dir(vd, pc),
                    )
                )
    if COMMON.is_file():
        for z in MOD_ZIPS:
            zp = MODS / f"{z}.zip"
            if zp.is_file():
                pc = "35b84.pc" if z == "seat_ibiza_acfr" else None
                out.append(
                    (
                        f"zip/{z}",
                        "38022",
                        lambda zp=zp, pc=pc: load_vehicle_zip(zp, COMMON, pc),
                    )
                )
    return out


CONTROLS = (
    ("etk800_854_190_M", "etk800", "854_190_M.pc"),
    ("pessima_DX_M", "pessima", "DX_M.pc"),
)


@functools.lru_cache(maxsize=None)
def resolved(label: str):
    """(fuente, contexto, resultados) de una fuente real, con el catálogo del repo."""
    for lab, _, load in corpus_sources():
        if lab == label:
            src = load()
            break
    else:
        name, z, pc = next(c for c in CONTROLS if f"control/{c[0]}" == label)
        src = load_vehicle_zip(BNG / f"{z}.zip", COMMON, pc)
    pv = preview_physics(src, 70.0)
    ctx, res = resolve_vehicle(pv, catalog())
    return src, pv, ctx, {r.target: r for r in res}


def need_corpus(test):
    return unittest.skipUnless(
        IBIZA.is_dir() and SAMPLES.is_dir(), "sin las muestras del corpus"
    )(test)


def need_bng(test):
    return unittest.skipUnless(COMMON.is_file(), "sin la instalación de BeamNG")(test)


# 1 + 6 (sintético) -------------------------------------------------------------------


G227_CAR_BASICS = {
    "car.fuel_200409@1",
    "car.max_fuel_200409@1",
    "car.screen_name_200409@1",
    "ctrl.ffmult_200409@1",
}


class TestMembershipUnit(unittest.TestCase):
    """SINTÉTICO: combinaciones de los campos de ``info.json`` y de ``camsoEngine``."""

    def test_catalog_membership_is_the_provenance_criterion(self) -> None:
        fam = catalog().families[FAMILY]
        self.assertEqual(
            [(c.fact, c.op, c.value) for c in fam.membership], NEW_MEMBERSHIP
        )
        # MISMO criterio que el importador (D-P1): una sola fuente de valores
        self.assertEqual(automation.TYPE, NEW_MEMBERSHIP[0][2])
        self.assertEqual(automation.AUTHOR, NEW_MEMBERSHIP[1][2])
        self.assertEqual(list(automation.KNOWN_EXPORTER_VERSIONS), NEW_MEMBERSHIP[2][2])
        # sin condición sobre el motor (la procedencia no depende del adaptador)
        self.assertFalse(any("camso" in c.fact.lower() for c in fam.membership))
        self.assertFalse(any("engine" in c.fact for c in fam.membership))

    def test_previous_membership_recorded_in_evidence(self) -> None:
        fam = catalog().families[FAMILY]
        self.assertIn(
            "ir.identity.source_tool eq 'Automation'; "
            "ir.formats.code_versions.camsoEngine exists None",
            fam.evidence[-1].note,
        )
        # la evidencia anterior (G2.5b) se conserva
        self.assertIn("bng.info.type eq 'Automation'", fam.evidence[3].note)

    def test_combinations(self) -> None:
        fam = catalog().families[FAMILY]
        for tool in ("Automation", "Car", None):
            for author in ("Camshaft Software LTD", "BeamNG", None):
                for ver in ("38022", "40340", "200409", 38022, "999999", None):
                    for camso in ("47437d5c", None):
                        info = {
                            k: v
                            for k, v in (
                                ("Type", tool),
                                ("Author", author),
                                ("ExporterVersion", ver),
                            )
                            if v is not None
                        }
                        expected = (
                            tool == "Automation"
                            and author == "Camshaft Software LTD"
                            and str(ver) in ("38022", "40340", "200409")
                        )
                        with self.subTest(info=info, camso=camso):
                            got = members(fam, facts_from_info(info, camso))
                            self.assertEqual(got, expected)
                            self.assertEqual(
                                automation.is_automation_info(info), expected
                            )

    def test_camso_without_provenance_is_not_member(self) -> None:
        fam = catalog().families[FAMILY]
        self.assertFalse(members(fam, facts_from_info({"Type": "Car"}, "47437d5c")))

    def test_provenance_without_camso_is_member(self) -> None:
        fam = catalog().families[FAMILY]
        info = {
            "Type": "Automation",
            "Author": "Camshaft Software LTD",
            "ExporterVersion": 200409,
        }
        self.assertTrue(members(fam, facts_from_info(info)))

    def test_unknown_exporter_version(self) -> None:
        """Prueba 6 adaptada a D-P1 («ExporterVersion reconocido»): una versión que no
        está en la lista cerrada NO es miembro, y el motivo lo dice."""
        info = {
            "Type": "Automation",
            "Author": "Camshaft Software LTD",
            "ExporterVersion": "999999",
        }
        prov = automation.provenance_of_info(info)
        self.assertFalse(prov.is_automation)
        self.assertEqual(prov.exporter_version, "999999")
        self.assertTrue(any("999999" in r for r in prov.failed), prov.failed)
        self.assertFalse(members(catalog().families[FAMILY], facts_from_info(info)))

    def test_exporter_version_is_opaque(self) -> None:
        """Identificador opaco: nada de rangos ni conversiones numéricas."""
        for ver in ("040340", "40340.0", " 40340", "50000", "38023"):
            with self.subTest(ver=ver):
                info = {
                    "Type": "Automation",
                    "Author": "Camshaft Software LTD",
                    "ExporterVersion": ver,
                }
                self.assertFalse(automation.is_automation_info(info))
        fam = catalog().families[FAMILY]
        ops = {c.op for c in fam.membership if c.fact == "ir.formats.exporter_version"}
        self.assertEqual(ops, {"in"})


class TestPartialSpoofing(unittest.TestCase):
    """Prueba 10, SINTÉTICO: solo uno o dos campos declarados."""

    CASES = (
        {"Type": "Automation"},
        {"ExporterVersion": "38022"},
        {"Type": "Automation", "ExporterVersion": "38022"},
        {"Author": "Camshaft Software LTD", "ExporterVersion": "38022"},
        {"Type": "Automation", "Author": "Camshaft Software LTD"},
        {"Type": "Car", "Author": "Camshaft Software", "ExporterVersion": "38022"},
        {
            "Type": "automation",
            "Author": "Camshaft Software LTD",
            "ExporterVersion": "38022",
        },
    )

    def test_not_member_and_detectors_agree(self) -> None:
        fam = catalog().families[FAMILY]
        for info in self.CASES:
            with self.subTest(info=info):
                self.assertFalse(members(fam, facts_from_info(info, "47437d5c")))
                self.assertFalse(automation.is_automation_info(info))
                self.assertFalse(pairing.declares_automation(info))

    def test_pairing_mod_zip_uses_the_same_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            for name, info in (
                ("solo_type", {"Name": "A", "Type": "Automation"}),
                (
                    "completo",
                    {
                        "Name": "B",
                        "Type": "Automation",
                        "Author": "Camshaft Software LTD",
                        "ExporterVersion": 40340,
                    },
                ),
                ("solo_autor", {"Name": "C", "Author": "Camshaft Software LTD"}),
            ):
                with zipfile.ZipFile(mods / f"{name}.zip", "w") as zf:
                    zf.writestr(f"vehicles/{name}/info.json", json.dumps(info))
            got = {v.name: v.automation for v in pairing.load_mod_zips(mods)}
        self.assertEqual(got, {"A": False, "B": True, "C": False})


# 2, 9 (real) ------------------------------------------------------------------------


@need_corpus
class TestCorpusMembers(unittest.TestCase):
    """REAL: las 17 exportaciones del corpus (Ibiza también como zip) son miembros."""

    @pytest.mark.ampliada
    def test_all_exports_are_members_with_their_version(self) -> None:
        sources = corpus_sources()
        self.assertGreaterEqual(len(sources), 13)
        for label, ver, _ in sources:
            with self.subTest(source=label):
                src, pv, ctx, _ = resolved(label)
                self.assertIn(FAMILY, ctx.families, ctx.family_evidence)
                self.assertEqual(ctx.fact("ir.formats.exporter_version").value, ver)
                self.assertTrue(automation.is_automation(src))

    def test_versions_present(self) -> None:
        vers = {ver for _, ver, _ in corpus_sources()}
        self.assertEqual(vers, {"38022", "40340", "200409"})

    def test_lax_info_json_of_200409(self) -> None:
        """Prueba 9: el ``info.json`` del 200409 no es JSON estricto (coma final) y
        aun así da los tres campos."""
        vd = SAMPLES / "2020_rpm_atlanta_gt_3_6t/vehicles/2020_rpm_atlanta_gt_3_6t"
        raw = (vd / "info.json").read_text("utf-8")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        info = loads(raw).value
        self.assertEqual(
            (info["Type"], info["Author"], str(info["ExporterVersion"])),
            ("Automation", "Camshaft Software LTD", "200409"),
        )
        src = load_vehicle_dir(vd, "2020_rpm_atlanta_gt_3_6t.pc")
        self.assertTrue(automation.is_automation(src))


# 3 (real) ---------------------------------------------------------------------------


@need_bng
class TestControlsExcluded(unittest.TestCase):
    """REAL: los coches oficiales de BeamNG no son miembros."""

    def test_project_controls(self) -> None:
        for name, _, _ in CONTROLS:
            with self.subTest(control=name):
                src, _, ctx, _ = resolved(f"control/{name}")
                self.assertNotIn(FAMILY, ctx.families)
                self.assertFalse(automation.is_automation(src))
                self.assertFalse(pairing.declares_automation(src.model_info))

    def test_all_official_vehicles_zero_members(self) -> None:
        """Recorre los ``info.json`` de todos los zips de ``content/vehicles``."""
        seen = 0
        for z in sorted(BNG.glob("*.zip")):
            with zipfile.ZipFile(z) as zf:
                for n in zf.namelist():
                    parts = n.split("/")
                    if (
                        len(parts) == 3
                        and parts[0] == "vehicles"
                        and parts[2] == "info.json"
                    ):
                        info = loads(zf.read(n).decode("utf-8", "replace")).value
                        if not isinstance(info, dict):
                            continue
                        seen += 1
                        with self.subTest(vehicle=n, zip=z.name):
                            self.assertFalse(automation.is_automation_info(info))
                            self.assertFalse(
                                members(
                                    catalog().families[FAMILY], facts_from_info(info)
                                )
                            )
        self.assertGreaterEqual(seen, 100)

    def test_etk800_854t_A_still_fails_to_load(self) -> None:
        with self.assertRaisesRegex(ValueError, "no existe en etk800.zip"):
            load_vehicle_zip(BNG / "etk800.zip", COMMON, "854t_A.pc")


# 4 (real) ---------------------------------------------------------------------------


@need_corpus
class TestClosedListOfNewMembers(unittest.TestCase):
    """REAL: con la pertenencia anterior, las altas son exactamente los 7 del 200409."""

    def test_new_members_are_the_closed_list(self) -> None:
        from migrate_family_provenance import EXPECTED_NEW_MEMBERS

        self.assertEqual(set(EXPECTED_NEW_MEMBERS), NEW_200409)
        old = family_with(OLD_MEMBERSHIP)
        entered = set()
        for label, _, _ in corpus_sources():
            src, _, ctx, _ = resolved(label)
            facts = dict(ctx.facts)
            was, now = members(old, facts), FAMILY in ctx.families
            self.assertTrue(now, label)
            if not was:
                entered.add(src.vehicle_id)
        self.assertEqual(entered, NEW_200409)

    def test_tool_refuses_unexpected_changes(self) -> None:
        from migrate_family_provenance import FamilyMigrationError, check_members

        def rec(vid, **info):
            facts = facts_from_info(info)
            facts["ir.identity.vehicle_id"] = Fact(
                "ir.identity.vehicle_id", vid, "text", "x"
            )
            return {
                "facts": {
                    k: {"value": f.value, "unit": f.unit, "kind": f.kind}
                    for k, f in facts.items()
                }
            }

        full = {"Type": "Automation", "Author": "Camshaft Software LTD"}
        old, new = family_with(OLD_MEMBERSHIP), family_with(NEW_MEMBERSHIP)
        ok = {
            "sources": {v: rec(v, ExporterVersion="200409", **full) for v in NEW_200409}
        }
        self.assertEqual(len(check_members(old, new, ok)), 7)
        extra = {
            "sources": dict(
                ok["sources"], x=rec("intruso", ExporterVersion="38022", **full)
            )
        }
        with self.assertRaisesRegex(FamilyMigrationError, "alta no prevista"):
            check_members(old, new, extra)
        short = {"sources": dict(list(ok["sources"].items())[:6])}
        with self.assertRaisesRegex(FamilyMigrationError, "no entran"):
            check_members(old, new, short)


# 5 (sintético a partir de una muestra real) ----------------------------------------


@need_corpus
class TestCorroborationNotRequired(unittest.TestCase):
    """SINTÉTICO (copia REDUCIDA de una exportación real 200409): sin ``license.txt``,
    sin ``*.car``, sin ``art/`` ni ``paints.meta`` sigue siendo miembro (D-P2)."""

    def test_reduced_copy_is_member(self) -> None:
        vd = SAMPLES / "2020_rpm_prothean_s/vehicles/2020_rpm_prothean_s"
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / vd.name
            dst.mkdir()
            for p in vd.rglob("*"):
                if p.is_file() and (
                    p.suffix in (".jbeam", ".pc") or p.name == "info.json"
                ):
                    out = dst / p.relative_to(vd)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(p, out)
            left = {p.name for p in dst.rglob("*") if p.is_file()}
            self.assertNotIn("license.txt", left)
            self.assertFalse(any(n.endswith(".car") for n in left))
            pc = sorted(p.name for p in dst.glob("*.pc"))[0]
            src = load_vehicle_dir(dst, pc)
            ctx, _ = resolve_vehicle(preview_physics(src, 70.0), catalog())
        self.assertIn(FAMILY, ctx.families)
        self.assertTrue(automation.is_automation(src))


# 6 (real) ---------------------------------------------------------------------------


@need_corpus
class TestDetectorsAgree(unittest.TestCase):
    """REAL: ``is_automation``, ``pairing`` y la familia dan lo mismo."""

    def test_same_answer(self) -> None:
        labels = [lab for lab, _, _ in corpus_sources()]
        if COMMON.is_file():
            labels += [f"control/{c[0]}" for c in CONTROLS]
        for label in labels:
            with self.subTest(source=label):
                src, _, ctx, _ = resolved(label)
                fam = FAMILY in ctx.families
                self.assertEqual(automation.is_automation(src), fam)
                self.assertEqual(pairing.declares_automation(src.model_info), fam)


# 7 (sintético) ----------------------------------------------------------------------


class TestAcRefsRename(unittest.TestCase):
    """SINTÉTICO: la clase de ``ac_refs`` cambia de nombre y nada más (D-P4)."""

    @property
    def NEW(self) -> str:
        return ac_refs.AC_UI_DECLARES_AUTOMATION

    def test_name_no_longer_collides(self) -> None:
        self.assertNotEqual(self.NEW, FAMILY)
        self.assertEqual(self.NEW, "ac_ui_declares_automation")
        text = (ROOT / "src/catalog/ac_refs.py").read_text("utf-8")
        self.assertNotIn('"automation_export"', text)

    def test_same_classification(self) -> None:
        cases = {
            ("x", None, ""): "origin_undeclared",
            ("x", "Camshaft Software", ""): self.NEW,
            ("x", "someone", "Exported from Automation"): self.NEW,
            ("x", "alguien", "Exportado de Automation, v2"): self.NEW,
            ("ks_x", None, ""): "kunos_probable",
            ("ks_x", "Kunos Simulazioni", ""): "kunos_probable",
            ("ks_x", "Kunos edit", ""): "mod_derived_from_kunos",
            ("ks_x", "camshaft", ""): self.NEW,
            ("beam2ac_x", "Camshaft", ""): "beam2ac_generated",
            ("y", "someone", "automation"): "mod",
        }
        for (cid, author, desc), cls in cases.items():
            with self.subTest(car=cid, author=author, desc=desc):
                ui = {"description": desc}
                if author is not None:
                    ui["author"] = author
                self.assertEqual(ac_refs.classify_origin(cid, ui)["class"], cls)


# 8, 9, 10 (real y sintético) --------------------------------------------------------


def current_family_rules() -> list[S.Rule]:
    cat = catalog()
    targets = {r.target for r in cat.rules.values()}
    return [
        r
        for t in sorted(targets)
        for r in cat.rules_for(t)
        if r.scope.level == "family" and r.scope.family == FAMILY
    ]


class TestTractionRuleV3(unittest.TestCase):
    """``dt.traction@3``: catálogo (estático) y casos sintéticos."""

    def test_rule_definition(self) -> None:
        cat = catalog()
        r3, r2 = cat.rules["dt.traction@3"], cat.rules["dt.traction@2"]
        self.assertEqual(r3.supersedes, "dt.traction@2")
        self.assertEqual(
            [(c.fact, c.op, c.value) for c in r3.conditions],
            [
                ("ir.drivetrain.layout", "in", ["FWD", "RWD"]),
                ("ir.formats.exporter_version", "in", ["38022", "40340"]),
            ],
        )
        a, b = S.to_dict(r2), S.to_dict(r3)
        changed = {k for k in a if a[k] != b[k]}
        self.assertEqual(
            changed,
            {"version", "supersedes", "conditions", "evidence", "history", "notes"},
        )
        self.assertEqual(S.to_dict(r2)["history"], S.to_dict(r3)["history"][:2])
        # G2.12 añade dt.traction_awd@1 (solo 200409 + combustionEngine + AWD); la
        # regla de FWD/RWD sigue siendo dt.traction@3 y no se amplía
        self.assertEqual(
            [r.key for r in cat.rules_for(TRACTION)],
            [
                "dt.traction@3",
                "dt.traction_awd@1",
                "dt.traction_awd_40340@1",
                "dt.traction_awd_lockc@1",
                "dt.traction_awd_lockc_40340@1",
                "dt.traction_awd_viscous_40340@1",
            ],  # G2.30, G2.44, bisexual_aspiration (2026-11-16), agus viscoso (2026-11-22)
        )

    def test_invariant_family_rules_are_protected(self) -> None:
        """Informe §5.2 paso 2: ampliar la familia nunca amplía una regla."""
        rules = current_family_rules()
        self.assertGreaterEqual(len(rules), 5)
        for r in rules:
            with self.subTest(rule=r.key):
                facts = [c.fact for c in r.conditions]
                self.assertTrue(
                    any(
                        f in PROTECTING or f.startswith("ir.formats.code_versions.")
                        for f in facts
                    ),
                    facts,
                )
        from migrate_family_provenance import unprotected_rules

        self.assertEqual(unprotected_rules(catalog()), [])

    def _resolve(self, facts: dict[str, Fact]):
        cat = catalog()
        ctx = VehicleContext("sintetico", "x", dict(facts))
        return ctx, ResolutionEngine(cat, ctx).resolve(TRACTION)

    def _facts(self, ver: str | None, layout: str | None) -> dict[str, Fact]:
        info = {"Type": "Automation", "Author": "Camshaft Software LTD"}
        if ver is not None:
            info["ExporterVersion"] = ver
        f = facts_from_info(info)
        if layout is not None:
            f["ir.drivetrain.layout"] = Fact(
                "ir.drivetrain.layout", layout, "enum", "extracted"
            )
        return f

    def test_synthetic_matrix(self) -> None:
        """SINTÉTICO: solo 38022/40340 con FWD/RWD dan la regla aplicada."""
        for ver in ("38022", "40340", "200409", None):
            for layout in ("FWD", "RWD", "AWD", None):
                with self.subTest(ver=ver, layout=layout):
                    ctx, r = self._resolve(self._facts(ver, layout))
                    applied = r.source == "rule" and r.rule == "dt.traction@3"
                    self.assertEqual(
                        applied, ver in ("38022", "40340") and layout in ("FWD", "RWD")
                    )
                    if applied:
                        self.assertEqual(r.value, layout)
                    else:
                        self.assertIsNone(r.value)
                        self.assertFalse(r.requires_approval)
                        self.assertNotEqual(r.status, "known")
                        self.assertNotIn(r.status, ("known", "inferred"))

    def test_missing_data_is_rejected_not_defaulted(self) -> None:
        for ver, layout, why in (
            ("38022", None, "ir.drivetrain.layout"),
            (None, "FWD", "ir.formats.exporter_version"),
        ):
            with self.subTest(ver=ver, layout=layout):
                _, r = self._resolve(self._facts(ver, layout))
                self.assertEqual(r.status, "manual_required")
                self.assertIsNone(r.value)
                rej = [x for x in r.rejected if x["rule"] == "dt.traction@3"]
                self.assertTrue(rej and why in rej[0]["reason"], r.rejected)

    def test_unknown_version_admitted_in_future_still_protected(self) -> None:
        """SINTÉTICO: aunque una versión nueva entrara en la familia, la regla de
        tracción no se aplicaría sola (protección propia de la regla)."""
        cat = Catalog.load(REPO_CATALOG)
        fam = family_with(
            NEW_MEMBERSHIP[:2]
            + [
                (
                    "ir.formats.exporter_version",
                    "in",
                    ["38022", "40340", "200409", "999999"],
                )
            ]
        )
        cat.families = {FAMILY: fam}
        ctx = VehicleContext("s", "x", self._facts("999999", "RWD"))
        r = ResolutionEngine(cat, ctx).resolve(TRACTION)
        self.assertIn(FAMILY, ctx.families)
        self.assertNotEqual(r.source, "rule")
        self.assertIsNone(r.value)


# G2.10 (decisiones D-G29a/b): únicas reglas de familia que se aplican a un 200409
G210_COMBUSTION_RULES = {
    "eng.power_lut_combustion@1",
    "eng.inertia_combustion@1",
    "eng.limiter_combustion@2",
    "eng.minimum_combustion@2",
}
# G2.24 (decisión del usuario sobre D-G29c): [COAST_REF] anclado en el techo; solo
# combustionEngine + 200409 + limitador activo (los 7 del corpus); no dependen de la
# tracción. La @1 (con un open_issue que la dejaba ambigua) no se aplica nunca.
G224_COAST_RULES = {
    "eng.coast_rpm_combustion@2",
    "eng.coast_torque_combustion@2",
    "eng.coast_nonlinearity_combustion@2",
}
G210_COMBUSTION_RULES = G210_COMBUSTION_RULES | G224_COAST_RULES
# G2.11: marchas y grupo final de la caja de doble embrague (solo dctGearbox + 200409 +
# combustionEngine; docs/research/g211_transmissions_2026-11-02.md)
G211_DCT_RULES = {"dt.gear_count_dct@1", "dt.gear_r_dct@1", "dt.final_dct@1"} | {
    f"dt.gear_{n}_dct@1" for n in range(1, 9)
}
# G2.43: automática con convertidor (D-G211-TC b)
G243_ATC_RULES = {
    "dt.gear_count_atc_200409@1",
    "dt.gear_r_atc_200409@1",
    "dt.final_atc_200409@1",
} | {f"dt.gear_{n}_atc_200409@1" for n in range(1, 10)}
# G2.13: geometría del chasis del 200409 (WHEELBASE y TRACK; solo por la versión del
# exportador, aplicadas en los 7 en cuanto la geometría se extrae). La lista cerrada de
# las pruebas siguientes las incluye; antes de G2.13 no existían.
# G2.14: masa no suspendida y TOTALMASS del 200409 (por la versión del exportador; el
# HUB_MASS solo se resuelve en un eje independiente y TOTALMASS con los dos)
G214_MASS_FRONT = {"susp.hub_mass_front_200409@1"}
G214_MASS_BOTH = {"susp.hub_mass_rear_200409@2", "car.totalmass_200409@2"}
G213_GEOMETRY_RULES = {
    "susp.wheelbase_200409@1",
    "susp.track_front_200409@1",
    "susp.track_rear_200409@1",
}
# G2.15: frenos y dirección del 200409 (por la versión del exportador; los de freno solo
# sin codo de pedal y freno de mano trasero; STEER_LOCK con cremallera). Los 7 cumplen
# las condiciones (inventario de G2.15), así que se aplican en todos.
# G2.19: dimensiones del neumático del 200409 (los 7) y aerodinámica medida en el banco
# (los 7 tienen registro en measurements/). Las 3 POSITION dependen de
# CG_LOCATION/BASEY, que solo se resuelven con piloto 0 kg (G2.18): con 70 kg no se
# aplican.
G219_TYRE_RULES = {
    f"tyre.{axle}.{k}_200409@1"
    for axle in ("front", "rear")
    for k in ("radius", "rim_radius", "width", "pressure_static")
}
G219_AERO_POSITION_RULES = {f"aero.wing{n}_position_200409@1" for n in range(3)}
# G2.20: modelo de agarre del neumático medido
# (tools/catalog/add_tyre_grip_200409_rules.py);
# solo con la medida del banco en el IR (los 7 del corpus la tienen)
G220_TYRE_RULES = (
    {"tyre.header_version_200409@1"}
    | {
        f"app.tyres.{axle}.{k}_200409@1"
        for axle in ("front", "rear")
        for k in ("dx0", "dy0", "dx1", "dy1", "xmu")
    }
    | {
        f"tyre.{axle}.{k}_200409@1"
        for axle in ("front", "rear")
        for k in (
            "angular_inertia",
            "rate",
            "fz0",
            "dy_ref",
            "dx_ref",
            "ls_expy",
            "ls_expx",
            "friction_limit_angle",
            "flex_gain",
            "falloff_level",
            "falloff_speed",
            "cx_mult",
            "brake_dx_mod",
            "speed_sensitivity",
            "combined_factor",
            "camber_gain",
            "pressure_ideal",
            "rolling_resistance_0",
        )
    }
)
# G2.25 (decisión del usuario: medianas del corpus de AC): DAMP y RELAXATION_LENGTH,
# mismas condiciones que el neumático de G2.20
# (tools/catalog/add_tyre_damp_relax_200409_rules.py)
G225_TYRE_RULES = {
    f"tyre.{axle}.{k}_200409@1"
    for axle in ("front", "rear")
    for k in ("damp", "relaxation_length")
}
G220_TYRE_RULES = G220_TYRE_RULES | G225_TYRE_RULES
G219_AERO_RULES = (
    {"aero.header_version_200409@1"}
    | {
        f"aero.wing{n}_{k}_200409@1"
        for n in range(3)
        for k in (
            "name",
            "chord",
            "span",
            "lut_aoa_cl_file",
            "lut_aoa_cd_file",
            "lut_aoa_cl",
            "lut_aoa_cd",
            "cl_gain",
            "cd_gain",
            "angle",
        )
    }
) - G219_AERO_POSITION_RULES
# G2.19: puntal en raíl medido (linkage strut_rail): TYPE, 7 puntos, claves a lo largo
# del puntal (si su campo del IR existe) y clones de las reglas DWB con la misma
# semántica en STRUT; delante, además, TOE_OUT, STEER_RATIO y LSRR
_G219_STRUT_POINTS = (
    "strut_car",
    "strut_tyre",
    "wbcar_bottom_front",
    "wbcar_bottom_rear",
    "wbtyre_bottom",
    "wbcar_steer",
    "wbtyre_steer",
)
_G219_STRUT_AXIS = (
    ("spring_rate", "wheel_rate"),
    ("rod_length", "spring_preload_travel"),
    ("damp_bump", "damper_rate_slow_bump"),
    ("damp_rebound", "damper_rate_slow_rebound"),
    ("damp_fast_bump", "damper_rate_fast_bump"),
    ("damp_fast_rebound", "damper_rate_fast_rebound"),
    ("damp_fast_bumpthreshold", "damper_fast_threshold_bump"),
    ("damp_fast_reboundthreshold", "damper_fast_threshold_rebound"),
)
_G219_STRUT_CLONES = (
    ("static_camber", "rest_camber"),
    ("bumpstop_up", "bumpstop_travel_up"),
    ("bumpstop_dn", "bumpstop_travel_down"),
    ("arb", "antiroll_rate"),
)


def g219_strut_axle(axle: str, ir) -> set[str]:
    base = f"suspension.axles.{axle}."
    out = {f"susp.type_strut_{axle}_200409@1"}
    out |= {f"susp.{k}_{axle}_strut_200409@1" for k in _G219_STRUT_POINTS}
    out |= {
        f"susp.{k}_{axle}_strut_200409@1"
        for k, f in _G219_STRUT_AXIS + _G219_STRUT_CLONES
        if ir.get(base + f).present
    }
    if axle == "front":
        pos = ir.get("steering.tie_rod_position")
        if pos.present:
            out |= {
                f"ctrl.steer_ratio_rod_{pos.value}_strut_200409@1",
                "ctrl.linear_steer_rod_ratio_strut_200409@1",
            }
            if ir.get(base + "rod_inward_shift_per_toe_in").present:
                out.add("susp.toe_out_front_strut_200409@1")
    return out


G215_BRAKES_STEERING = {
    "brk.max_torque_200409@1",
    "brk.front_share_200409@1",
    "brk.handbrake_torque_200409@1",
    "ctrl.steer_lock_200409@1",
}

# G2.16: suspensión por la estructura MEDIDA del eje (doble triángulo o equivalente),
# dirección con DWB delantero y embrague de la DCT (todas solo 200409; ninguna depende
# de la tracción). La lista cerrada de las pruebas siguientes las incluye según el IR.
_G216_KEYS = (
    "wbcar_top_front",
    "wbcar_top_rear",
    "wbcar_bottom_front",
    "wbcar_bottom_rear",
    "wbtyre_top",
    "wbtyre_bottom",
    "wbcar_steer",
    "wbtyre_steer",
    "spring_rate",
    "rod_length",
    "static_camber",
)


def g216_axle(axle: str, damper: bool) -> set[str]:
    out = {f"susp.type_dwb_{axle}_200409@1"}
    out |= {f"susp.{k}_{axle}_200409@1" for k in _G216_KEYS}
    if damper:
        out |= {f"susp.damp_bump_{axle}_200409@1", f"susp.damp_rebound_{axle}_200409@1"}
    return out


G216_ALL = (
    g216_axle("front", True)
    | g216_axle("rear", True)
    | {
        "ctrl.steer_ratio_rod_behind_200409@1",
        "ctrl.steer_ratio_rod_ahead_200409@1",
        "ctrl.linear_steer_rod_ratio_200409@1",
        "dt.clutch_max_torque_dct_200409@1",
    }
)


# G2.17: amortiguación por sentido (@2) y rápida, topes, [ARB], TOE_OUT delantero y
# LSRR @2 (todas solo 200409 y DWB medido; ninguna depende de la tracción). Las @1
# sustituidas (DAMP_BUMP/DAMP_REBOUND y LSRR) dejan de aplicarse.
G217_SUPERSEDED = {
    f"susp.damp_{k}_{axle}_200409@1"
    for k in ("bump", "rebound")
    for axle in ("front", "rear")
} | {"ctrl.linear_steer_rod_ratio_200409@1"}
_G217_FIELDS = (
    ("damp_fast_bump", "damper_rate_fast_bump"),
    ("damp_fast_rebound", "damper_rate_fast_rebound"),
    ("damp_fast_bumpthreshold", "damper_fast_threshold_bump"),
    ("damp_fast_reboundthreshold", "damper_fast_threshold_rebound"),
    ("bumpstop_up", "bumpstop_travel_up"),
    ("bumpstop_dn", "bumpstop_travel_down"),
    ("bump_stop_rate", "bumpstop_rate"),
    ("arb", "antiroll_rate"),
)


def g217_axle(axle: str, ir) -> set[str]:
    base = f"suspension.axles.{axle}."
    out = {
        f"susp.{k}_{axle}_200409@1" for k, f in _G217_FIELDS if ir.get(base + f).present
    }
    if ir.get(base + "damper_rate_slow_bump").present:
        out |= {f"susp.damp_bump_{axle}_200409@2", f"susp.damp_rebound_{axle}_200409@2"}
    return out


G217_ALL = (
    {f"susp.{k}_{a}_200409@1" for k, _ in _G217_FIELDS for a in ("front", "rear")}
    | {
        f"susp.damp_{k}_{a}_200409@2"
        for k in ("bump", "rebound")
        for a in ("front", "rear")
    }
    | {"susp.toe_out_front_200409@1", "ctrl.linear_steer_rod_ratio_200409@2"}
)


@need_corpus
class TestTractionRuleOnCorpus(unittest.TestCase):
    """REAL: 8 (38022 y 40340) y 9 (200409)."""

    def test_applied_in_38022_and_40340_where_layout_is_2wd(self) -> None:
        seen = set()
        for label, ver, _ in corpus_sources():
            if ver == "200409":
                continue
            _, _, ctx, res = resolved(label)
            layout = ctx.fact("ir.drivetrain.layout")
            r = res[TRACTION]
            with self.subTest(source=label, layout=layout and layout.value):
                if layout is not None and layout.value in ("FWD", "RWD"):
                    self.assertEqual((r.source, r.rule), ("rule", "dt.traction@3"))
                    self.assertEqual(r.value, layout.value)
                    self.assertEqual(r.status, "known")
                    seen.add(ver)
                elif label.endswith(
                    "autist_cuanto"
                ):  # G2.30: AWD camsoEngine, regla propia
                    self.assertEqual(r.rule, "dt.traction_awd_40340@1")
                elif label.endswith(
                    "agus_1_nrt_1"
                ):  # AWD con diferenciales viscosos (2026-11-22)
                    self.assertEqual(r.rule, "dt.traction_awd_viscous_40340@1")
                else:
                    self.assertNotEqual(r.source, "rule")
        self.assertEqual(seen, {"38022", "40340"})

    def test_ibiza_unchanged_value_and_confidence(self) -> None:
        _, _, ctx, res = resolved("dir/seat_ibiza_acfr")
        r = res[TRACTION]
        self.assertEqual((r.rule, r.value, r.status), ("dt.traction@3", "FWD", "known"))
        self.assertEqual(res["ac:power.lut"].rule, "eng.power_lut@4")

    def test_not_applied_in_200409(self) -> None:
        for label, ver, _ in corpus_sources():
            if ver != "200409":
                continue
            with self.subTest(source=label):
                _, _, ctx, res = resolved(label)
                self.assertIn(FAMILY, ctx.families)
                r = res[TRACTION]
                # dt.traction@3 nunca se aplica al 200409; G2.12: solo
                # dt.traction_awd@1 (AWD con central open/lsd) puede resolverlo
                self.assertNotEqual(r.rule, "dt.traction@3")
                if r.source == "rule":
                    self.assertEqual(r.value, "AWD")  # G2.44: o dt.traction_awd_lockc@1
                    self.assertIn(
                        r.rule, ("dt.traction_awd@1", "dt.traction_awd_lockc@1")
                    )
                else:
                    self.assertIsNone(r.value)
                    self.assertNotIn(r.status, ("known", "inferred"))

    @pytest.mark.ampliada
    def test_latent_200409_with_extracted_traction(self) -> None:
        """Prueba 5 del informe (SINTÉTICO sobre un 200409 REAL): se inyectan
        ``layout = RWD`` y ``device_type = combustionEngine``, como si existieran los
        adaptadores. La regla queda rechazada por la versión del exportador: ni
        aplicada ni propuesta (el informe esperaba «propuesta»; con la condición en la
        regla el motor la rechaza antes, que es más estricto)."""
        _, pv, _, _ = resolved("dir/2020_rpm_atlanta_gt_3_6t")
        from src.attrs.report import context_from_preview

        cat = catalog()
        ctx = context_from_preview(pv, cat, 70.0)
        for fid, val in (
            ("ir.drivetrain.layout", "RWD"),
            ("ir.engine.device_type", "combustionEngine"),
        ):
            ctx.facts[fid] = Fact(fid, val, "enum", "extracted")
        eng = ResolutionEngine(cat, ctx)
        self.assertIn(FAMILY, ctx.families)
        r = eng.resolve(TRACTION)
        self.assertEqual(r.status, "manual_required")
        self.assertIsNone(r.value)
        self.assertFalse(r.requires_approval)
        rej = [x for x in r.rejected if x["rule"] == "dt.traction@3"]
        self.assertIn("ir.formats.exporter_version", rej[0]["reason"])
        # y ninguna regla de motor camso (power.lut, LIMITER…) se aplica; G2.10: solo
        # las reglas combustion del 200409 (lista cerrada)
        for rule in current_family_rules():
            # G2.11: atlanta tiene caja dctGearbox: sus reglas de marchas se aplican por
            # la condición de caja, no por la tracción inyectada (ver test_g211)
            # G2.13: las de geometría tampoco dependen de la tracción inyectada
            if rule.key in (
                G210_COMBUSTION_RULES
                | G211_DCT_RULES
                | G243_ATC_RULES
                | G213_GEOMETRY_RULES
                | G214_MASS_FRONT
                | G214_MASS_BOTH
                | G215_BRAKES_STEERING  # G2.15: no dependen de la tracción inyectada
                | {"dt.gearbox_inertia_200409@1"}  # 2026-11-10: depende de la caja
                | {  # G2.45: con piloto de 70 kg: CdG, BASEY e inercia, no la tracción
                    "susp.cg_location_200409_driver@1",
                    "susp.basey_front_200409_driver@1",
                    "susp.basey_rear_200409_driver@1",
                    "car.explicit_inertia_200409_driver@1",
                }
                | G227_CAR_BASICS
                | G216_ALL  # G2.16: ni la suspensión, la dirección o el embrague DCT
                | G217_ALL  # G2.17: ni amortiguación, topes, ARB, TOE_OUT ni LSRR @2
                # G2.19: ni las dimensiones del neumático ni la aerodinámica medida
                | G219_TYRE_RULES
                | G219_AERO_RULES
                | G219_AERO_POSITION_RULES
                | G220_TYRE_RULES  # G2.20: el neumático no depende de la tracción
                | g219_strut_axle("front", import_preview(pv, 70.0))
            ) or any(
                x in rule.key
                for x in (
                    "_axle",
                    "susp.axle_",
                    "_lockc",
                    "_openmode",
                    "abs.",
                )  # G2.50: el ABS es de los frenos
            ):
                continue  # G2.44: eje rígido y central bloqueado: estructura, no tracción
            res = eng.resolve(rule.target)
            with self.subTest(rule=rule.key):
                self.assertFalse(
                    res.source == "rule" and res.rule == rule.key, res.reasons
                )

    def test_adapter_missing_blocks_with_reason(self) -> None:
        """Prueba 8 (REAL): un 200409 con ``combustionEngine`` es de Automation y no
        resuelve nada con reglas de familia. G2.9: el adaptador ya existe; las reglas de
        motor se rechazan por su condición de dispositivo (``camsoEngine``), no por
        falta del hecho (antes el motivo nombraba el adaptador ausente)."""
        family_keys = {r.key for r in current_family_rules()}
        for label, ver, _ in corpus_sources():
            if ver != "200409":
                continue
            with self.subTest(source=label):
                src, pv, ctx, res = resolved(label)
                self.assertTrue(automation.is_automation(src))
                applied = {
                    r.rule for r in res.values() if r.source == "rule"
                } & family_keys
                # G2.10: solo las reglas combustion del 200409 (lista cerrada); G2.11:
                # más las de la caja de doble embrague si la caja es dctGearbox (las
                # aplicadas son las de las marchas que existen; la 8.ª «no aplica»)
                ir_kind = (
                    pv.drivetrain.drivetrain.gearbox.type if pv.drivetrain else None
                )
                # G2.13: más las 3 de geometría del chasis (versión del exportador)
                expected = set(G210_COMBUSTION_RULES) | G213_GEOMETRY_RULES
                # G2.14: HUB_MASS delantero (los 7); trasero y TOTALMASS solo si el eje
                # trasero es independiente (atacama: eje rígido, sin masa por rueda)
                ir = import_preview(pv, 70.0)
                expected |= G214_MASS_FRONT
                # G2.15: frenos y STEER_LOCK (los 7 cumplen sus condiciones)
                expected |= G215_BRAKES_STEERING
                # G2.27: combustible, nombre y FFMULT (solo versión del exportador)
                expected |= G227_CAR_BASICS
                # investigación 2026-11-10: [GEARBOX] INERTIA (DCT y automática)
                if ir_kind in ("dctGearbox", "automaticGearbox"):
                    expected.add("dt.gearbox_inertia_200409@1")
                rigid = ir.get("suspension.axles.rear.linkage").value == "rigid_axle"
                if ir.get("suspension.axles.rear.unsprung_mass_per_wheel").present:
                    if not rigid:
                        expected |= G214_MASS_BOTH
                if (
                    not rigid
                ):  # G2.45: piloto de 70 kg → reglas *_driver (CdG, BASEY, inercia)
                    expected |= {
                        "susp.cg_location_200409_driver@1",
                        "susp.basey_front_200409_driver@1",
                        "susp.basey_rear_200409_driver@1",
                        "car.explicit_inertia_200409_driver@1",
                    }
                if rigid:  # G2.44: eje rígido → AXLE (J0…J4 con 5 enlaces)
                    expected |= {
                        r.key
                        for r in catalog().rules.values()
                        if r.kind != "applicability"
                        and ("_axle" in r.id or r.id.startswith("susp.axle_"))
                        and not any(f"_j{n}_" in r.id + "_" for n in (5, 6, 7))
                        and "nodriver" not in r.id  # el test usa piloto de 70 kg
                    }
                if (
                    ir.get("drivetrain.differentials.center.initial_mode").value
                    == "locked"
                ):
                    expected |= {  # G2.44: central bloqueado (D-G212-LOCK)
                        r.key
                        for r in catalog().rules.values()
                        if ("_lockc" in r.id or "_openmode" in r.id)
                        and not r.id.endswith("_40340")  # reglas de 40340 + camsoEngine
                    }
                # G2.16: por eje con doble triángulo medido; dirección con DWB delantero
                # (signo por la posición de la barra); embrague con caja dctGearbox
                for axle in ("front", "rear"):
                    if ir.get(f"suspension.axles.{axle}.linkage").value in (
                        "dwb_5link",
                        "dwb_equivalent",
                    ):
                        expected |= g216_axle(
                            axle,
                            ir.get(f"suspension.axles.{axle}.damper_rate_slow").present,
                        )
                        # G2.17: amortiguación, topes y ARB del eje DWB
                        expected |= g217_axle(axle, ir)
                    # G2.19: puntal en raíl medido
                    if ir.get(f"suspension.axles.{axle}.linkage").value == "strut_rail":
                        expected |= g219_strut_axle(axle, ir)
                pos = ir.get("steering.tie_rod_position")
                # G2.19: la barra también se mide con el puntal en raíl; las reglas DWB
                # de dirección solo con DWB delantero (el puntal tiene sus clones)
                front_dwb = ir.get("suspension.axles.front.linkage").value in (
                    "dwb_5link",
                    "dwb_equivalent",
                )
                if pos.present and front_dwb:
                    expected |= {
                        f"ctrl.steer_ratio_rod_{pos.value}_200409@1",
                        "ctrl.linear_steer_rod_ratio_200409@1",
                    }
                # G2.17: LSRR @2 sustituye a @1; TOE_OUT delantero con la barra de
                # dirección y su sensibilidad medida
                if pos.present and front_dwb:
                    expected.add("ctrl.linear_steer_rod_ratio_200409@2")
                    if ir.get(
                        "suspension.axles.front.rod_inward_shift_per_toe_in"
                    ).present:
                        expected.add("susp.toe_out_front_200409@1")
                expected -= G217_SUPERSEDED
                if ir_kind == "dctGearbox":
                    expected.add("dt.clutch_max_torque_dct_200409@1")
                if ir_kind == "dctGearbox":
                    expected |= G211_DCT_RULES
                if ir_kind == "automaticGearbox":
                    # solo las marchas que existen: GEAR_n con n ≤ forward_count
                    n = len(pv.drivetrain.drivetrain.gearbox.params["gearRatios"]) - 2
                    expected |= {
                        k
                        for k in G243_ATC_RULES
                        if not (
                            k.startswith("dt.gear_")
                            and k[8].isdigit()
                            and int(k[8]) > n
                        )
                    }
                # G2.12: AWD con central open/lsd. G2.13: atacama y patagonia ya llegan
                # con tracción (central `locked`, sin reglas AWD: D-G212-LOCK), así que
                # la condición es el tipo del central, no solo su existencia
                if (
                    pv.drivetrain is not None
                    and pv.drivetrain.layout.center is not None
                    and pv.drivetrain.layout.center.diff.get("kind") in ("open", "lsd")
                ):
                    expected |= {"dt.traction_awd@1", "dt.awd_front_share@1"}
                    expected |= {
                        f"dt.awd_{role}_diff_{k}_open@1"
                        for role, ax in (("center", pv.drivetrain.layout.center),)
                        + tuple(pv.drivetrain.layout.axles.items())
                        if ax.diff.get("kind") == "open"
                        for k in ("power", "coast", "preload")
                    }
                    # G2.23: bloqueos LSD (×2) para cada diferencial `lsd`; los 4 del
                    # corpus usan 50/0,2/0,2 del juego (coef ≤ 1 se cumple)
                    expected |= {
                        f"dt.awd_{role}_diff_{k}_lsd_200409@1"
                        for role, ax in (("center", pv.drivetrain.layout.center),)
                        + tuple(pv.drivetrain.layout.axles.items())
                        if ax.diff.get("kind") == "lsd"
                        for k in ("power", "coast", "preload")
                    }
                # G2.19: neumáticos (dimensiones) y aerodinámica medida; POSITION solo
                # con CG_LOCATION/BASEY resueltos (piloto 0 kg)
                expected |= G219_TYRE_RULES
                if ir.get("aero.cda_vs_aoa").present:
                    expected |= G219_AERO_RULES
                    cg = res["ac:suspensions.ini/BASIC/CG_LOCATION"]
                    if cg.source == "rule" and cg.status in ("known", "inferred"):
                        expected |= G219_AERO_POSITION_RULES
                # G2.20: neumático medido en el banco (registro asociado por huella)
                if ir.get("tyres.axles.front.force_slip_samples").present:
                    expected |= G220_TYRE_RULES
                # G2.50: ABS -> electronics.ini [ABS] (PRESENT/ACTIVE siempre que haya
                # resumen; límite y frecuencia solo con ABS)
                ap = ir.get("brakes.abs_present")
                if (
                    ap.present
                    and ir.get("identity.vehicle_id").value != "seat_ibiza_acfr"
                ):
                    expected |= {"abs.present@1", "abs.active@1"}
                    if ap.value == 1:
                        expected |= {"abs.slip_ratio_limit@1", "abs.rate_hz@1"}
                self.assertEqual(applied, expected)
                ir = import_preview(pv, 70.0)
                self.assertEqual(ir.get("engine.device_type").value, "combustionEngine")
                why = [
                    x["reason"]
                    for x in res["ac:power.lut"].to_dict()["rejected"]
                    if x["rule"] == "eng.power_lut@4"
                ]
                self.assertTrue(
                    any("device_type='combustionEngine'" in w for w in why), why
                )


if __name__ == "__main__":
    unittest.main()
