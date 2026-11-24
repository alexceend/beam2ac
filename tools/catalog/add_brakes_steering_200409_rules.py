"""Reglas de frenos y dirección del exportador 200409 (G2.15, 2026-11-03).

    python tools/catalog/add_brakes_steering_200409_rules.py [--dry-run]

Encargada por el usuario en G2.15. Solo añade; nunca sobrescribe (reglas inmutables). No
amplía ninguna regla anterior: ``brk.front_share@2`` sigue limitada al Ibiza y los
exportadores 38022/40340 no reciben nada nuevo. Todas llevan
``ir.formats.exporter_version in ["200409"]`` (lista cerrada).

Semántica de AC (
``output/g215_direction_brakes_aero_2026-11-03/acs_dir_frenos_aero/``) [C]:

* frenos de AC: P = MAX_TORQUE·multiplicador(1)·pedal; cada rueda delantera
  P·FRONT_SHARE; cada trasera max(0, (1 − FRONT_SHARE)·P − corrección(0)); cada trasera
  recibe HANDBRAKE_TORQUE·freno de mano (completo, no repartido). Lineal: sin codo.
* coche de AC + dirección de AC: señal = STEER_LOCK·volante/STEER_RATIO; el punto
  de bieleta del chasis se desplaza −señal·LINEAR_STEER_ROD_RATIO. STEER_LOCK = grados de
  VOLANTE con el mando a tope (también escala el volante gráfico, el ángulo gráfico del volante).

Semántica de BeamNG (``wheels.lua`` 1eaf8b31…, ``hydros.lua`` 20893345…, ``input.lua``
25438ea9…) [C]: par de freno por rueda = brakeTorque·(min(e, s) + c·max(e − s, 0)) ×
eficiencia térmica; freno de mano = max(principal, parkingTorque·entrada) por rueda;
volante = s·steeringWheelLock.

Reglas (``provisional``):

* ``brk.max_torque_200409@1`` (``computed``, ``linear_sum``): MAX_TORQUE = T_rueda_del +
  T_rueda_tras (por rueda, pedal 1). Exacto en todo el recorrido del pedal solo sin codo
  (``input_split`` = 1 en los dos ejes, condición).
* ``brk.front_share_200409@1`` (``computed``, identidad): FRONT_SHARE =
  ``ir.brakes.front_share_full_pedal`` = T_del/(T_del + T_tras); misma condición.
* ``brk.handbrake_torque_200409@1`` (``direct``, identidad): HANDBRAKE_TORQUE =
  ``parkingTorque`` de la rueda trasera; condición: freno de mano delantero 0 (AC solo
  tiene freno de mano detrás).
* ``ctrl.steer_lock_200409@1`` (``direct``, identidad): STEER_LOCK = ``steeringWheelLock``;
  condición: mecanismo ``rack``.

NO se añaden (pendientes con motivo): STEER_RATIO y LINEAR_STEER_ROD_RATIO (solo su
cociente lo fija BeamNG; su efecto depende de los anclajes de bieleta WBCAR_STEER/
WBTYRE_STEER, fuera de esta fase), térmica de frenos ([TEMPS_*], modelos distintos), ABS
(electronics.ini: todo/nada por umbral en AC frente a controlador PI en BeamNG).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-03"
AUTHOR = "claude (G2.15: dirección, frenos y aerodinámica del 200409, encargada por el usuario)"
DOC = "docs/research/g215_direction_brakes_aero_2026-11-03.md"
EVID = "output/g215_direction_brakes_aero_2026-11-03"
ACS = f"{EVID}/acs_dir_frenos_aero"
EXPORTERS = ["200409"]
COND = [S.Condition(fact="ir.formats.exporter_version", op="in", value=EXPORTERS)]
NO_KNEE = [
    S.Condition(fact="ir.brakes.axles.front.input_split", op="eq", value=1),
    S.Condition(fact="ir.brakes.axles.rear.input_split", op="eq", value=1),
]


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


WHEELS = "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/wheels.lua"
BRAKE_SOURCE = [
    ev("code", "src/extract/brakes_tyres.py", "brake_torque, front_share"),
    ev(
        "code",
        WHEELS,
        "updateBrakeNoABS l. 554-557; updateWheelTorques l. 642",
        "1eaf8b31…",
    ),
    ev(
        "code",
        WHEELS,
        "updateWheelVelocities l. 595-596: max(principal, parkingTorque·entrada)",
        "1eaf8b31…",
    ),
]
BRAKE_TARGET = [
    ev(
        "code",
        "",
        "frenos de AC",
    ),
    ev(
        "code",
        "",
        "frenos de AC (MAX_TORQUE, FRONT_SHARE, HANDBRAKE_TORQUE)",
    ),
    ev("doc", "docs/research/convenciones_fisicas_2026-10-26.md", "§2"),
]
EVIDENCE = [
    ev("report", DOC),
    ev("report", f"{EVID}/inventario.md", "frenos por rueda de los 7 vehículos 200409"),
]
BRAKE_ASSUMPTIONS = [
    "par nominal: BeamNG multiplica el par por la eficiencia térmica de la pastilla "
    "(wheels.lua calculateThermalEfficiency, ≤ 1; 'sport' ≈ 0,92 y 'semi-race' ≈ 0,86 a 20 °C); "
    "AC sin [TEMPS_*] no tiene pérdida térmica: se convierte el par a eficiencia 1",
    "ABS de BeamNG no convertido (electronics.ini pendiente): con el ABS activo el par real "
    "de BeamNG lo limita el controlador",
    "brakeSpring y el retardo de presión (0,04 s) de BeamNG no tienen equivalente en AC",
    "ningún vehículo 200409 se ha probado en AC",
]


def history(reason: str) -> list[S.HistoryEntry]:
    return [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="create",
            reason=reason,
            status_from="",
            status_to="provisional",
        )
    ]


def rule(
    id_,
    target,
    kind,
    transform,
    inputs,
    conditions,
    assumptions,
    source,
    tdoc,
    notes,
    reason,
) -> S.Rule:
    return S.Rule(
        id=id_,
        version=1,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=list(COND) + conditions,
        assumptions=assumptions,
        open_issues=[],
        source_doc=source,
        target_doc=tdoc,
        evidence=list(EVIDENCE),
        validations=[],
        history=history(reason),
        notes=notes,
    )


def build() -> list[S.Rule]:
    out = [
        rule(
            "brk.max_torque_200409",
            "ac:brakes.ini/DATA/MAX_TORQUE",
            "computed",
            S.Transform(
                id="linear_sum",
                params={"coefficients": {"front": 1, "rear": 1}},
                input_units={"front": "N·m", "rear": "N·m"},
                output_unit="N·m",
                formula="MAX_TORQUE = T_rueda_del + T_rueda_tras (pedal 1)",
            ),
            {
                "front": "ir.brakes.axles.front.max_torque_per_wheel",
                "rear": "ir.brakes.axles.rear.max_torque_per_wheel",
            },
            list(NO_KNEE),
            list(BRAKE_ASSUMPTIONS),
            list(BRAKE_SOURCE),
            list(BRAKE_TARGET),
            "G2.15: solo 200409 y sin codo de pedal en los dos ejes (input_split = 1): el "
            "freno de AC es lineal en el pedal, como el de BeamNG en ese caso",
            "G2.15: MAX_TORQUE por la semántica de frenos de AC (cada rueda delantera "
            "MAX·FS, cada trasera MAX·(1 − FS))",
        ),
        rule(
            "brk.front_share_200409",
            "ac:brakes.ini/DATA/FRONT_SHARE",
            "computed",
            S.Transform(
                id="identity", params={}, input_units={"x": "1"}, output_unit="1"
            ),
            {"x": "ir.brakes.front_share_full_pedal"},
            list(NO_KNEE),
            list(BRAKE_ASSUMPTIONS),
            list(BRAKE_SOURCE),
            list(BRAKE_TARGET),
            "G2.15: solo 200409 y sin codo: el reparto es el mismo a cualquier pedal "
            "(front_share_full_pedal = front_share_below_knee)",
            "G2.15: FRONT_SHARE = T_del/(T_del + T_tras) por rueda (frenos de AC)",
        ),
        rule(
            "brk.handbrake_torque_200409",
            "ac:brakes.ini/DATA/HANDBRAKE_TORQUE",
            "direct",
            S.Transform(
                id="identity", params={}, input_units={"x": "N·m"}, output_unit="N·m"
            ),
            {"x": "ir.brakes.axles.rear.parking_torque_per_wheel"},
            [
                S.Condition(
                    fact="ir.brakes.axles.front.parking_torque_per_wheel",
                    op="eq",
                    value=0,
                )
            ],
            [
                "BeamNG aplica max(freno principal, parkingTorque·entrada) por rueda; cómo "
                "combina AC handBrakeTorque con brakeTorque en la rueda (Tyre) no se ha leído: "
                "igual con solo el freno de mano",
                "ningún vehículo 200409 se ha probado en AC",
            ],
            list(BRAKE_SOURCE),
            list(BRAKE_TARGET),
            "G2.15: solo 200409 y freno de mano delantero 0 (AC solo frena con él las traseras)",
            "G2.15: HANDBRAKE_TORQUE completo en cada rueda trasera (frenos de AC)",
        ),
        rule(
            "ctrl.steer_lock_200409",
            "ac:car.ini/CONTROLS/STEER_LOCK",
            "direct",
            S.Transform(
                id="identity", params={}, input_units={"x": "deg"}, output_unit="deg"
            ),
            {"x": "ir.steering.steering_wheel_lock"},
            [S.Condition(fact="ir.steering.mechanism", op="eq", value="rack")],
            [
                "misma magnitud en los dos juegos: grados de volante con el mando a tope "
                "(BeamNG hydros.lua l. 500; AC coche de AC steerLock·controls.steer)",
                "STEER_RATIO y LINEAR_STEER_ROD_RATIO pendientes: el ángulo de rueda en AC no "
                "queda fijado por STEER_LOCK solo",
                "precedente: STEER_LOCK = steeringWheelLock (450) en el Ibiza 38022, conducido "
                "en AC con un G29 (docs/research/informe_direccion_piloto_integracion_2026-10-24.md)",
                "ningún vehículo 200409 se ha probado en AC",
            ],
            [
                ev("code", "src/extract/steering.py", "build_steering"),
                ev(
                    "code",
                    "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/input.lua",
                    "initSecondStage l. 149-182",
                    "25438ea9…",
                ),
            ],
            [
                ev(
                    "code",
                    "",
                    "STEER_LOCK, STEER_RATIO (12 por defecto), LINEAR_STEER_ROD_RATIO (0,003)",
                ),
                ev("code", f"{ACS}/steer_step_disasm.txt", "dirección de AC"),
            ],
            "G2.15: solo 200409 y cremallera en raíl movida por hidros",
            "G2.15: STEER_LOCK = steeringWheelLock (misma semántica en los dos juegos)",
        ),
    ]
    return out


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
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
