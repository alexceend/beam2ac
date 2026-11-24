"""Reglas ``[COAST_REF]`` de ``combustionEngine`` del 200409 (G2.24, 2026-11-09).

    python tools/catalog/add_coast_ref_combustion_rules.py [--version 1|2] [--dry-run]

Decisión del usuario en G2.24 sobre D-G29c (G2.10 §3, §10.3): «anclar en el techo».
Antes no había regla: el exportador dejaba ``engine.ini`` con
``COAST_CURVE=FROM_COAST_REF`` y sin ``[COAST_REF]``. motor de AC lee
entonces RPM = TORQUE = NON_LINEARITY = 0 ⇒ ``coast1 = coast2 = 0`` ⇒ freno motor nulo
(AC, ``output/g210_d_g29_decisions_2026-11-02/acs_ini/``), y en el juego las rpm no
bajaban al soltar el gas (prueba del usuario de los exports G2.23).

Reglas (``provisional``; solo añade, nunca sobrescribe):

* ``eng.coast_rpm_combustion@1``, ``eng.coast_torque_combustion@1``,
  ``eng.coast_nonlinearity_combustion@1``: transformación ``ac_coast_ref`` con
  ``law = acs_from_minimum`` sobre ``ir.engine.coast_curve`` (``computed``,
  friction + dynamicFriction·ω + engineBrakeTorque), ``ir.engine.speed_ceiling`` e
  ``ir.engine.idle_speed``. Ancla = último punto de la curva ≤ techo (el mismo método
  que ``eng.coast_*@2`` del Ibiza); NON_LINEARITY = 0.
* Error conocido y aceptado por el usuario: AC frena 0 en MINIMUM y BeamNG 54-77 N·m
  (el error de cada vehículo queda en los supuestos de la resolución).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402
from src.extract.engine_combustion import GAME_LUA, LUA  # noqa: E402

DATE = "2026-11-09"
AUTHOR = "claude (G2.24: decisión del usuario sobre D-G29c, «anclar en el techo»)"
DOC = "docs/research/g224_diagnostico_exports_ac_2026-11-09.md"
G210 = "docs/research/g210_d_g29_decisions_2026-11-02.md"
ACS = "output/g210_d_g29_decisions_2026-11-02/acs_ini"
ACS_SHA = ""
GAME = "G:/SteamLibrary/steamapps/common/BeamNG.drive/"
EXPORTERS = ["200409"]
OUTPUTS = {
    "rpm": ("eng.coast_rpm_combustion", "ac:engine.ini/COAST_REF/RPM", "rpm", None),
    "torque": (
        "eng.coast_torque_combustion",
        "ac:engine.ini/COAST_REF/TORQUE",
        "N·m",
        1,
    ),
    "non_linearity": (
        "eng.coast_nonlinearity_combustion",
        "ac:engine.ini/COAST_REF/NON_LINEARITY",
        "1",
        None,
    ),
}


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


def acs(locator: str, file: str) -> S.EvidenceRef:
    return ev("code", f"{ACS}/{file}", locator, note="AC")


def build() -> list[S.Rule]:
    out = []
    for output, (rid, target, unit, digits) in OUTPUTS.items():
        params: dict = {"output": output, "law": "acs_from_minimum"}
        if digits is not None:
            params["round"] = digits
        out.append(
            S.Rule(
                id=rid,
                version=1,
                target=target,
                kind="computed",
                transform=S.Transform(
                    id="ac_coast_ref",
                    params=params,
                    input_units={
                        "curve": "table:rpm->N·m",
                        "ceiling": "rpm",
                        "minimum": "rpm",
                    },
                    output_unit=unit,
                    formula=(
                        "ac_coast_ref (src/ac/coast_ref.py coast_ref_from_minimum): "
                        "RPM, TORQUE = último punto (r, t) de la curva con r ≤ techo; "
                        "NON_LINEARITY = 0; AC aplica freno(rpm) = TORQUE·(rpm − "
                        "MINIMUM)/(RPM − MINIMUM) para rpm > MINIMUM y 0 por debajo "
                        "(motor de AC)"
                    ),
                ),
                scope=S.Scope(level="family", family="automation_export"),
                status="provisional",
                inputs={
                    "curve": "ir.engine.coast_curve",
                    "ceiling": "ir.engine.speed_ceiling",
                    "minimum": "ir.engine.idle_speed",
                },
                conditions=[
                    S.Condition(
                        fact="ir.engine.device_type", op="eq", value="combustionEngine"
                    ),
                    S.Condition(
                        fact="ir.formats.exporter_version", op="in", value=EXPORTERS
                    ),
                    S.Condition(
                        fact="ir.engine.rev_limiter_mode", op="eq", value="activo"
                    ),
                ],
                assumptions=[
                    "freno motor de AC con [COAST_REF] y "
                    "NON_LINEARITY = 0: lineal desde "
                    "MINIMUM (0 en MINIMUM y por debajo) hasta "
                    "TORQUE en RPM (AC); "
                    "anclado en el freno de BeamNG en el techo "
                    "de rpm (mismo método que "
                    "eng.coast_*@2 del Ibiza)",
                    "error conocido y aceptado por el usuario (G2.24): BeamNG ya frena "
                    "friction + engineBrakeTorque + dynamicFriction·ω en el ralentí "
                    "(54-77 N·m en los 200409) y AC 0; el error "
                    "decrece linealmente hasta "
                    "0 en el techo. Ningún "
                    "RPM/TORQUE/NON_LINEARITY lo elimina en MINIMUM "
                    "(G2.10 §3, D-G29c)",
                    "MINIMUM = idle_speed truncado (getInt), el mismo valor que AC usa "
                    "con eng.minimum_combustion@2",
                ],
                open_issues=[
                    "COAST_CURVE desde archivo y "
                    "[COAST_SETTINGS] (gasCoastOffsetCurve) "
                    "de AC sin analizar: podrían "
                    "representar el freno en el ralentí"
                ],
                source_doc=[
                    ev("code", "src/extract/engine_combustion.py", "coast_curve"),
                    ev("code", "src/importers/beamng.py", "engine.coast_curve"),
                    ev(
                        "code",
                        GAME + LUA,
                        "l. 731-806 updateTorque (freno motor)",
                        GAME_LUA[LUA],
                    ),
                ],
                target_doc=[
                    ev("doc", "sample_assetto/data/engine.ini", "comentarios de Kunos"),
                    acs(
                        "motor de AC (COAST_CURVE=FROM_COAST_REF)",
                        "",
                    ),
                    acs(
                        "motor de AC",
                        "",
                    ),
                    acs("motor de AC", ""),
                ],
                evidence=[
                    ev("report", DOC, "§4 (problema C)"),
                    ev("report", G210, "§3 D-G29c"),
                    ev(
                        "report",
                        "output/g224_2026-11-09/diagnostico/coast_error.py",
                        "perfil de error por vehículo",
                    ),
                ],
                validations=[],
                history=[
                    S.HistoryEntry(
                        date=DATE,
                        author=AUTHOR,
                        action="create",
                        reason=(
                            "sin [COAST_REF] AC aplica freno motor 0 "
                            "(AC) y las rpm "
                            "no bajan (prueba del usuario, G2.23); el usuario elige el "
                            "ajuste anclado en el techo con el error en el ralentí "
                            "documentado"
                        ),
                        status_from="",
                        status_to="provisional",
                    )
                ],
                notes="solo con limitador activo: el techo es entonces el LIMITER de "
                "eng.limiter_combustion@2",
            )
        )
    return out


def build_v2(cat: Catalog) -> list[S.Rule]:
    """@2: el ``open_issue`` de la @1 (COAST_CURVE desde archivo y [COAST_SETTINGS] sin
    analizar) es una línea de investigación futura, no un problema de esta regla; como
    ``open_issue`` el motor la marca ``ambiguous`` y nunca se aplica (los 4 exports de
    G2.24, primer intento). Pasa a ``notes``; todo lo demás igual."""
    out = []
    for rid, _target, _unit, _digits in OUTPUTS.values():
        old = cat.rules[f"{rid}@1"]
        new = S.from_dict(S.to_dict(old))
        new.version, new.supersedes = 2, old.key
        new.notes = (
            old.notes + "; línea futura (no bloquea): " + "; ".join(old.open_issues)
        )
        new.open_issues = []
        new.history = list(old.history) + [
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="new_version",
                reason="el open_issue de la @1 era una alternativa sin analizar, no un "
                "defecto de la regla, y la dejaba ambigua (nunca aplicada); pasa a "
                "notes; transformación, entradas, ámbito, condiciones y supuestos sin "
                "cambios",
                status_from=old.status,
                status_to=old.status,
            )
        ]
        out.append(new)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--version", type=int, choices=[1, 2], default=1)
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build() if args.version == 1 else build_v2(cat)
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
        if r.key in cat.rules:
            raise SystemExit(f"{r.key} ya existe: las reglas no se sobrescriben")
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
