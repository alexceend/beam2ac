"""Candidatos de la fase 5 para atributos que el catálogo no resuelve (estudio, no
reglas).

Cada candidato es ``{target, value, unit, method, assumptions, missing, evidence}``.
Ninguno se aplica: son el material que necesita una decisión humana (editor de
atributos). Cuando un atributo admite varias interpretaciones, se dan todas (p. ej.
``MAX_TORQUE`` por rueda o total) y ``missing`` dice qué dato las desempataría. Ningún
valor es inventado: o sale de los hechos del vehículo con una fórmula escrita, o el
candidato no existe y ``missing`` lo explica.
"""

from __future__ import annotations

from typing import Any

from src.jbeam import parse_table
from src.pipeline.physics_preview import PhysicsPreview

from src.extract.nodes import UnsupportedGeometry

from .candidates_dwb import StructureError, axle_wheels, measure_dwb, symmetry_error
from .facts_bng import Fact

# Errores de geometría que se registran como carencia (no detienen el informe).
GEOMETRY_ERRORS = (StructureError, UnsupportedGeometry)

ACS = "docs/research/ac_suspension_coordinates.md"


def _c(
    target: str,
    value: Any,
    unit: str,
    method: str,
    assumptions: list[str],
    missing: list[str],
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "target": target,
        "value": value,
        "unit": unit,
        "method": method,
        "assumptions": assumptions,
        "missing": missing,
        "evidence": evidence,
        "applied": False,
    }


def _r(x: float, n: int = 4) -> float:
    return round(float(x), n)


WHEEL_MASS_OPTIONS = ("hubWeight", "tireWeight", "hubWeightGainRatio")


def mass_checks(pv: PhysicsPreview) -> dict[str, Any]:
    """Comprobaciones de las fuentes de masa que la suma de nodos podría omitir o
    duplicar, calculadas en ESTE vehículo (wheels.lua: addPressureWheel l. 1377-1388 y
    tapacubos 1540-1600).
    """
    resolved = pv.resolved
    state: dict[str, Any] = {}
    options: dict[str, list[str]] = {}
    ids: list[str] = []
    common_nodes: list[str] = []
    for a in resolved.active:
        data = a.part.data
        if "pressureWheels" in data:
            t = parse_table(data["pressureWheels"], initial_modifiers=state)
            state = t.final_modifiers
            for row in t.rows:
                p = row.properties
                used = [k for k in WHEEL_MASS_OPTIONS if k in p]
                if p.get("enableHubcaps") is True:
                    used.append("enableHubcaps")
                if used:
                    options[str(row.get("name"))] = used
        if "nodes" in data:
            rows = parse_table(data["nodes"]).rows
            ids += [r.get("id") for r in rows]
            if "common" in str(a.part.file).replace("\\", "/").split("/"):
                common_nodes.append(a.name)
    return {
        "wheel_mass_options_used": options,
        "node_rows": len(ids),
        "unique_node_ids": len(set(ids)),
        "parts_from_common_with_nodes": common_nodes,
        "note": "si no hay opciones de masa de rueda, la fórmula "
        "numRays·2·(hubNodeWeight + nodeWeight) es "
        "la del Lua",
    }


def _safe(fn, *args):
    try:
        return fn(*args)
    except GEOMETRY_ERRORS as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def build_candidates(pv: PhysicsPreview, facts: dict[str, Fact]) -> dict[str, Any]:
    out: dict[str, list[dict[str, Any]]] = {}
    gaps: dict[str, dict[str, Any]] = {}

    def add(c: dict[str, Any]) -> None:
        out.setdefault(c["target"], []).append(c)

    # G2.8: hechos ``ir.*`` (mismo parámetro del IR que los ``bng.*`` anteriores)
    fv = {k: f.value for k, f in facts.items()}
    g, ch = pv.geometry, pv.chassis

    # --- masas -----------------------------------------------------------------------
    if g is not None:
        by_axle: dict[str, list[float]] = {"FRONT": [], "REAR": []}
        for w in g.wheels:
            axle = "FRONT" if w.name in g.axles[0].wheels else "REAR"
            by_axle[axle].append(w.assembly_mass)
        for axle, masses in by_axle.items():
            if masses:
                add(
                    _c(
                        f"ac:suspensions.ini/{axle}/HUB_MASS",
                        _r(min(masses), 3),
                        "kg",
                        "cota inferior: conjunto de rueda (neumático "
                        "generado + nodos del eje), por rueda",
                        [
                            "la masa de mangueta y brazos que se "
                            "mueve con la rueda no se ha repartido"
                        ],
                        [
                            "criterio estructural validado de qué "
                            "nodos son no suspendidos (en el Ibiza "
                            "se eligieron "
                            "a mano: ibiza_mass_suspension_proposal.md §1.1)"
                        ],
                        [
                            "src/extract/mass_geometry.py WheelSummary.assembly_mass",
                            f"{ACS} §2.8",
                        ],
                    )
                )
        add(
            _c(
                "ac:car.ini/BASIC/TOTALMASS",
                None,
                "kg",
                "TOTALMASS = masa + piloto − Σ HUB_MASS (regla car.totalmass@1)",
                [],
                ["decisión de HUB_MASS delantero y trasero"],
                ["catalog/rules/car.totalmass@1.json"],
            )
        )
        cog_y = g.cog_ac[1]
        for axle, idx in (("FRONT", 0), ("REAR", 1)):
            centers = [
                w.center_ac[1] for w in g.wheels if w.name in g.axles[idx].wheels
            ]
            if centers:
                add(
                    _c(
                        f"ac:suspensions.ini/{axle}/BASEY",
                        _r(sum(centers) / len(centers) - cog_y),
                        "m",
                        "altura del centro de rueda − altura del CdG (marco AC)",
                        [
                            "CdG del vehículo completo (incluye "
                            "bujes y ruedas, sin piloto)"
                        ],
                        [
                            "reparto de masas (HUB_MASS) y posición "
                            "del piloto para el CdG del cuerpo "
                            f"({ACS} §7, incógnita 1)"
                        ],
                        [f"{ACS} §2.1, §6.2"],
                    )
                )
        if g.front_axle_fraction is not None:
            add(
                _c(
                    "ac:suspensions.ini/BASIC/CG_LOCATION",
                    _r(g.front_axle_fraction),
                    "1",
                    "fracción de WHEELBASE del CdG medida desde "
                    "el eje trasero = reparto estático delantero",
                    ["CdG del vehículo completo, sin piloto"],
                    ["mismo reparto de masas que BASEY"],
                    [
                        f"{ACS} §2.1 (CG_LOCATION)",
                        "Ibiza: 0,5730 del vehículo frente a "
                        "0,5676 aprobado (cuerpo + piloto)",
                    ],
                )
            )
        i = g.inertia_ac
        add(
            _c(
                "ac:car.ini/EXPLICIT_INERTIA/INERTIA",
                [_r(i[0][0], 1), _r(i[1][1], 1), _r(i[2][2], 1)],
                "kg·m²",
                "tensor de masas puntuales del vehículo completo "
                "respecto a su CdG (diagonal, ejes AC)",
                ["masas puntuales en los nodos"],
                [
                    "restar los bujes y sumar el piloto, como "
                    "en el Ibiza (ensamblado.md §9.13): depende "
                    "de HUB_MASS "
                    "y de la posición del piloto"
                ],
                ["src/extract/mass_geometry.py _inertia"],
            )
        )
    if "ir.declared.weight" in fv and "ir.mass.total" in fv and pv.resolved is not None:
        diff = fv["ir.mass.total"] - fv["ir.declared.weight"]
        gaps["mass_difference"] = {
            "measured_kg": _r(fv["ir.mass.total"], 3),
            "declared_kg": fv["ir.declared.weight"],
            "difference_kg": _r(diff, 3),
            "compatible_with_rounding": abs(diff) <= 0.5,
            "checks": _safe(mass_checks, pv),
            "missing": (
                []
                if abs(diff) <= 0.5
                else [
                    "definición de 'Weight' del exportador de "
                    "Automation (o medida de la masa en BeamNG)"
                ]
            ),
        }

    # --- frenos ----------------------------------------------------------------------
    for key, fid, note in (
        ("pedal 100 %", "ir.brakes.front_share_full_pedal", "a pedal máximo"),
        (
            "por debajo del codo",
            "ir.brakes.front_share_below_knee",
            "con frenada suave",
        ),
    ):
        if fid in fv:
            add(
                _c(
                    "ac:brakes.ini/DATA/FRONT_SHARE",
                    _r(fv[fid]),
                    "1",
                    f"reparto de par delantero {key}",
                    ["AC usa un reparto fijo; BeamNG tiene un codo (brakeInputSplit)"],
                    [f"elegir el régimen que debe reproducir AC ({note})"],
                    ["src/extract/brakes_tyres.py front_share"],
                )
            )
    f_t, r_t = fv.get("ir.brakes.axles.front.max_torque_per_wheel"), fv.get(
        "ir.brakes.axles.rear.max_torque_per_wheel"
    )
    if f_t is not None and r_t is not None:
        for interp, value in (
            ("por rueda delantera", f_t),
            ("total de las 4 ruedas", 2 * f_t + 2 * r_t),
            ("por eje delantero", 2 * f_t),
            (
                "rueda delantera + rueda trasera (MAX_TORQUE·FRONT_SHARE "
                "= par por rueda "
                "delantera; interpretación de la base DIAG del Ibiza)",
                f_t + r_t,
            ),
        ):
            add(
                _c(
                    "ac:brakes.ini/DATA/MAX_TORQUE",
                    _r(value, 1),
                    "N·m",
                    f"par de freno a pedal 1, {interp}",
                    ["interpretación de MAX_TORQUE de AC sin verificar"],
                    [
                        "qué representa MAX_TORQUE en AC (análisis "
                        "de AC o prueba de frenada en el "
                        "juego)"
                    ],
                    [
                        "Kunos: 'Maximum Brake torque in Nm' "
                        "(ks_toyota_ae86/data/brakes.ini)"
                    ],
                )
            )
    p_t = fv.get("ir.brakes.axles.rear.parking_torque_per_wheel")
    if p_t:
        for interp, value in (
            ("por rueda trasera", p_t),
            ("total del eje trasero", 2 * p_t),
        ):
            add(
                _c(
                    "ac:brakes.ini/DATA/HANDBRAKE_TORQUE",
                    _r(value, 1),
                    "N·m",
                    f"parkingTorque {interp}",
                    ["misma ambigüedad que MAX_TORQUE"],
                    ["semántica de HANDBRAKE_TORQUE en AC"],
                    ["src/extract/brakes_tyres.py parkingTorque"],
                )
            )

    # --- aerodinámica ----------------------------------------------------------------
    if "ir.aero.cla_front" in fv:
        for axle, fid in (
            ("delantero", "ir.aero.cla_front"),
            ("trasero", "ir.aero.cla_rear"),
        ):
            sign = "carga" if fv[fid] > 0 else "sustentación"
            add(
                _c(
                    "ac:aero.ini/WING_0/NAME",
                    None,
                    "text",
                    f"Cl·A {axle} = {_r(fv[fid])} m² ({sign}) a densidad 1,225 "
                    "(cefaero.lua:157-163)",
                    [
                        "convenio de AC 'CL > 0 = carga' INFERIDO "
                        "de 3 referencias de Kunos (R8 LMS +0,30/+0,24; "
                        "AE86 −0,03/−0,02; Celica −0,04/−0,05), "
                        "no verificado en código",
                        "una ala de AC con CL constante (tabla "
                        "plana) reproduce una fuerza ∝ v² de "
                        "cefaero",
                    ],
                    [
                        "confirmar el signo de CL en AC",
                        "origen de POSITION de las alas (ensamblado.md §9.7, l. 563)",
                        "elegir CHORD·SPAN (solo cuenta el producto con CL constante)",
                    ],
                    [
                        "src/extract/aero.py",
                        "G:/…/assettocorsa/content/cars/*/data/wing_*_AOA_CL.lut",
                    ],
                )
            )

    # --- suspensión (DWB) ------------------------------------------------------------
    if pv.resolved is not None:
        try:
            wheels = axle_wheels(pv.resolved)
        except GEOMETRY_ERRORS as exc:
            wheels = {}
            gaps["dwb"] = {"error": str(exc)}
        for axle, wheel in wheels.items():
            try:
                cand = measure_dwb(pv.resolved, wheel, axle)
            except GEOMETRY_ERRORS as exc:
                gaps.setdefault("dwb", {})[axle] = str(exc)
                continue
            try:
                sym = symmetry_error(pv.resolved, axle)
                cand.evidence.append(
                    f"simetría izquierda/derecha: máx. Δ = {sym * 1000:.3f} mm "
                    "(AC refleja la derecha)"
                )
            except GEOMETRY_ERRORS as exc:
                cand.caveats.append(
                    f"la rueda izquierda no tiene la misma estructura: {exc}"
                )
            add(
                _c(
                    f"ac:suspensions.ini/{axle}/TYPE",
                    "DWB",
                    "enum",
                    "estructura de doble triángulo identificada "
                    "por vigas (buje–mangueta–2 brazos–bieleta)",
                    [],
                    [
                        "decisión de usar DWB (semántica confirmada; "
                        "estabilidad en AC sin probar)"
                    ],
                    cand.evidence,
                )
            )
            for key, point in cand.points.items():
                add(
                    _c(
                        f"ac:suspensions.ini/{axle}/{key}",
                        [round(c, 5) for c in point],
                        "m",
                        f"nodo {cand.nodes[key]} − centro de rueda {wheel}, ejes AC, "
                        "rueda derecha (+X hacia dentro)",
                        cand.caveats,
                        ["decisión de usar DWB en este eje"],
                        cand.evidence,
                    )
                )
    if ch is not None:
        for axle, pos in (("FRONT", "front"), ("REAR", "rear")):
            corners = [
                c
                for a in ch.suspension.axles
                if a.index == (0 if pos == "front" else 1)
                for c in a.corners
            ]
            rates = sorted({round(c.wheel_rate_hyp, 1) for c in corners})
            if len(rates) == 1:
                add(
                    _c(
                        f"ac:suspensions.ini/{axle}/SPRING_RATE",
                        rates[0],
                        "N/m",
                        "rigidez del muelle × (relación de palanca)² "
                        "(hipótesis del extractor)",
                        [
                            "relación de palanca por proyección "
                            "geométrica, no cinemática medida"
                        ],
                        [
                            "cinemática del eje (desplazamiento "
                            "del muelle frente al de la rueda)"
                        ],
                        [
                            "src/extract/suspension.py wheel_rate_hyp",
                            "CLAUDE.md: SPRING_RATE = k·MR²",
                        ],
                    )
                )

    # --- sin datos en el origen
    # ----------------------------------------------------------
    for target, why in (
        (
            "ac:car.ini/CONTROLS/FFMULT",
            "preferencia del conductor y del volante; no "
            "hay dato en BeamNG (Ibiza: 1,95 "
            "calibrado por el usuario con un G29)",
        ),
        (
            "ac:car.ini/CONTROLS/STEER_LOCK",
            "necesita la geometría de dirección (hydros + "
            "cremallera): no hay extractor",
        ),
        ("ac:car.ini/CONTROLS/STEER_RATIO", "ídem"),
        (
            "ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO",
            "ídem, y unidad de AC sin documentar",
        ),
        (
            "ac:tyres.ini/FRONT/DX0",
            "modelo de neumático de AC sin equivalente en BeamNG (96 claves)",
        ),
    ):
        gaps.setdefault("no_source", {})[target] = why
    return {"candidates": out, "gaps": gaps}
