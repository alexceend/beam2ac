"""Reglas de marchas y grupo final para la caja de doble embrague (``dctGearbox``) del
exportador 200409 de Automation (G2.11, 2026-11-02).

    python tools/catalog/add_dct_gearbox_rules.py [--dry-run]

Solo añade (reglas inmutables). No reutiliza ni amplía las reglas ``dt.*_2wd`` (caja
manual o secuencial, FWD/RWD): cada regla se construye aquí con condiciones propias en
lista cerrada:

* ``ir.drivetrain.gearbox.kind == dctGearbox``;
* ``ir.formats.exporter_version in ["200409"]`` (identificador opaco, nunca rangos);
* ``ir.engine.device_type == combustionEngine`` (el único motor visto con DCT).

No hay condición de tracción: las relaciones de la caja y el ``FINAL`` se aplican igual
en ``transmisión 2WD de AC`` y en ``transmisión 4WD de AC`` de AC (``ratio =
finalRatio · relación de la marcha``; G2.11 ``acs_drivetrain/``). ``[TRACTION] TYPE`` y
``[AWD]`` NO se resuelven aquí (la semántica de el cálculo 4WD de AC queda pendiente).

Semántica de origen (Lua del juego, solo lectura;
``src/extract/automatic_transmission.py``):

* ``gearRatios`` se separa por signo como ``manualGearbox`` (dctGearbox.lua l. 417-445):
  ``[R, 0, 1.ª, …]`` → GEAR_R negativa y GEAR_n.
* En régimen estable, con un embrague cerrado: salida = entrada · relación, sin
  pérdidas con ``friction`` = 0 (l. 74-79; ``torqueLossCoef`` no se usa).
* ``FINAL`` = ``gearRatio`` común de los diferenciales de eje con el central 1:1 (lo
  exige el extractor, ``src/extract/drivetrain._awd_layout``).

Lo que NO se traslada (queda sin regla): traspaso entre embragues (0,05-0,5 s según la
agresividad), modos automáticos del controlador, salida con embrague controlado,
igualación de rpm en reducciones y la capacidad de los embragues (``[CLUTCH]
MAX_TORQUE``). Ver ``docs/research/g211_transmissions_2026-11-02.md``.

``GEAR_COUNT`` solo se resuelve con ≤ 8 marchas: el catálogo no tiene ``GEAR_9`` y AC
leería GEAR_9 = 0 (transmisión de AC recorre ``GEAR_%d`` hasta ``COUNT``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402
from src.extract.automatic_transmission import DCT_LUA, GAME_LUA  # noqa: E402

DATE = "2026-11-02"
AUTHOR = "claude (G2.11: transmisiones DCT y automática, encargada por el usuario)"
DOC = "docs/research/g211_transmissions_2026-11-02.md"
EVID = "output/g211_transmissions_2026-11-02"
GAME = "G:/SteamLibrary/steamapps/common/BeamNG.drive/"
EXPORTERS = ["200409"]
MAX_GEARS_IN_CATALOG = 8


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


def cond(fact: str, op: str, value) -> S.Condition:
    return S.Condition(fact=fact, op=op, value=value)


def dct_lua(locator: str) -> S.EvidenceRef:
    return ev("code", GAME + DCT_LUA, locator, GAME_LUA[DCT_LUA])


BASE_CONDITIONS = [
    cond("ir.drivetrain.gearbox.kind", "eq", "dctGearbox"),
    cond("ir.formats.exporter_version", "in", EXPORTERS),
    cond("ir.engine.device_type", "eq", "combustionEngine"),
]
SOURCE_DOC = [
    ev("code", "src/extract/drivetrain.py", "build_drivetrain, _split_gears"),
    ev("code", "src/extract/automatic_transmission.py", "read_dct"),
    dct_lua("l. 417-445 (gearRatios por signo; índice 0 = punto muerto)"),
    dct_lua("l. 53-86 (par de los dos embragues; régimen estable = entrada·relación)"),
]
TARGET_DOC = [
    ev("doc", "sample_assetto/data/drivetrain.ini"),
    ev("doc", "docs/research/transmision.md"),
    ev(
        "code",
        f"{EVID}/acs_drivetrain/c",
        "transmisión de AC (COUNT, GEAR_R, GEAR_%d, FINAL); "
        "transmisión 2WD de AC y el cálculo 4WD de AC "
        "(ratio = finalRatio · relación de la marcha)",
    ),
]
ASSUMPTION = (
    "en régimen estable la DCT es una relación rígida (un embrague cerrado; "
    "dctGearbox.lua l. 74-79) y AC aplica la misma relación (FINAL·marcha); el "
    "traspaso entre embragues (0,05-0,5 s), los modos automáticos y la salida con "
    "embrague controlado de BeamNG no se trasladan"
)


def rule(
    rid: str,
    target: str,
    inputs: dict[str, str],
    extra: list[S.Condition],
    applies_when: list[S.Condition],
    reason: str,
    kind: str = "direct",
    assumptions: list[str] | None = None,
) -> S.Rule:
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind=kind,
        transform=S.Transform(
            id="identity", params={}, input_units={"x": "1"}, output_unit="1"
        ),
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(BASE_CONDITIONS) + extra,
        applies_when=applies_when,
        assumptions=assumptions if assumptions is not None else [ASSUMPTION],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=[
            ev("report", DOC, "§3"),
            ev("report", f"{EVID}/siete_200409.md", "por vehículo"),
        ],
        history=[
            S.HistoryEntry(
                date=DATE,
                author=AUTHOR,
                action="create",
                reason=reason,
                status_from="",
                status_to="provisional",
            )
        ],
        notes="G2.11: solo dctGearbox del exportador 200409; no amplía dt.*_2wd",
    )


def build() -> list[S.Rule]:
    rules = [
        rule(
            "dt.gear_count_dct",
            "ac:drivetrain.ini/GEARS/COUNT",
            {"x": "ir.drivetrain.gearbox.forward_count"},
            [
                cond(
                    "ir.drivetrain.gearbox.forward_count",
                    "le",
                    MAX_GEARS_IN_CATALOG,
                )
            ],
            [],
            "G2.11: nº de marchas hacia delante de la DCT (relaciones ≥ 0 tras el "
            "punto "
            "muerto); limitado a 8 porque el catálogo no tiene GEAR_9",
        ),
        rule(
            "dt.gear_r_dct",
            "ac:drivetrain.ini/GEARS/GEAR_R",
            {"x": "ir.drivetrain.gearbox.reverse_ratio"},
            [],
            [],
            "G2.11: marcha atrás de la DCT (relación negativa, como en AC)",
        ),
        rule(
            "dt.final_dct",
            "ac:drivetrain.ini/GEARS/FINAL",
            {"x": "ir.drivetrain.final_ratio"},
            [],
            [],
            "G2.11: grupo final común de los diferenciales de eje (central 1:1, "
            "exigido por el extractor); AC aplica FINAL·marcha también en el cálculo 4WD de AC",
            assumptions=[
                ASSUMPTION,
                "con tracción total, FINAL = gearRatio común de los dos diferenciales "
                "de eje y el central 1:1 (src/extract/drivetrain._awd_layout); el "
                "reparto entre ejes no lo representa FINAL",
            ],
        ),
    ]
    for n in range(1, MAX_GEARS_IN_CATALOG + 1):
        rules.append(
            rule(
                f"dt.gear_{n}_dct",
                f"ac:drivetrain.ini/GEARS/GEAR_{n}",
                {"x": f"ir.drivetrain.gearbox.forward_ratios.{n}"},
                [],
                [cond("ir.drivetrain.gearbox.forward_count", "ge", n)],
                f"G2.11: relación de la {n}.ª marcha de la DCT",
            )
        )
    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
        if r.key in cat.rules:
            print("ya existe (no se reescribe):", r.key)
            return 1
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
