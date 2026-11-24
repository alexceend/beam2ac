"""Reglas de suspensión y dirección del exportador 200409 (G2.17).

    python tools/catalog/add_suspension_g217_200409_rules.py [--dry-run]

Encargada por el usuario en G2.17. Solo añade; nunca sobrescribe (reglas inmutables). Las
reglas anteriores no cambian; las que se sustituyen lo hacen con una versión nueva
(``supersedes``). Todas llevan ``ir.formats.exporter_version in ["200409"]`` (lista cerrada)
y la arquitectura MEDIDA del eje (``linkage`` DWB). Nunca el nombre del vehículo.

Semántica de AC (analizada en
``output/g217_suspension_steering_2026-11-04/acs_suspension/``) [C]:

* suspensión de AC: ``TOE_OUT`` → ``toeOUT_Linear`` (m, sin conversión);
  ``BUMPSTOP_UP`` y ``−BUMPSTOP_DN`` (m), ``BUMP_STOP_RATE`` (0 ⇒ 500 000),
  ``BUMP_STOP_PROGRESSIVE`` (solo si existe), ``DAMP_FAST_*`` (0 ⇒ el lento) y
  ``DAMP_FAST_*THRESHOLD`` (0 ⇒ 0,2 m/s).
* suspensión de AC: el punto del chasis de la barra se desplaza en X
  ``signo(x de la rueda)·toeOUT_Linear`` (hacia fuera con valores positivos); solo se llama
  en cada paso para el eje delantero (dirección de AC; el trasero solo con 4WS o al
  cambiar el reglaje) ⇒ sin regla para ``TOE_OUT`` trasero.
* amortiguador de AC: v > 0 (el buje sube respecto a la carrocería) = compresión:
  ``DAMP_BUMP``·v hasta ``DAMP_FAST_BUMPTHRESHOLD`` y después la pendiente
  ``DAMP_FAST_BUMP`` (continua); extensión igual con ``*REBOUND*``.
* suspensión de AC: topes con x = altura del buje − base: por encima de ``BUMPSTOP_UP``
  o por debajo de ``−BUMPSTOP_DN``, F = (``BUMP_STOP_RATE`` + ``BUMP_STOP_PROGRESSIVE``·x')·x'
  (x' = penetración), sin amortiguación.
* barra estabilizadora de AC: F = ``[ARB]``·(y_der − y_izq) vertical en cada buje (k en N/m de
  diferencia de altura).
* coche de AC + coche de AC + dirección de AC: el punto de la barra se
  desplaza −(STEER_LOCK·volante/STEER_RATIO)·LSRR (señal en grados) ⇒ LSRR en m/grado.

Origen (BeamNG): banco de G2.17 (``output/g217_suspension_steering_2026-11-04/banco``) [M] y
cinemática del DWB medido (``src/extract/linkage.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-04"
AUTHOR = "claude (G2.17: cierre de suspensión y dirección del 200409, encargada por el usuario)"
DOC = "docs/research/g217_suspension_steering_2026-11-04.md"
EVID = "output/g217_suspension_steering_2026-11-04"
ACSDIR = f"{EVID}/acs_suspension"
BENCH = f"{EVID}/banco/resultados_s1_s2.md y topes_s1_s2_s3.md"
EXPORTERS = ["200409"]
DWB_KINDS = ["dwb_5link", "dwb_equivalent"]
AXLES = (("front", "FRONT"), ("rear", "REAR"))


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


def cond_exporter() -> list[S.Condition]:
    return [S.Condition(fact="ir.formats.exporter_version", op="in", value=EXPORTERS)]


def cond_dwb(axle: str) -> list[S.Condition]:
    return [
        S.Condition(
            fact=f"ir.suspension.axles.{axle}.linkage", op="in", value=list(DWB_KINDS)
        )
    ]


SOURCE = [
    ev("code", "src/extract/linkage.py", "analyse_corner"),
    ev("code", "src/importers/beamng.py", "_import_linkage_g217"),
]
COMMON = [
    "modelo de enlaces rígidos de BeamNG (src/extract/linkage.py): la elasticidad de las "
    "barras y de la carrocería no se representa en AC",
    "ningún vehículo 200409 se ha probado en AC",
]


def history(reason: str, version: int) -> list[S.HistoryEntry]:
    return [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="create" if version == 1 else "new_version",
            reason=reason,
            status_from="" if version == 1 else "provisional",
            status_to="provisional",
        )
    ]


def rule(
    id_: str,
    target: str,
    kind: str,
    transform: S.Transform,
    inputs: dict[str, str],
    conditions: list[S.Condition],
    assumptions: list[str],
    target_doc: list[S.EvidenceRef],
    evidence: list[S.EvidenceRef],
    notes: str,
    reason: str,
    version: int = 1,
    supersedes: str = "",
    source_doc: list[S.EvidenceRef] | None = None,
) -> S.Rule:
    return S.Rule(
        id=id_,
        version=version,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=cond_exporter() + conditions,
        assumptions=list(COMMON) + assumptions,
        open_issues=[],
        source_doc=list(source_doc if source_doc is not None else SOURCE),
        target_doc=target_doc,
        evidence=[ev("report", DOC)] + evidence,
        validations=[],
        supersedes=supersedes,
        history=history(reason, version),
        notes=notes,
    )


def ident(unit: str, formula: str = "") -> S.Transform:
    return S.Transform(
        id="identity",
        params={},
        input_units={"x": unit},
        output_unit=unit,
        formula=formula,
    )


T_LOAD = ev(
    "code",
    "",
    "suspensión de AC",
)
T_STEP = ev(
    "code",
    "",
    "suspensión de AC",
)
T_DAMP = ev(
    "code",
    "",
    "amortiguador de AC",
)
T_TOE = ev(
    "code",
    "",
    "suspensión de AC",
)
T_ATTACH = ev(
    "code",
    "",
    "suspensión de AC",
)
T_ARB = ev(
    "code",
    "",
    "barra estabilizadora de AC",
)
T_ARBLOAD = ev(
    "code",
    "",
    "coche de AC",
)
G215 = "output/g215_direction_brakes_aero_2026-11-03/acs_dir_frenos_aero"
T_LSRR = [
    ev(
        "code",
        G215,
        "coche de AC (LINEAR_STEER_ROD_RATIO; 0 ⇒ 0,003)",
    ),
    ev(
        "code",
        G215,
        "coche de AC: finalSteerAngleSignal = steerLock·steer/steerRatio (grados)",
    ),
    ev("code", f"{G215}/steer_step_disasm.txt", "dirección de AC"),
]
E_BENCH = ev("report", BENCH, "banco de BeamNG (2 sesiones + topes)")
E_KIN = ev("report", f"{EVID}/cinematica_g217.md")


def build() -> list[S.Rule]:
    out: list[S.Rule] = []
    # ---------------------------------------------------------------- D-G216-LSRR
    out.append(
        rule(
            "ctrl.linear_steer_rod_ratio_200409",
            "ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO",
            "measured",
            ident(
                "m/deg",
                "LSRR = desplazamiento lateral de la barra por grado de rueda (m/°)",
            ),
            {"x": "ir.steering.rack_displacement_per_wheel_degree"},
            cond_dwb("front")
            + [S.Condition(fact="ir.steering.mechanism", op="eq", value="rack")],
            [
                "unidad del destino documentada en la REGLA (m por grado de la señal de "
                "ángulo nominal STEER_LOCK·volante/STEER_RATIO; [C] coche de AC, "
                "coche de AC, dirección de AC): el atributo del catálogo sigue con "
                "unidad 'undocumented' porque las dos decisiones históricas (Ibiza "
                "0,003156, Porsche 0,003504) se registraron así y el generador de INI "
                "dejaría de escribirlas si el atributo cambiase (D-G216-LSRR, "
                f"{EVID}/lsrr/prueba_unidad.md)",
                "AC: desplazamiento de WBCAR_STEER = STEER_LOCK·volante/STEER_RATIO·LSRR; "
                "BeamNG: recorrido de la cremallera·entrada ⇒ LSRR/STEER_RATIO = "
                "recorrido/STEER_LOCK (exacto); el reparto entre las dos claves sigue la "
                "convención de Kunos (LSRR = m por grado de rueda)",
                "BeamNG gira 1–1,5° menos a tope que el modelo rígido (elasticidad; banco "
                "de G2.15): AC (rígido) dará el ángulo del modelo",
            ],
            T_LSRR + [T_TOE],
            [ev("report", f"{EVID}/lsrr/prueba_unidad.md")],
            "G2.17: solo 200409, DWB delantero medido y cremallera; unidad m/deg [C]",
            "G2.17 (D-G216-LSRR): unidad m/deg documentada en la regla (identidad); "
            "sustituye a as_undocumented (E0) sin tocar el atributo ni las decisiones",
            version=2,
            supersedes="ctrl.linear_steer_rod_ratio_200409@1",
        )
    )
    # ---------------------------------------------------------------- TOE_OUT delantero
    out.append(
        rule(
            "susp.toe_out_front_200409",
            "ac:suspensions.ini/FRONT/TOE_OUT",
            "computed",
            S.Transform(
                id="ac_toe_out",
                params={},
                input_units={"toe": "deg", "shift": "m/deg"},
                output_unit="m",
                formula="TOE_OUT = −convergencia en reposo × desplazamiento hacia dentro "
                "de la barra por grado de toe-in",
            ),
            {
                "toe": "ir.suspension.axles.front.rest_toe",
                "shift": "ir.suspension.axles.front.rod_inward_shift_per_toe_in",
            },
            cond_dwb("front")
            + [
                S.Condition(
                    fact="ir.steering.tie_rod_position",
                    op="in",
                    value=["ahead", "behind"],
                )
            ],
            [
                "AC monta el buje alineado con la carrocería (suspensión de AC) y los "
                "puntos WBTYRE_* son los de la mangueta en reposo: sin TOE_OUT la rueda "
                "queda a 0°; TOE_OUT gira la mangueta la convergencia de reposo, que la "
                "deja 0,08–0,19° más girada que en BeamNG (efecto de segundo orden en la "
                "geometría)",
                "la barra delantera del DWB es la de dirección (unida al extremo de la "
                "cremallera: steering.tie_rod_position presente)",
                "convergencia de reposo = la que fija la precompresión de las barras "
                "(L·beamPrecompression, [M] G2.16), sin carga",
            ],
            [T_LOAD, T_TOE, T_ATTACH],
            [E_KIN],
            "G2.17: solo 200409, DWB delantero medido y barra de dirección; el trasero "
            "no tiene regla (AC no aplica TOE_OUT trasero al cargar)",
            "G2.17: TOE_OUT delantero como desplazamiento lineal de la barra [C]",
        )
    )
    for axle, AX in AXLES:
        pfx = f"ac:suspensions.ini/{AX}/"
        ir = f"ir.suspension.axles.{axle}"
        damp_ass = [
            "BeamNG [M] (banco de G2.17, 3 arquitecturas, un parámetro cada vez): al "
            "acortarse el amortiguador actúan beamDamp (lento) y beamDampFast (pendiente "
            "por encima de beamDampVelocitySplit), al alargarse beamDampRebound, "
            "beamDampReboundFast y beamDampVelocitySplitRebound; la ley es continua en el "
            "umbral, como amortiguador de AC",
            "coeficientes y umbral llevados a la rueda con la palanca cinemática del "
            "amortiguador en reposo (MR² y 1/MR); la palanca varía ≤ 2,3 % en ±40 mm",
            "el filtro dampCutoffHz (500 Hz) de BeamNG no existe en AC (desfase "
            "despreciable por debajo de 50 Hz)",
        ]
        for key, field_, what in (
            ("DAMP_BUMP", "damper_rate_slow_bump", "lento de compresión"),
            ("DAMP_REBOUND", "damper_rate_slow_rebound", "lento de extensión"),
        ):
            out.append(
                rule(
                    f"susp.{key.lower()}_{axle}_200409",
                    pfx + key,
                    "computed",
                    ident("N·s/m"),
                    {"x": f"{ir}.{field_}"},
                    cond_dwb(axle),
                    damp_ass,
                    [T_DAMP, T_STEP, T_LOAD],
                    [E_BENCH],
                    f"G2.17: coeficiente {what} por sentido (antes solo si "
                    "beamDamp = beamDampRebound)",
                    "G2.17: sentido de la amortiguación medido en el banco",
                    version=2,
                    supersedes=f"susp.{key.lower()}_{axle}_200409@1",
                )
            )
        for key, field_, unit in (
            ("DAMP_FAST_BUMP", "damper_rate_fast_bump", "N·s/m"),
            ("DAMP_FAST_REBOUND", "damper_rate_fast_rebound", "N·s/m"),
            ("DAMP_FAST_BUMPTHRESHOLD", "damper_fast_threshold_bump", "m/s"),
            ("DAMP_FAST_REBOUNDTHRESHOLD", "damper_fast_threshold_rebound", "m/s"),
        ):
            out.append(
                rule(
                    f"susp.{key.lower()}_{axle}_200409",
                    pfx + key,
                    "computed",
                    ident(unit),
                    {"x": f"{ir}.{field_}"},
                    cond_dwb(axle),
                    damp_ass,
                    [T_DAMP, T_STEP, T_LOAD],
                    [E_BENCH],
                    f"G2.17: {key} desde la ley medida en BeamNG; solo 200409 y eje "
                    f"{axle} DWB medido",
                    f"G2.17: {key} (amortiguación rápida medida en el banco)",
                )
            )
        stop_ass = [
            "BeamNG [M] (banco de G2.17, 7 vehículos, 28 topes): el tope empieza "
            "exactamente cuando la viga mide la longitud de creación ∓ *BoundRange y, en "
            "la zona de transición, F = 11·beamLimitSpring·p + "
            "(beamLimitSpring/boundZone)·p² a lo largo de la viga",
            "recorrido hasta el contacto con la cinemática del DWB (rígido) desde el "
            "reposo del modelo (la base de AC)",
        ]
        out.append(
            rule(
                f"susp.bumpstop_up_{axle}_200409",
                pfx + "BUMPSTOP_UP",
                "computed",
                ident("m"),
                {"x": f"{ir}.bumpstop_travel_up"},
                cond_dwb(axle),
                stop_ass,
                [T_LOAD, T_STEP],
                [E_BENCH, E_KIN],
                f"G2.17: solo 200409 y eje {axle} DWB medido",
                "G2.17: posición del tope de compresión",
            )
        )
        out.append(
            rule(
                f"susp.bumpstop_dn_{axle}_200409",
                pfx + "BUMPSTOP_DN",
                "computed",
                ident("m"),
                {"x": f"{ir}.bumpstop_travel_down"},
                cond_dwb(axle),
                stop_ass
                + [
                    "AC lee BUMPSTOP_DN con el signo cambiado (suspensión de AC): el "
                    "valor positivo es el recorrido hacia abajo"
                ],
                [T_LOAD, T_STEP],
                [E_BENCH, E_KIN],
                f"G2.17: solo 200409 y eje {axle} DWB medido",
                "G2.17: posición del tope de extensión",
            )
        )
        rate_ass = stop_ass + [
            "AC usa una sola BUMP_STOP_RATE para arriba y abajo: se toma la palanca en "
            "el contacto de compresión; en el de extensión la rigidez real difiere en "
            "(MR_abajo/MR_arriba)² (cinematica_g217.md)",
            "AC no amortigua los topes (suspensión de AC): beamLimitDamp de BeamNG se "
            "pierde (ir…bumpstop_damping)",
            "solo el término lineal 11·kL·MR²: el cuadrático (kL/boundZone)·p² no se "
            "exporta porque BUMP_STOP_PROGRESSIVE no está en el catálogo y añadirlo "
            "cambia los bloqueos de los proyectos históricos (decisión D-G217-BSP; "
            "queda en ir…bumpstop_progressive): dentro de la zona AC da hasta p/(11·z) "
            "menos (≤ 8,3 % al final de la zona)",
            "más allá de la zona (p > boundZone) BeamNG pasa a F = 12·kL·z + kL·(p − z) "
            "[M] (banco, sesión 3): AC (lineal 11·kL) es más rígido a partir de una "
            "fuerza en la viga de 12·kL·z",
        ]
        out.append(
            rule(
                f"susp.bump_stop_rate_{axle}_200409",
                pfx + "BUMP_STOP_RATE",
                "computed",
                ident("N/m"),
                {"x": f"{ir}.bumpstop_rate"},
                cond_dwb(axle),
                rate_ass,
                [T_LOAD, T_STEP],
                [E_BENCH],
                f"G2.17: término lineal de la ley medida; solo 200409 y eje {axle} DWB",
                "G2.17: rigidez del tope (término lineal medido en el banco)",
            )
        )
        out.append(
            rule(
                f"susp.arb_{axle}_200409",
                f"ac:suspensions.ini/ARB/{AX}",
                "computed",
                ident("N/m"),
                {"x": f"{ir}.antiroll_rate"},
                cond_dwb(axle),
                [
                    "AC [C] (barra estabilizadora de AC): F = k·(y_der − y_izq) vertical en cada "
                    "buje ⇒ energía ½k·Δy²; BeamNG: barra de torsión, par = spring·θ "
                    "(bdebugImpl.lua) ⇒ ½k_t·θ² con θ = (dθ/dh)·Δy ⇒ k = k_t·(dθ/dh)²",
                    "dθ/dh de la cinemática rígida del DWB; el banco da 1–4,5 % menos "
                    "(elasticidad; cinematica_g217.md)",
                    "lineal en reposo (sin variación de dθ/dh con el recorrido)",
                ],
                [T_ARB, T_ARBLOAD],
                [E_BENCH, E_KIN],
                f"G2.17: solo 200409 y eje {axle} DWB medido con una barra de torsión "
                "entre las dos manguetas",
                "G2.17: barra estabilizadora (rigidez vertical equivalente)",
            )
        )
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cat = Catalog.load(REPO_CATALOG)
    rules = build()
    clash = [r.key for r in rules if r.key in cat.rules]
    if clash:
        print("ya existen (no se sobrescribe nada):", clash)
        return 1
    for r in rules:
        if r.supersedes and r.supersedes not in cat.rules:
            print("supersedes inexistente:", r.key, r.supersedes)
            return 1
    if args.dry_run:
        print(len(rules), "reglas:", [r.key for r in rules])
        return 0
    for r in rules:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print("escritas", len(rules), "reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
