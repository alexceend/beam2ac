"""Reglas de tracción total para el exportador 200409 (G2.12, 2026-11-02).

    python tools/catalog/add_awd_200409_rules.py [--dry-run]

Encargada por el usuario en G2.12. Solo añade; nunca sobrescribe (reglas inmutables). No
amplía ninguna regla anterior (``dt.traction@3`` sigue limitada a 38022/40340 y FWD/RWD).
Todas las reglas nuevas llevan las condiciones:

* ``ir.formats.exporter_version in ["200409"]`` (lista cerrada; nunca rangos);
* ``ir.engine.device_type == combustionEngine`` (dispositivo);
* ``ir.drivetrain.layout == AWD`` (arquitectura);
* ``ir.drivetrain.differentials.center.kind in ["open", "lsd"]``: central con reparto FIJO
  (``differential.lua`` l. 37-39 y 64-65). Un central ``locked`` (0,5/0,5 rígido, l. 98-99)
  queda fuera: AC no tiene central rígido (``acs_awd_analisis.md`` §2.6).

Reglas (``provisional``: equivalencia leída en el código de los dos juegos, sin prueba en
AC; ver ``docs/research/g212_awd_2026-11-02.md`` §5):

* ``dt.traction_awd@1``: ``[TRACTION] TYPE = AWD`` (el cálculo 4WD de AC: diferencial central con
  reparto fijo y tres diferenciales). ``AWD2`` es tracción trasera con acoplamiento
  delantero (la variante 4WD nueva de AC): no equivale a ninguno de los 7.
* ``dt.awd_front_share@1``: ``[AWD] FRONT_SHARE`` (%) = reparto delantero del central
  (``drivetrain.front_torque_share``; salida 1 = ``diffTorqueSplit``). En el cálculo 4WD de AC el par
  del motor llega a los semiejes delanteros como ``2·s·Δω`` y a los traseros como
  ``2·(1−s)·Δω`` con las cuatro inercias igualadas por ``loadINI``: reparto de par ``s``.
* ``dt.awd_<rol>_diff_<clave>_open@1`` (9): POWER, COAST y PRELOAD = 0 en un diferencial
  ``open``. En BeamNG ``open`` no transfiere par entre salidas (el término ``openTorque``
  de l. 36-39 se resta de las dos con el mismo signo); en AC límite 0 ⇒ sin corrección.
  El resultado no depende del factor de escala (sin resolver) entre ``lsdPreload`` y
  PRELOAD.

NO se añaden reglas para los bloqueos de un ``lsd`` (los 9 valores quedan en
``manual_required``; propuesta ×2 documentada, decisión D-G212-LSD) ni para un central
``locked`` (D-G212-LOCK).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-02"
AUTHOR = "claude (G2.12: tracción total del exportador 200409, encargada por el usuario)"
DOC = "docs/research/g212_awd_2026-11-02.md"
EVID = "output/g212_awd_2026-11-02"
ACS_C = "output/g211_transmissions_2026-11-02/acs_drivetrain/c"
DIFF_LUA = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain/differential.lua"
DIFF_LUA_SHA = "1b53a79101bdd31cda16b7b00e86ee1494b909149aca6d42d236e964c6e0aa0a"
EXPORTERS = ["200409"]
ROLES = {"front": "FRONT", "rear": "REAR", "center": "CENTRE"}
KEYS = {"power": ("POWER", "1"), "coast": ("COAST", "1"), "preload": ("PRELOAD", "N·m")}


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


def cond(fact: str, op: str, value) -> S.Condition:
    return S.Condition(fact=fact, op=op, value=value)


BASE_CONDITIONS = [
    cond("ir.formats.exporter_version", "in", EXPORTERS),
    cond("ir.engine.device_type", "eq", "combustionEngine"),
    cond("ir.drivetrain.layout", "eq", "AWD"),
    cond("ir.drivetrain.differentials.center.kind", "in", ["open", "lsd"]),
]
SOURCE_DOC = [
    ev("code", DIFF_LUA, "l. 315-317 (diffTorqueSplit), 37-39 (open), 64-65 (lsd), 98-99 (locked)", DIFF_LUA_SHA),
    ev("code", "src/extract/traction.py", "classify_traction, awd_axle_roles"),
    ev("report", f"{EVID}/beamng_awd_analisis.md", "§1-§2"),
]
TARGET_DOC = [
    ev("code", ACS_C, "transmisión de AC ([TRACTION] TYPE, [AWD], igualación de inercias)"),
    ev("code", ACS_C, "transmisión 4WD de AC; el bloque de aceleración de la transmisión de AC"),
    ev("report", f"{EVID}/acs_awd_analisis.md", "§1-§3"),
    ev("report", "docs/research/g212_ac_reference_corpus_2026-11-02.md", "§4 (7 AWD, 6 AWD2 en el corpus)"),
]


def rule(rid: str, target: str, transform: S.Transform, inputs: dict, extra: list, assumptions: list[str],
         reason: str, notes: str) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind="computed",
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(BASE_CONDITIONS) + extra,
        assumptions=assumptions,
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=[ev("report", DOC), ev("report", f"{EVID}/siete_awd.md", "por vehículo")],
        validations=[],
        history=[
            S.HistoryEntry(date=DATE, author=AUTHOR, action="create", reason=reason, status_from="",
                           status_to="provisional")
        ],
        notes=notes,
    )


def build() -> list[S.Rule]:
    rules = [
        rule(
            "dt.traction_awd",
            "ac:drivetrain.ini/TRACTION/TYPE",
            S.Transform(id="identity", params={}, input_units={"x": "enum"}, output_unit="enum"),
            {"x": "ir.drivetrain.layout"},
            [],
            [
                "TYPE=AWD (el cálculo 4WD de AC) = diferencial central con reparto fijo y bloqueo POWER/COAST/PRELOAD, "
                "más un diferencial por eje; es la arquitectura de origen (central open/lsd 1:1). AWD2 "
                "(la variante 4WD nueva de AC) es tracción trasera con acoplamiento delantero por deslizamiento, sin reparto fijo",
                "con TYPE=AWD, loadINI iguala la inercia de las 4 ruedas a la media delantera/trasera "
                "(pérdida de fidelidad si difieren)",
            ],
            "G2.12: el cálculo 4WD de AC analizado; el central open/lsd de BeamNG reparte el par de forma fija como el cálculo 4WD de AC",
            "G2.12: solo 200409 + combustionEngine + AWD con central open/lsd; no amplía dt.traction@3",
        ),
        rule(
            "dt.awd_front_share",
            "ac:drivetrain.ini/AWD/FRONT_SHARE",
            S.Transform(id="identity", params={}, input_units={"x": "1"}, output_unit="%",
                        formula="FRONT_SHARE[%] = 100 · diffTorqueSplit(salida delantera del central)"),
            {"x": "ir.drivetrain.front_torque_share"},
            [],
            [
                "BeamNG: salida delantera del central = T·A (open, lsd), A = diffTorqueSplit; AC el cálculo 4WD de AC: "
                "el bloque de aceleración de la transmisión de AC da 2·s·Δω a los semiejes delanteros y 2·(1−s)·Δω a los traseros "
                "con inercias iguales ⇒ reparto del par del motor s; s = FRONT_SHARE·0,01 (loadINI)",
                "en AC FRONT_SHARE también fija el par nominal de cada eje con el que se calcula el límite "
                "de su diferencial (s·T·r y (1−s)·T·r); BeamNG usa el par real del eje (incluye el bloqueo "
                "del central)",
            ],
            "G2.12: semántica de FRONT_SHARE leída en el cálculo 4WD de AC/el bloque de aceleración de la transmisión de AC/loadINI",
            "G2.12: no es un valor del corpus; es el diffTorqueSplit del vehículo",
        ),
    ]
    for role, sec in ROLES.items():
        for key, (ac_key, unit) in KEYS.items():
            rules.append(
                rule(
                    f"dt.awd_{role}_diff_{key}_open",
                    f"ac:drivetrain.ini/AWD/{sec}_DIFF_{ac_key}",
                    S.Transform(id="constant", params={"value": 0}, input_units={}, output_unit=unit,
                                formula="diferencial open: sin par de bloqueo entre salidas"),
                    {},
                    [cond(f"ir.drivetrain.differentials.{role}.kind", "eq", "open")],
                    [
                        "BeamNG open (differential.lua l. 24-40): salidas T·A y T·(1−A); el término "
                        "openTorque = 0,034·|T|·clamp(Δω, ±0,1) se resta de las dos con el mismo signo "
                        "(pérdida simétrica ≤ 0,34 % del par, no acopla las salidas) y no tiene "
                        "equivalente en AC (sin pérdidas)",
                        "AC el cálculo 4WD de AC: con POWER = COAST = PRELOAD = 0 el límite es 0 y no hay corrección "
                        "entre salidas (diferencial abierto); independiente del factor de escala "
                        "lsdPreload→PRELOAD, que sigue sin validar",
                    ],
                    "G2.12: diferencial open ⇒ bloqueo nulo en AC",
                    f"G2.12: {sec}_DIFF_{ac_key} = 0 solo con differentials.{role}.kind == open",
                )
            )
    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
