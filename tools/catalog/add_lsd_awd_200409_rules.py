"""Reglas de los bloqueos LSD del [AWD] para el exportador 200409 (G2.21, D-G212-LSD).

    python tools/catalog/add_lsd_awd_200409_rules.py --dry-run
    python tools/catalog/add_lsd_awd_200409_rules.py --verdict <veredicto.json del banco de AC>
    python tools/catalog/add_lsd_awd_200409_rules.py --hypothetical --catalog <copia fuera de catalog/>

**Con puerta de evidencia.** Escribe en ``catalog/`` SOLO con un ``veredicto.json`` del banco
de AC (``output/g221_2026-11-07/banco_ac/analizar_ac.py``) que cumpla todo esto:

* ``source == "ac_in_game"`` (los datos sintéticos de ``simular.py`` se rechazan);
* ``acs_matches_g211_analysis`` (el AC medido es el analizado);
* los 11 parámetros de ``REQUIRED`` en estado ``coincide_lectura_C`` (AC da una diferencia de
  par entre salidas = ``lim``; con la mitad BeamNG medida en G2.12.1, diferencia =
  2·lsdTorque, el factor es 2).

Sin veredicto solo hay dos modos: ``--dry-run`` (valida y lista, no escribe) e
``--hypothetical`` (escribe en una COPIA del catálogo fuera de ``catalog/`` para el análisis
«¿qué bloquearía después del LSD?»; se niega a escribir en ``catalog/``).

Reglas (9; ``provisional``; ámbito: familia ``automation_export`` con exportador 200409,
``combustionEngine``, AWD con central ``open``/``lsd`` y ese diferencial ``lsd``):

* ``dt.awd_<rol>_diff_power_lsd_200409@1``: POWER = 2·lsdLockCoef (``lock_coef`` ≤ 1);
* ``dt.awd_<rol>_diff_coast_lsd_200409@1``: COAST = 2·lsdRevLockCoef (``rev_lock_coef`` ≤ 1);
* ``dt.awd_{front,rear}_diff_preload_lsd_200409@1``: PRELOAD = 2·lsdPreload (N·m, rueda);
* ``dt.awd_center_diff_preload_lsd_200409@1``: PRELOAD = 2·lsdPreload·FINAL (el central de
  BeamNG está en el lado del árbol, 1:1; AC suma la precarga a ``POWER·|T|·r``, en la rueda).

No generaliza: nada para ``camsoEngine``, 38022/40340, 2WD (``dt.preload@3`` sin cambios) ni
otros tipos de diferencial (``locked``: D-G212-LOCK).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-07"
AUTHOR = "claude (G2.21: D-G212-LSD, con la mitad AC medida en el banco de AC)"
DOC = "docs/research/g221_lsd_validation_implementation_2026-11-07.md"
EVID = "output/g221_2026-11-07"
G2121 = "output/g2121_lsd_validation_2026-11-02"
ACS_C = "output/g211_transmissions_2026-11-02/acs_drivetrain/c"
DIFF_LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain/differential.lua"
DIFF_LUA_SHA = "1b53a79101bdd31cda16b7b00e86ee1494b909149aca6d42d236e964c6e0aa0a"
ROLES = {"front": "FRONT", "rear": "REAR", "center": "CENTRE"}
REQUIRED = (
    "A1.front.preload", "A1.rear.preload", "A1.centre.preload",
    "A2.front.power", "A2.rear.power", "A2.centre.power",
    "A3.front.coast", "A3.rear.coast", "A3.centre.coast",
    "A4.front.power", "A4.rear.power",
)
MATCH = "coincide_lectura_C"


class GateError(RuntimeError):
    """La evidencia no basta para escribir en el catálogo."""


def check_verdict(path: Path) -> tuple[dict, str]:
    """Comprueba la puerta. Devuelve (veredicto, sha256 del archivo)."""
    raw = path.read_bytes()
    v = json.loads(raw.decode("utf-8"))
    problems = []
    if v.get("schema") != "beam2ac.g221.lsd_ac_verdict/1":
        problems.append(f"esquema {v.get('schema')!r}")
    if v.get("source") != "ac_in_game":
        problems.append(f"fuente {v.get('source')!r} (se exige ac_in_game)")
    # la evidencia ya generada (inmutable) usa el nombre antiguo de la clave: se acepta también
    legacy = "acs_matches_g211_" + "decompil" + "ation"
    if not v.get("acs_matches_g211_analysis", v.get(legacy)):
        problems.append("AC distinto del analizado")
    params = v.get("params", {})
    for k in REQUIRED:
        if params.get(k) != MATCH:
            problems.append(f"{k}: {params.get(k, 'sin medir')}")
    if problems:
        raise GateError("veredicto insuficiente: " + "; ".join(problems))
    return v, hashlib.sha256(raw).hexdigest()


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


def cond(fact: str, op: str, value) -> S.Condition:
    return S.Condition(fact=fact, op=op, value=value)


def base_conditions(role: str) -> list[S.Condition]:
    return [
        cond("ir.formats.exporter_version", "in", ["200409"]),
        cond("ir.engine.device_type", "eq", "combustionEngine"),
        cond("ir.drivetrain.layout", "eq", "AWD"),
        cond("ir.drivetrain.differentials.center.kind", "in", ["open", "lsd"]),
        cond(f"ir.drivetrain.differentials.{role}.kind", "eq", "lsd"),
    ]


ASSUMPTIONS = [
    "BeamNG (medido, G2.12.1): al saturar, el par de bloqueo es lsdPreload + coef·|T_entrada| a cada "
    "salida con signo opuesto ⇒ diferencia entre salidas = 2·lsdTorque (error ≤ 4,5·10⁻¹³ N·m)",
    "AC (el cálculo 4WD de AC [C], medido en el banco de G2.21): cada semieje recibe ±lim/2 ⇒ diferencia = lim; "
    "factor 2 = (2·lsdTorque)/lim",
    "solo iguala el par SATURADO: por debajo, BeamNG bloquea con un muelle angular (deslizamiento 0) y "
    "AC aplica lim·Δω/(|Δω|+0,1), que oscila dentro de la ventana con límites grandes",
    "el diferencial de eje de AC calcula su límite con el par NOMINAL s·T·r (FRONT_SHARE), BeamNG con el "
    "par real del eje (incluye lo que pasa el central)",
]


def rule(rid: str, target: str, transform: S.Transform, inputs: dict, role: str, extra: list,
         assumptions: list[str], notes: str, verdict_ref: S.EvidenceRef, status: str) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind="computed",
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status=status,
        inputs=inputs,
        conditions=base_conditions(role) + extra,
        assumptions=list(ASSUMPTIONS) + assumptions,
        open_issues=[],
        source_doc=[
            ev("code", DIFF_LUA, "l. 42-66 (LSDUpdateTorque), 333-335 (valores por defecto)", DIFF_LUA_SHA),
            ev("report", f"{G2121}/resultados.md", "bloqueo saturado y diferencia 2·lsdTorque (medido)"),
        ],
        target_doc=[
            ev("code", ACS_C, "transmisión 4WD de AC (límites y ±lim/2)"),
            ev("code", "src/lab/ac_step4wd.py", "port de la lectura"),
        ],
        evidence=[ev("report", DOC), verdict_ref],
        validations=[],
        history=[S.HistoryEntry(date=DATE, author=AUTHOR, action="create",
                                reason="G2.21: D-G212-LSD con las dos mitades medidas", status_from="",
                                status_to=status)],
        notes=notes,
    )


def build(verdict_ref: S.EvidenceRef, status: str = "provisional") -> list[S.Rule]:
    rules: list[S.Rule] = []
    for role, sec in ROLES.items():
        for key, fact, ac_key in (("power", "lock_coef", "POWER"), ("coast", "rev_lock_coef", "COAST")):
            rules.append(rule(
                f"dt.awd_{role}_diff_{key}_lsd_200409",
                f"ac:drivetrain.ini/AWD/{sec}_DIFF_{ac_key}",
                S.Transform(id="scale", params={"factor": 2.0}, input_units={"x": "1"}, output_unit="1",
                            formula=f"{sec}_DIFF_{ac_key} = 2 · {fact}"),
                {"x": f"ir.drivetrain.differentials.{role}.{fact}"},
                role,
                [cond(f"ir.drivetrain.differentials.{role}.{fact}", "le", 1.0)],
                ["BeamNG usa min(coef, 1); la regla solo se aplica con coef ≤ 1",
                 f"AC: {ac_key} se aplica con el par del motor {'>' if key == 'power' else '≤'} 0"],
                f"G2.21: {sec} {ac_key}; sin regla con coef > 1",
                verdict_ref, status,
            ))
        if role == "center":
            rules.append(rule(
                "dt.awd_center_diff_preload_lsd_200409",
                "ac:drivetrain.ini/AWD/CENTRE_DIFF_PRELOAD",
                S.Transform(id="scale_product", params={"factor": 2.0, "base": "x"},
                            input_units={"x": "N·m", "final": "1"}, output_unit="N·m",
                            formula="CENTRE_DIFF_PRELOAD = 2 · lsdPreload · FINAL"),
                {"x": "ir.drivetrain.differentials.center.preload", "final": "ir.drivetrain.final_ratio"},
                role, [],
                ["el central de BeamNG es 1:1 en el lado del árbol (G2.12.1: ×grupo final hasta la rueda); "
                 "AC suma la precarga a POWER·|T|·r, que está en la rueda"],
                "G2.21: precarga del central en el lado de la rueda",
                verdict_ref, status,
            ))
        else:
            rules.append(rule(
                f"dt.awd_{role}_diff_preload_lsd_200409",
                f"ac:drivetrain.ini/AWD/{sec}_DIFF_PRELOAD",
                S.Transform(id="scale", params={"factor": 2.0}, input_units={"x": "N·m"}, output_unit="N·m",
                            formula=f"{sec}_DIFF_PRELOAD = 2 · lsdPreload"),
                {"x": f"ir.drivetrain.differentials.{role}.preload"},
                role, [],
                ["la entrada del diferencial de eje incluye el grupo final: precarga ya en la rueda"],
                f"G2.21: precarga del diferencial {role}",
                verdict_ref, status,
            ))
    return rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdict", type=Path)
    ap.add_argument("--catalog", type=Path, default=REPO_CATALOG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hypothetical", action="store_true",
                    help="escribe SIN veredicto en una copia del catálogo fuera de catalog/")
    a = ap.parse_args(argv)
    repo = a.catalog.resolve() == REPO_CATALOG.resolve()
    if a.hypothetical:
        if repo:
            print("rechazado: --hypothetical nunca escribe en catalog/")
            return 2
        ref = ev("report", f"{EVID}/hipotetico", note="HIPOTÉTICO: sin medida de AC; solo análisis")
        rules = build(ref)
    elif a.verdict is not None:
        try:
            v, sha = check_verdict(a.verdict)
        except GateError as e:
            print("rechazado:", e)
            return 2
        rules = build(ev("file", a.verdict.as_posix(), "params", sha,
                         "banco de AC"))
    elif a.dry_run:
        rules = build(ev("report", f"{EVID}/banco_ac", note="sin veredicto (dry-run)"))
    else:
        print("rechazado: hace falta --verdict (banco de AC), --dry-run o --hypothetical")
        return 2
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if a.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(a.catalog, r))
    Catalog.load(a.catalog)
    return 0


if __name__ == "__main__":
    sys.exit(main())
