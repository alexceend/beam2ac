"""Eje rígido trasero → ``TYPE=AXLE`` de AC (G2.44, 2026-11-14): atributos, reglas y aplicabilidad.

    python tools/catalog/add_rigid_axle_rules.py [--dry-run]

NO volver a ejecutar tras escribir (reglas inmutables). Informe: ``docs/research/g244_eje_rigido_2026-11-14.md``.

Qué añade (todo ``provisional``; solo con ``ir.suspension.axles.rear.linkage == rigid_axle``):

* atributos nuevos: ``[HEADER] VERSION`` y ``[AXLE]`` (``LINK_COUNT``, ``ATTACH_REL_POS``, ``TORQUE_REACTION``,
  ``LEAF_SPRING_LAT_K``, ``J0…J5_CAR/_AXLE``) de ``suspensions.ini``;
* reglas de valor: clones de las del DWB trasero del 200409 (muelle, precarga, amortiguadores, topes) con la
  estructura de eje rígido, ``TYPE=AXLE``, los puntos ``J<n>``, ``HUB_MASS`` = masa del eje entero y las variantes
  de ``TOTALMASS``/CdG/``BASEY``/inercia que cuentan el ``HUB_MASS`` trasero UNA vez (``rear_hub_is_axle``);
* las reglas del 200409/40340 que usaban el ``HUB_MASS`` trasero por rueda pasan a ``@2`` con ``linkage ne
  rigid_axle`` (si no, aplicarían a la vez que la variante de eje);
* aplicabilidad: ``app.rear.*@2`` (TYPE explícito) y ``app.rear.*@1`` nuevas para los puntos WB*; ``app.axle.*``.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-14"
AUTHOR = "claude (G2.44: eje rígido → AXLE, decisión autónoma de la misión de desbloqueo)"
DOC = "docs/research/g244_eje_rigido_2026-11-14.md"
RULES = ROOT / "catalog" / "rules"
ATTR_FILE = ROOT / "catalog" / "attributes" / "assettocorsa.json"
LINK = "ir.suspension.axles.rear.linkage"
VERSION = "ir.formats.exporter_version"
EXPORTERS = ["200409", "40340", "38022"]
REAR = "ir.suspension.axles.rear"
SUSP = "ac:suspensions.ini"
ACS = "output/g217_suspension_steering_2026-11-04/acs_suspension/c2"
ASSUMPTIONS = [
    "heave puro: el eje sube sin girar; palancas = componente vertical del eje del muelle/amortiguador/tope "
    "(la cinemática con las barras la resuelve el motor de AC con las mismas barras como DistanceJoint)",
    "suspensión de eje de AC [C] sale con el aviso «local variable allocation has failed»: el punto del muelle "
    "(centro + semivía·ATTACH_REL_POS, anclaje en la carrocería 0,2 m por encima) es lectura dudosa; sin prueba en AC",
]


def hist(reason: str) -> list[dict]:
    return [
        {"date": DATE, "author": AUTHOR, "action": "create", "reason": reason, "status_from": "",
         "status_to": "provisional"}
    ]


def load(key: str) -> dict:
    return json.loads((RULES / f"{key}.json").read_text("utf-8"))


def widen(d: dict, exporters: list[str]) -> None:
    for c in d.get("conditions", []) + d.get("scope", {}).get("conditions", []):
        if c["fact"] == VERSION:
            c["value"] = list(exporters)


def set_linkage(d: dict, kind_ops: tuple[str, object]) -> None:
    d["conditions"] = [c for c in d["conditions"] if c["fact"] != LINK]
    d["conditions"].append({"fact": LINK, "op": kind_ops[0], "value": kind_ops[1]})


def finish(d: dict, rid: str, reason: str, extra_assumptions: list[str] | None = None) -> S.Rule:
    d.update(id=rid, version=1, status="provisional", supersedes="", history=hist(reason), notes="G2.44")
    d["assumptions"] = list(d.get("assumptions", [])) + ASSUMPTIONS + (extra_assumptions or [])
    d["evidence"] = list(d.get("evidence", [])) + [{"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}]
    d["target_doc"] = list(d.get("target_doc", [])) + [
        {"type": "code", "path": ACS, "locator": "suspensión de eje de AC, step",
         "sha256": "", "note": ""}
    ]
    return S.from_dict(d)


def clone_for_axle(src: str, rid: str, target: str | None = None, **over) -> S.Rule:
    d = load(src)
    widen(d, EXPORTERS)
    set_linkage(d, ("eq", "rigid_axle"))
    if target:
        d["target"] = target
    d.update(over)
    return finish(d, rid, f"clon de {src} para el eje rígido")


def new_version(src: str, mutate, reason: str) -> S.Rule:
    d = load(src)
    mutate(d)
    old = f"{d['id']}@{d['version']}"
    d.update(version=d["version"] + 1, supersedes=old)
    d["history"] = list(d.get("history", [])) + [
        {"date": DATE, "author": AUTHOR, "action": "new_version", "reason": reason,
         "status_from": d["status"], "status_to": d["status"]}
    ]
    return S.from_dict(d)


def not_axle(d: dict) -> None:
    d["conditions"] = list(d["conditions"]) + [{"fact": LINK, "op": "ne", "value": "rigid_axle"}]


def app_rule(src: str, rid: str, target: str, unit: str, cond: dict, version: int = 1, supersedes: str = "") -> S.Rule:
    d = load(src)
    d.update(id=rid, version=version, target=target, supersedes=supersedes)
    d["applies_when"] = [cond]
    d["transform"]["output_unit"] = unit
    d["history"] = [
        {"date": DATE, "author": AUTHOR, "action": "create" if not supersedes else "new_version",
         "reason": "aplicabilidad por TYPE con eje rígido (AXLE)", "status_from": "", "status_to": d["status"]}
    ]
    d["evidence"] = list(d.get("evidence", [])) + [{"type": "report", "path": DOC, "locator": "", "sha256": "", "note": ""}]
    d["notes"] = "G2.44"
    return S.from_dict(d)


def attributes() -> list[dict]:
    base = json.loads(ATTR_FILE.read_text("utf-8"))
    ids = {a["id"] for a in base}
    doc = [{"type": "doc", "path": "docs/research/g244_eje_rigido_2026-11-14.md", "locator": "", "sha256": "", "note": ""},
           {"type": "code", "path": ACS, "locator": "suspensión de eje de AC", "sha256": "", "note": ""}]
    spec = [("HEADER/VERSION", "1", "number", "[HEADER] VERSION: con ≥ 4 AC lee ATTACH_REL_POS (SuspensionAxle)"),
            ("AXLE/LINK_COUNT", "1", "number", "número de enlaces J<n> del eje rígido"),
            ("AXLE/ATTACH_REL_POS", "1", "number", "posición lateral del muelle (fracción de la semivía)"),
            ("AXLE/TORQUE_REACTION", "1", "number", "reacción del par motor sobre el eje (transmisión 2WD de AC)"),
            ("AXLE/LEAF_SPRING_LAT_K", "undocumented", "number", "rigidez lateral de ballesta (0 = sin ballesta)")]
    for i in range(6):
        for side in ("CAR", "AXLE"):
            spec.append((f"AXLE/J{i}_{side}", "m", "vector", f"punto {side} del enlace {i}, relativo al centro del eje"))
    out = []
    for key, unit, dtype, desc in spec:
        aid = f"{SUSP}/{key}"
        if aid not in ids:
            out.append({"schema": "beam2ac.attribute/1", "id": aid, "game": "assettocorsa", "unit": unit,
                        "dtype": dtype, "description": desc, "doc": doc, "required_for_load": False})
    return out


def build() -> list[S.Rule]:
    r: list[S.Rule] = []
    d = load("susp.type_dwb_rear_200409@1")
    widen(d, EXPORTERS)
    set_linkage(d, ("eq", "rigid_axle"))
    d["transform"] = {"id": "constant", "params": {"value": "AXLE"}, "input_units": {}, "output_unit": "enum",
                      "formula": "eje rígido de BeamNG (cuerpo único de las dos ruedas) = TYPE=AXLE de AC"}
    r.append(finish(d, "susp.type_axle_rear", "TYPE=AXLE para un eje rígido con enlaces de barra"))
    for src, rid in [
        ("susp.spring_rate_rear_200409@1", "susp.spring_rate_rear_axle"),
        ("susp.rod_length_rear_200409@1", "susp.rod_length_rear_axle"),
        ("susp.damp_bump_rear_200409@2", "susp.damp_bump_rear_axle"),
        ("susp.damp_rebound_rear_200409@2", "susp.damp_rebound_rear_axle"),
        ("susp.damp_fast_bump_rear_200409@1", "susp.damp_fast_bump_rear_axle"),
        ("susp.damp_fast_rebound_rear_200409@1", "susp.damp_fast_rebound_rear_axle"),
        ("susp.damp_fast_bumpthreshold_rear_200409@1", "susp.damp_fast_bumpthreshold_rear_axle"),
        ("susp.damp_fast_reboundthreshold_rear_200409@1", "susp.damp_fast_reboundthreshold_rear_axle"),
        ("susp.bumpstop_up_rear_200409@1", "susp.bumpstop_up_rear_axle"),
        ("susp.bumpstop_dn_rear_200409@1", "susp.bumpstop_dn_rear_axle"),
        ("susp.bump_stop_rate_rear_200409@1", "susp.bump_stop_rate_rear_axle"),
    ]:
        r.append(clone_for_axle(src, rid))
    # HUB_MASS = eje entero (AC lo cuenta una vez): 2 × el valor por rueda del IR
    d = load("susp.hub_mass_rear_200409@1")
    widen(d, EXPORTERS)
    set_linkage(d, ("eq", "rigid_axle"))
    d["transform"] = {"id": "scale", "params": {"factor": 2.0}, "input_units": {"x": "kg"}, "output_unit": "kg",
                      "formula": "HUB_MASS(AXLE) = masa del eje entero = 2 × (masa por rueda equivalente)"}
    r.append(finish(d, "susp.hub_mass_rear_axle", "HUB_MASS del eje rígido = eje entero",
                    ["en AC un AXLE aporta HUB_MASS UNA vez (G2.14); el valor por rueda del IR es la mitad del eje"]))
    # variantes que cuentan el HUB_MASS trasero una vez
    for src, rid in [
        ("susp.cg_location_200409_nodriver@1", "susp.cg_location_axle_nodriver"),
        ("susp.basey_front_200409_nodriver@1", "susp.basey_front_axle_nodriver"),
        ("susp.basey_rear_200409_nodriver@1", "susp.basey_rear_axle_nodriver"),
        ("car.explicit_inertia_200409_nodriver@1", "car.explicit_inertia_axle_nodriver"),
    ]:
        d = load(src)
        widen(d, EXPORTERS)
        set_linkage(d, ("eq", "rigid_axle"))
        d["transform"]["params"] = dict(d["transform"]["params"], rear_hub_is_axle=True)
        r.append(finish(d, rid, f"clon de {src}: HUB_MASS trasero (AXLE) = eje entero, contado una vez"))
    d = load("car.totalmass_200409@1")
    widen(d, EXPORTERS)
    set_linkage(d, ("eq", "rigid_axle"))
    d["transform"]["params"]["coefficients"]["hub_rear"] = -1
    d["transform"]["formula"] = "TOTALMASS = masa BeamNG + piloto − 2·HUB_MASS_del − HUB_MASS_eje_trasero (AXLE, una vez)"
    r.append(finish(d, "car.totalmass_axle", "TOTALMASS con eje rígido trasero"))
    # [AXLE]
    base = load("susp.track_rear_200409@1")
    for key, ac, src_in in (("link_count", "LINK_COUNT", f"{REAR}.link_count"),
                            ("attach_rel_pos", "ATTACH_REL_POS", f"{REAR}.attach_rel_pos")):
        d = copy.deepcopy(base)
        widen(d, EXPORTERS)
        set_linkage(d, ("eq", "rigid_axle"))
        d.update(target=f"{SUSP}/AXLE/{ac}", inputs={"x": src_in})
        d["transform"].update(input_units={"x": "1"}, output_unit="1", formula=f"AXLE/{ac} = {src_in}")
        r.append(finish(d, f"susp.axle_{key}", f"[AXLE] {ac} medido"))
    for ac, why in (("TORQUE_REACTION", "0 = sin par de reacción artificial: el aplica el reparto completo del par al cuerpo "
                                        "(transmisión 2WD de AC [C]); BeamNG resuelve la reacción con sus propias vigas"),
                    ("LEAF_SPRING_LAT_K", "0 = sin ballesta (el eje se suspende con muelle helicoidal y barra Panhard)")):
        d = copy.deepcopy(base)
        widen(d, EXPORTERS)
        set_linkage(d, ("eq", "rigid_axle"))
        d.update(target=f"{SUSP}/AXLE/{ac}", inputs={})
        d["transform"] = {"id": "constant", "params": {"value": 0}, "input_units": {}, "output_unit": "1", "formula": why}
        r.append(finish(d, f"susp.axle_{ac.lower()}", f"[AXLE] {ac} = 0", [why]))
    d = copy.deepcopy(base)
    widen(d, EXPORTERS)
    set_linkage(d, ("eq", "rigid_axle"))
    d.update(target=f"{SUSP}/HEADER/VERSION", inputs={})
    d["transform"] = {"id": "constant", "params": {"value": 4}, "input_units": {}, "output_unit": "1",
                      "formula": "ATTACH_REL_POS solo se lee con [HEADER] VERSION ≥ 4 (SuspensionAxle [C])"}
    r.append(finish(d, "susp.header_version_axle", "VERSION 4 para leer ATTACH_REL_POS",
                    ["VERSION ≥ 4 también activa RIM_OFFSET en DWB/STRUT (G2.27): el delantero sin RIM_OFFSET no cambia"]))
    anchor = load("susp.wbcar_bottom_front_rear_200409@1")
    for i in range(6):
        for side in ("CAR", "AXLE"):
            d = copy.deepcopy(anchor)
            widen(d, EXPORTERS)
            set_linkage(d, ("eq", "rigid_axle"))
            src_in = f"{REAR}.anchors.J{i}_{side}"
            d["conditions"].append({"fact": src_in, "op": "exists", "value": None})
            d.update(target=f"{SUSP}/AXLE/J{i}_{side}", inputs={"x": src_in})
            d["transform"]["formula"] = (f"punto {side} del enlace {i} − centro del eje en reposo (ejes AC: x izquierda, "
                                         "y arriba, z adelante)")
            r.append(finish(d, f"susp.axle_j{i}_{side.lower()}", f"[AXLE] J{i}_{side} medido"))
    # --- @2: las reglas con el HUB_MASS trasero por rueda no se aplican al eje rígido
    for suffix in ("200409", "40340"):
        for src, reason in (
            (f"susp.hub_mass_rear_{suffix}@1", "HUB_MASS por rueda: no aplica al eje rígido"),
            (f"susp.cg_location_{suffix}_nodriver@1", "CdG con HUB_MASS por rueda: no aplica al eje rígido"),
            (f"susp.basey_front_{suffix}_nodriver@1", "idem"),
            (f"susp.basey_rear_{suffix}_nodriver@1", "idem"),
            (f"car.explicit_inertia_{suffix}_nodriver@1", "idem"),
            (f"car.totalmass_{suffix}@1", "TOTALMASS con 2·HUB_MASS trasero: no aplica al eje rígido"),
        ):
            r.append(new_version(src, not_axle, reason))
    # --- aplicabilidad
    typ = f"{SUSP}/REAR/TYPE"
    for k, unit in (("strut_car", "m"), ("strut_tyre", "m")):
        r.append(app_rule(f"app.rear.{k}@1", f"app.rear.{k}", f"{SUSP}/REAR/{k.upper()}", unit,
                          {"fact": typ, "op": "eq", "value": "STRUT"}, 2, f"app.rear.{k}@1"))
    for k in ("wbcar_top_front", "wbcar_top_rear", "wbtyre_top"):
        r.append(app_rule(f"app.rear.{k}@1", f"app.rear.{k}", f"{SUSP}/REAR/{k.upper()}", "m",
                          {"fact": typ, "op": "eq", "value": "DWB"}, 2, f"app.rear.{k}@1"))
    for k in ("wbcar_bottom_front", "wbcar_bottom_rear", "wbcar_steer", "wbtyre_bottom", "wbtyre_steer"):
        r.append(app_rule("app.rear.wbtyre_top@1", f"app.rear.{k}", f"{SUSP}/REAR/{k.upper()}", "m",
                          {"fact": typ, "op": "in", "value": ["DWB", "STRUT"]}))
    keys = ["LINK_COUNT", "ATTACH_REL_POS", "TORQUE_REACTION", "LEAF_SPRING_LAT_K"] + [
        f"J{i}_{s}" for i in range(6) for s in ("CAR", "AXLE")]
    for k in keys:
        r.append(app_rule("app.rear.wbtyre_top@1", f"app.axle.{k.lower()}", f"{SUSP}/AXLE/{k}", "1" if "J" != k[0] else "m",
                          {"fact": typ, "op": "eq", "value": "AXLE"}))
    r.append(app_rule("app.rear.wbtyre_top@1", "app.header.version_axle", f"{SUSP}/HEADER/VERSION", "1",
                      {"fact": typ, "op": "eq", "value": "AXLE"}))
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rules = build()
    attrs = attributes()
    have = {p.stem for p in RULES.glob("*.json")}
    for x in rules:
        S.from_dict(S.to_dict(x))
        if x.key in have:
            raise SystemExit(f"ya existe: {x.key}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(attrs)} atributos, {len(rules)} reglas")
        return 0
    if attrs:
        base = json.loads(ATTR_FILE.read_text("utf-8"))
        ATTR_FILE.write_text(json.dumps(base + attrs, indent=1, ensure_ascii=False) + "\n", "utf-8")
    for x in rules:
        write_rule(REPO_CATALOG, x)
    Catalog.load(REPO_CATALOG)
    print(f"escritos {len(attrs)} atributos y {len(rules)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
