"""D-G212-LOCK (G2.44, 2026-11-14): central ``locked`` de los 200409 (patagonia, atacama).

    python tools/catalog/add_awd_locked_center_200409_rules.py [--dry-run]

NO volver a ejecutar tras escribir las reglas (reglas inmutables).

Derivación (``docs/research/g244_central_bloqueado_2026-11-14.md``):

* BeamNG ``lockedUpdateTorque`` (differential.lua l. 86-100): reparto FIJO 0,5/0,5 (no usa
  ``diffTorqueSplit``) y par de bloqueo ±lockTorque con muelle angular. Sin ``lockTorque``
  declarado (los 7 del 200409 no lo declaran) ``lockTorque = stabilityCoef²·min(inercia de
  las salidas)`` (l. 235-238, ``stabilityCoef = 250``): del orden de 10⁴ N·m, es decir,
  rígido en la práctica.
* AC el cálculo 4WD de AC [C]: central con ``lim = PRELOAD + coef·|T|·r`` y corrección ``sat``
  explícita; un ``lim`` grande oscila dentro de la ventana con amplitud pico-pico
  ``dt·lim/ΣI``. No existe un valor que dé Δω = 0 sin oscilar (``estabilidad.md`` de G2.19).
* Decisión: ``CENTRE_DIFF_PRELOAD = chatter·ΣI/dt`` (amplitud admitida 1 rad/s en rueda,
  g = 10; los 4 AWD con central ``lsd`` validados en AC, G2.25, trabajaron hasta g ≈ 17),
  ``POWER = COAST = 0`` (el bloqueo de BeamNG no depende del par), reparto 50 %.
* Ejes: ``diffType ["open","locked"]`` ⇒ ``mode = lista[1] = open`` (l. 307-309); el modo
  ``locked`` es conmutable en el juego y no se representa ⇒ POWER = COAST = PRELOAD = 0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "catalog"))

import add_awd_200409_rules as base  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-14"
AUTHOR = "claude (G2.44: D-G212-LOCK, decisión autónoma de la misión de desbloqueo)"
DOC = "docs/research/g244_central_bloqueado_2026-11-14.md"
CENTER = "ir.drivetrain.differentials.center"
CHATTER = 1.0  # rad/s pico-pico admitidos en rueda
cond = base.cond

LOCKED_CENTER = [
    cond("ir.formats.exporter_version", "in", base.EXPORTERS),
    cond("ir.engine.device_type", "eq", "combustionEngine"),
    cond("ir.drivetrain.layout", "eq", "AWD"),
    cond(f"{CENTER}.initial_mode", "eq", "locked"),
    cond(f"{CENTER}.lock_torque", "absent", None),
]
SOURCE_DOC = base.SOURCE_DOC + [
    base.ev(
        "code",
        base.DIFF_LUA,
        "l. 86-100 (locked), 235-238 (lockTorque automático), 307-309 (lista de modos)",
        base.DIFF_LUA_SHA,
    ),
    base.ev(
        "code",
        "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain.lua",
        "l. 10: stabilityCoef = 250",
    ),
]
TARGET_DOC = base.TARGET_DOC + [
    base.ev(
        "code",
        "output/g219_2026-11-06/central/estabilidad.py",
        "oscilación del central de AC con lim grande",
    ),
    base.ev("code", "src/lab/ac_step4wd.py", "port de el cálculo 4WD de AC"),
]
AX_NOTE = (
    "diffType es una lista ['open','locked']: BeamNG arranca en el primer modo (open) y "
    "deja el resto como modos conmutables en el juego (differential.lua l. 307-309); AC "
    "no tiene ese selector ⇒ se representa el modo inicial"
)


def rule(rid, target, transform, inputs, extra, assumptions, reason):
    return S.Rule(
        id=rid,
        version=1,
        target=target,
        kind="computed",
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(LOCKED_CENTER) + extra,
        assumptions=assumptions,
        open_issues=[],
        source_doc=list(SOURCE_DOC),
        target_doc=list(TARGET_DOC),
        evidence=[base.ev("report", DOC)],
        validations=[],
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
        notes="G2.44: central locked de los 200409; aproximación, no validada en AC",
    )


def build() -> list[S.Rule]:
    lock = [
        "BeamNG: el central locked mantiene Δω ≈ 0 hasta lockTorque (≈10⁴ N·m, automático): "
        "rígido en la práctica",
        "AC no tiene central rígido: con lim grande el cálculo 4WD de AC oscila (amplitud pico-pico ≈ "
        "dt·lim/ΣI); se acota a 1 rad/s en rueda. El par que AC puede pasar entre ejes "
        "queda limitado a ese lim: PÉRDIDA respecto a BeamNG",
    ]
    r = [
        rule(
            "dt.traction_awd_lockc",
            "ac:drivetrain.ini/TRACTION/TYPE",
            S.Transform(
                id="identity", params={}, input_units={"x": "enum"}, output_unit="enum"
            ),
            {"x": "ir.drivetrain.layout"},
            [],
            lock[:1]
            + [
                "TYPE=AWD (el cálculo 4WD de AC) con central de límite finito es la única arquitectura "
                "de 4 ruedas medida en AC (G2.22); AWD2 es tracción trasera con "
                "acoplamiento y no equivale"
            ],
            "G2.44 D-G212-LOCK: TYPE=AWD para central locked",
        ),
        rule(
            "dt.awd_front_share_lockc",
            "ac:drivetrain.ini/AWD/FRONT_SHARE",
            S.Transform(
                id="identity",
                params={},
                input_units={"x": "1"},
                output_unit="%",
                formula="FRONT_SHARE[%] = 100 · 0,5 (lockedUpdateTorque: inputTorque·0,5)",
            ),
            {"x": "ir.drivetrain.front_torque_share"},
            [cond("ir.drivetrain.front_torque_share", "eq", 0.5)],
            [
                "differential.lua l. 98-99: el locked reparte el par de entrada 0,5/0,5 y "
                "suma ±lockTorque; diffTorqueSplit no se usa (la condición exige 0,5 para "
                "no perder un reparto declarado)",
                "en AC FRONT_SHARE es el reparto nominal; el central limitado solo "
                "transporta la desviación",
            ],
            "G2.44 D-G212-LOCK: reparto 50 %",
        ),
        rule(
            "dt.awd_center_diff_preload_lockc",
            "ac:drivetrain.ini/AWD/CENTRE_DIFF_PRELOAD",
            S.Transform(
                id="ac_center_lock_cap",
                params={"chatter": CHATTER},
                input_units={"i_front": "kg·m²", "i_rear": "kg·m²"},
                output_unit="N·m",
                formula="CENTRE_DIFF_PRELOAD = chatter · 2·(I_del + I_tras) / dt  (dt = 1/333 s)",
            ),
            {
                "i_front": "ir.tyres.axles.front.rotational_inertia",
                "i_rear": "ir.tyres.axles.rear.rotational_inertia",
            },
            [],
            lock,
            "G2.44 D-G212-LOCK: precarga del central acotada por la oscilación admitida",
        ),
    ]
    for key, ac in (("power", "POWER"), ("coast", "COAST")):
        r.append(
            rule(
                f"dt.awd_center_diff_{key}_lockc",
                f"ac:drivetrain.ini/AWD/CENTRE_DIFF_{ac}",
                S.Transform(
                    id="constant",
                    params={"value": 0},
                    input_units={},
                    output_unit="1",
                    formula="el bloqueo de BeamNG no depende del par de entrada",
                ),
                {},
                [],
                lock[:1]
                + [
                    "con coef > 0 el lim crecería con el par y superaría el tope de "
                    "oscilación; el límite se fija solo con PRELOAD"
                ],
                f"G2.44 D-G212-LOCK: CENTRE_DIFF_{ac} = 0",
            )
        )
    for role, sec in (("front", "FRONT"), ("rear", "REAR")):
        for key, (ac, unit) in base.KEYS.items():
            r.append(
                rule(
                    f"dt.awd_{role}_diff_{key}_openmode",
                    f"ac:drivetrain.ini/AWD/{sec}_DIFF_{ac}",
                    S.Transform(
                        id="constant",
                        params={"value": 0},
                        input_units={},
                        output_unit=unit,
                        formula="diferencial en modo inicial open: sin par de bloqueo",
                    ),
                    {},
                    [cond(f"ir.drivetrain.differentials.{role}.initial_mode", "eq", "open")],
                    [
                        AX_NOTE,
                        "open no transfiere par entre salidas (como dt.awd_*_diff_*_open@1)",
                    ],
                    f"G2.44 D-G212-LOCK: {sec}_DIFF_{ac} = 0 (modo inicial open)",
                )
            )
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    rules = build()
    for x in rules:
        S.from_dict(S.to_dict(x))
    if args.dry_run:
        for x in rules:
            print("ok (sin escribir):", x.key)
        return 0
    for x in rules:
        print("escrita:", write_rule(REPO_CATALOG, x))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
