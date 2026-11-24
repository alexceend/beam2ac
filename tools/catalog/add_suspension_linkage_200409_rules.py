"""Reglas de suspensión de doble triángulo y de dirección del exportador 200409 (G2.16).

    python tools/catalog/add_suspension_linkage_200409_rules.py [--dry-run]

Encargada por el usuario en G2.16 (D-G214-TYPE). Solo añade; nunca sobrescribe (reglas
inmutables). Ninguna regla anterior cambia; los exportadores 38022/40340 no reciben nada.
Todas llevan ``ir.formats.exporter_version in ["200409"]`` (lista cerrada) y la
arquitectura MEDIDA del eje: ``ir.suspension.axles.<eje>.linkage in ["dwb_5link",
"dwb_equivalent"]`` (``src/extract/linkage.py``). Nunca la etiqueta de la pieza ni el
nombre del vehículo.

Semántica de AC (AC, ``docs/research/ac_suspension_coordinates.md`` §2) [C]:

* ``TYPE=DWB`` → clase ``Suspension``: 5 barras rígidas (``dJointCreateDBall``) entre los 8
  puntos, escritos para la rueda derecha, relativos a la base de la rueda (centro de
  diseño del buje), ejes AC y +X hacia dentro; la izquierda se refleja.
* ``SPRING_RATE``: rigidez VERTICAL en rueda, ``F = (SPRING_RATE + …)·(x + ROD_LENGTH)``
  con x = recorrido vertical del buje desde la base; solo empuja.
* ``DAMP_BUMP``/``DAMP_REBOUND``: coeficientes verticales en rueda; v > 0 = compresión
  usa ``DAMP_BUMP`` (amortiguador de AC, ``ibiza_damping_research.md`` §2.1).
* ``STATIC_CAMBER`` (grados, + = parte alta hacia fuera) gira el marco del neumático sobre
  el buje; no mueve barras (§2.6).
* ``STEER_RATIO``/``LINEAR_STEER_ROD_RATIO``: el punto de la barra del chasis
  (``WBCAR_STEER``) se desplaza en X −STEER_LOCK·volante/STEER_RATIO·LSRR m
  (dirección de AC, G2.15).

Origen (BeamNG, ``src/extract/linkage.py``): mangueta rígida con los enlaces reales, sus
longitudes de reposo L·beamPrecompression (confirmado en el juego,
``output/g216_suspension_2026-11-04/precompresion_banco.md``) y la pose de reposo
resultante. Rigidez en rueda = beamSpring·MR², MR = |dL/dh| del muelle en el DWB.
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
AUTHOR = "claude (G2.16: arquitectura de suspensión y dirección del 200409, encargada por el usuario)"
DOC = "docs/research/g216_suspension_steering_2026-11-04.md"
EVID = "output/g216_suspension_2026-11-04"
G215 = "output/g215_direction_brakes_aero_2026-11-03"
ACS = f"{G215}/acs_dir_frenos_aero"
EXPORTERS = ["200409"]
DWB_KINDS = ["dwb_5link", "dwb_equivalent"]
AXLES = (("front", "FRONT"), ("rear", "REAR"))
ANCHOR_KEYS = (
    ("upper_chassis_front", "WBCAR_TOP_FRONT"),
    ("upper_chassis_rear", "WBCAR_TOP_REAR"),
    ("lower_chassis_front", "WBCAR_BOTTOM_FRONT"),
    ("lower_chassis_rear", "WBCAR_BOTTOM_REAR"),
    ("upper_upright", "WBTYRE_TOP"),
    ("lower_upright", "WBTYRE_BOTTOM"),
    ("rod_chassis", "WBCAR_STEER"),
    ("rod_upright", "WBTYRE_STEER"),
)


def ev(type_: str, path: str, locator: str = "", sha: str = "", note: str = ""):
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256=sha, note=note)


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
    ev("code", "src/importers/beamng.py", "_import_linkage"),
    ev(
        "report",
        f"{EVID}/precompresion_banco.md",
        "longitudes de los enlaces en el juego = L·beamPrecompression (≤ 0,07 mm)",
    ),
    ev(
        "report",
        f"{EVID}/elastico.md",
        "8 barras del 200409: cualquier DWB a ≤ 0,0024° del equilibrio elástico",
    ),
]
TARGET = [
    ev("doc", "docs/research/ac_suspension_coordinates.md", "§2.1-2.6 [Confirmed]"),
    ev("doc", "docs/research/ibiza_damping_research.md", "§2.1 amortiguador de AC"),
]
EVIDENCE = [ev("report", DOC), ev("report", f"{EVID}/cinematica_dwb.md")]
COMMON_ASSUMPTIONS = [
    "modelo de enlaces rígidos de BeamNG (vigas NORMAL ≥ 1000 N/m; las del 200409 ≥ 10⁶): "
    "la elasticidad de las barras, de la cremallera y de la carrocería no se representa en AC",
    "pose de reposo = enlaces con longitud L·beamPrecompression y el centro de rueda a la "
    "altura de la pose del JBeam, sin carga (src/extract/linkage.py)",
    "ningún vehículo 200409 se ha probado en AC; DWB de AC sin probar en ningún coche del "
    "proyecto (el Ibiza aprobado es STRUT)",
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


def rule(id_, target, kind, transform, inputs, conditions, assumptions, notes, reason):
    return S.Rule(
        id=id_,
        version=1,
        target=target,
        kind=kind,
        transform=transform,
        scope=S.Scope(level="family", family="automation_export"),
        status="provisional",
        inputs=inputs,
        conditions=cond_exporter() + conditions,
        assumptions=list(COMMON_ASSUMPTIONS) + assumptions,
        open_issues=[],
        source_doc=list(SOURCE),
        target_doc=list(TARGET),
        evidence=list(EVIDENCE),
        validations=[],
        history=history(reason),
        notes=notes,
    )


def ident(unit: str) -> S.Transform:
    return S.Transform(
        id="identity", params={}, input_units={"x": unit}, output_unit=unit
    )


def build() -> list[S.Rule]:
    out: list[S.Rule] = []
    for axle, AX in AXLES:
        pfx = f"ac:suspensions.ini/{AX}/"
        ir = f"ir.suspension.axles.{axle}"
        out.append(
            rule(
                f"susp.type_dwb_{axle}_200409",
                pfx + "TYPE",
                "computed",
                S.Transform(
                    id="constant",
                    params={"value": "DWB"},
                    input_units={},
                    output_unit="enum",
                    formula="DWB si la estructura medida del eje es un doble triángulo "
                    "(5 barras) o un conjunto de más barras con un DWB equivalente",
                ),
                {},
                cond_dwb(axle),
                [
                    "la arquitectura sale de la estructura de vigas, no de la etiqueta de la "
                    "pieza: 'Double Wishbone' trasero del 200409 son 8 barras (dos rótulas por "
                    "triángulo) de rango 5 cuyo DWB de 5 barras se desvía ≤ 0,0024° en ±60 mm"
                ],
                f"G2.16: solo 200409 y eje {axle} con estructura DWB medida",
                "G2.16: D-G214-TYPE, TYPE=DWB por estructura (src/extract/linkage.py)",
            )
        )
        for name, key in ANCHOR_KEYS:
            out.append(
                rule(
                    f"susp.{key.lower()}_{axle}_200409",
                    pfx + key,
                    "measured",
                    S.Transform(
                        id="vector_identity",
                        params={"length": 3},
                        input_units={"x": "m"},
                        output_unit="m",
                        formula="punto de anclaje − centro de rueda en reposo; ejes "
                        "(lateral hacia dentro, arriba, adelante), rueda derecha",
                    ),
                    {"x": f"{ir}.anchors.{name}"},
                    cond_dwb(axle),
                    [
                        "en un conjunto de más de 5 barras, el DWB es el subconjunto de rango 5 "
                        "mejor condicionado dentro de la tolerancia (todos dan la misma "
                        "trayectoria a ≤ 0,01°/0,1 mm); el reparto de fuerzas entre barras no se "
                        "conserva (en AC son rígidas)",
                        "la base de AC (±TRACK/2) es el centro de rueda de la pose del JBeam; el "
                        "centro en reposo se desplaza < 0,1 mm (precompresión de los enlaces)",
                    ],
                    f"G2.16: {name} → {key}; solo 200409 y eje {axle} DWB medido",
                    f"G2.16: anclaje {key} medido en la pose de reposo",
                )
            )
        out.append(
            rule(
                f"susp.spring_rate_{axle}_200409",
                pfx + "SPRING_RATE",
                "computed",
                ident("N/m"),
                {"x": f"{ir}.wheel_rate"},
                cond_dwb(axle),
                [
                    "rigidez en rueda = beamSpring·MR² con MR = |dL/dh| del muelle en el DWB "
                    "(trabajo virtual); AC la aplica en vertical en la base (suspensión de AC)",
                    "palanca casi constante (0,4–1,2 % en ±40 mm, cinematica_dwb.md): "
                    "PROGRESSIVE_SPRING_RATE queda pendiente (sin regla)",
                ],
                f"G2.16: solo 200409 y eje {axle} DWB medido",
                "G2.16: SPRING_RATE en rueda con la palanca cinemática (antes hipótesis)",
            )
        )
        out.append(
            rule(
                f"susp.rod_length_{axle}_200409",
                pfx + "ROD_LENGTH",
                "computed",
                ident("m"),
                {"x": f"{ir}.spring_preload_travel"},
                cond_dwb(axle),
                [
                    "ROD_LENGTH = compresión del muelle en reposo / MR: la fuerza vertical en "
                    "rueda en la base (SPRING_RATE·ROD_LENGTH) es la del muelle de BeamNG en la "
                    "pose de reposo (beamSpring·compresión·MR); compresión = L + "
                    "precompressionRange − longitud en reposo (stage2.lua)",
                    "no se supone que la pose del JBeam sea la estática con carga",
                ],
                f"G2.16: solo 200409 y eje {axle} DWB medido",
                "G2.16: ROD_LENGTH = precarga del muelle llevada a la rueda",
            )
        )
        for key in ("DAMP_BUMP", "DAMP_REBOUND"):
            out.append(
                rule(
                    f"susp.{key.lower()}_{axle}_200409",
                    pfx + key,
                    "computed",
                    ident("N·s/m"),
                    {"x": f"{ir}.damper_rate_slow"},
                    cond_dwb(axle),
                    [
                        "coeficiente lento en rueda = beamDamp·MR_amortiguador²; solo existe en "
                        "el IR si beamDamp = beamDampRebound (el coeficiente no depende de qué "
                        "sentido sea compresión en BeamNG, no verificado en C++)",
                        "los rápidos (beamDampFast/beamDampReboundFast, distintos en el 200409) "
                        "y los umbrales quedan pendientes",
                    ],
                    f"G2.16: solo 200409 y eje {axle} DWB medido; lento simétrico",
                    f"G2.16: {key} lento en rueda (DWB: vertical en rueda)",
                )
            )
        out.append(
            rule(
                f"susp.static_camber_{axle}_200409",
                pfx + "STATIC_CAMBER",
                "computed",
                ident("deg"),
                {"x": f"{ir}.rest_camber"},
                cond_dwb(axle),
                [
                    "caída del eje de rueda en la pose de reposo (incluye la precompresión de los "
                    "enlaces); en AC el buje empieza con la orientación del coche y "
                    "STATIC_CAMBER gira el neumático: la caída inicial es la de BeamNG",
                    "mismo signo: negativa = parte alta hacia dentro (AC §2.6 [C])",
                    "la convergencia en reposo (TOE_OUT) queda pendiente: el efecto del signo de "
                    "TOE_OUT no está validado",
                ],
                f"G2.16: solo 200409 y eje {axle} DWB medido",
                "G2.16: STATIC_CAMBER = caída en reposo",
            )
        )
    steer_cond = cond_dwb("front") + [
        S.Condition(fact="ir.steering.mechanism", op="eq", value="rack")
    ]
    steer_assump = [
        "relación en el centro: grados de volante por grado de rueda con la cinemática del "
        "DWB delantero (convergencia frente al desplazamiento lateral del punto del chasis de "
        "la barra, que es el extremo de la cremallera)",
        "AC: desplazamiento de WBCAR_STEER = STEER_LOCK·volante/STEER_RATIO·LSRR; BeamNG: "
        "recorrido de la cremallera·entrada ⇒ LSRR/STEER_RATIO = recorrido/STEER_LOCK (exacto); "
        "el reparto entre las dos claves sigue la convención de Kunos (LSRR = m por grado de "
        "rueda), que solo afecta a la señal de ángulo y al término giroscópico del FFB",
        "BeamNG gira 1–1,5° menos a tope que el modelo rígido (elasticidad; banco de G2.15): AC "
        "(rígido) dará el ángulo del modelo",
    ]
    for pos, factor in (("behind", -1), ("ahead", 1)):
        out.append(
            rule(
                f"ctrl.steer_ratio_rod_{pos}_200409",
                "ac:car.ini/CONTROLS/STEER_RATIO",
                "computed",
                S.Transform(
                    id="scale",
                    params={"factor": factor},
                    input_units={"x": "1"},
                    output_unit="1",
                    formula=f"STEER_RATIO = {factor:+d} × relación en el centro",
                ),
                {"x": "ir.steering.steering_ratio"},
                steer_cond
                + [
                    S.Condition(fact="ir.steering.tie_rod_position", op="eq", value=pos)
                ],
                steer_assump
                + [
                    (
                        "signo: con la barra detrás del eje, STEER_RATIO negativo (convención de "
                        "Kunos; Ibiza 38022 con −12,14 conducido en AC con el giro correcto, "
                        "informe_direccion_piloto_integracion_2026-10-24.md)"
                        if pos == "behind"
                        else "signo: con la barra delante del eje, positivo (el desplazamiento de la "
                        "barra gira la rueda al revés que detrás); sin coche de contraste en AC"
                    )
                ],
                f"G2.16: solo 200409, DWB delantero medido, cremallera y barra {pos}",
                "G2.16: STEER_RATIO desde la cinemática del DWB delantero",
            )
        )
    out.append(
        rule(
            "ctrl.linear_steer_rod_ratio_200409",
            "ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO",
            "measured",
            S.Transform(
                id="as_undocumented",
                params={
                    "unit": "m/deg",
                    "semantics": "dirección de AC desplaza la X del ancla del chasis "
                    "de la barra (m) −señal·LSRR con la señal en grados (STEER_LOCK·volante/"
                    "STEER_RATIO): LSRR en m por grado (G2.15 [C])",
                },
                input_units={"x": "m/deg"},
                output_unit="undocumented",
                formula="LSRR = desplazamiento lateral de la barra por grado de rueda",
            ),
            {"x": "ir.steering.rack_displacement_per_wheel_degree"},
            steer_cond,
            steer_assump,
            "G2.16: solo 200409, DWB delantero medido y cremallera",
            "G2.16: LINEAR_STEER_ROD_RATIO desde la cinemática del DWB delantero",
        )
    )
    for r in out:
        r.target_doc = list(r.target_doc)
        if r.target.startswith("ac:car.ini/CONTROLS/"):
            r.target_doc.append(
                ev("code", f"{ACS}/steer_step_disasm.txt", "dirección de AC")
            )
            r.source_doc = list(r.source_doc) + [
                ev("code", "src/extract/steering.py", "build_steering (rack_nodes)")
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
