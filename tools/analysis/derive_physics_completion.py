"""Deriva, con métodos documentados, las claves físicas pendientes de un coche de
Automation con suspensión de doble triángulo (fase C5). No inventa valores: cada clave
lleva método, fuente, confianza y GRUPO DE VALIDACIÓN; lo que no tiene método queda fuera.

    python tools/analysis/derive_physics_completion.py --project output/projects/<p>
        --out output/<nueva> [--apply]

Métodos (los mismos de la base aprobada del Ibiza, aplicados a la geometría propia del coche):

* suspensión DWB (AC: rigidez, amortiguación y topes EN RUEDA, verticales):
  ``SPRING_RATE = k·MR²``; ``DAMP_* = c·MR²``; umbrales = ``v/MR``; ``BUMPSTOP_UP/DN =
  rango/MR``; ``BUMP_STOP_RATE = K_tope·MR²/2`` (opción B del Ibiza); ``ROD_LENGTH`` =
  carga suspendida por rueda / ``SPRING_RATE``; ``PACKER_RANGE`` = 0;
  ``PROGRESSIVE_SPRING_RATE`` = 0 si la rigidez en rueda varía < 3 % en ±40 mm.
  ``MR`` y caída/convergencia salen de ``src.lab.kinematics`` con los enlaces rígidos reales
  de BeamNG. Trasera con brazos trapezoidales: correspondencia R1 (patrón del R8 LMS
  oficial).
* barra estabilizadora: ``ARB = k_torsión·(dθ/dh)²`` (N/m entre alturas de buje), con
  ``dθ/dh`` medido moviendo una rueda en la cinemática.
* masas: ``HUB_MASS`` = rueda generada + nodos de buje y mangueta; CdG de la carrocería sin
  bujes y con el piloto 0,55 m bajo sus ojos (cámara de cabina del JBeam);
  ``CG_LOCATION``, ``BASEY``, ``GRAPHICS_OFFSET``, ``TOTALMASS`` y ``EXPLICIT_INERTIA``
  como en ``ibiza_mass_suspension_proposal.md`` §3 y v6.
* dirección: ``STEER_LOCK = steeringWheelLock``; ``LINEAR_STEER_ROD_RATIO`` = desplazamiento
  de la cremallera por grado de giro de la rueda (cinemática); ``STEER_RATIO`` =
  −LOCK/(recorrido/LSRR), negativo con la bieleta detrás del eje (convención de Kunos).
* neumático: método v6 (medianas y regresión frente al ancho de los neumáticos ST de
  ``sdk/dev/v1.5_tyres_ac``). **NO CALIBRADO**: no hay datos de BeamNG para ajustarlo.
* motor y transmisión: propuestas de las reglas del Ibiza (misma familia camsoEngine).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics as st
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.car_validate import read_ini  # noqa: E402
from src.coords import beamng_to_ac  # noqa: E402
from src.extract.components import collect_beams  # noqa: E402
from src.extract.mass_geometry import compute_mass_geometry  # noqa: E402
from src.extract.nodes import collect_nodes  # noqa: E402
from src.jbeam import evaluate, parse_table, resolve  # noqa: E402
from src.lab import kinematics as K  # noqa: E402
from src.safe_paths import new_output_dir  # noqa: E402

G = 9.806
DRIVER_DROP = 0.55  # m bajo los ojos (aprox. antropométrica, propuesta del Ibiza §2)
SDK_TYRES = Path(r"G:\SteamLibrary\steamapps\common\assettocorsa\sdk\dev\v1.5_tyres_ac")
RIGID = 1000.0


def btype(b) -> str:
    return (
        str(b.props.get("beamType", "|NORMAL")).replace(" ", "").lstrip("|") or "NORMAL"
    )


# ----------------------------------------------------------------- estructura del eje
def axle_structure(r, nodes, wheel_row) -> dict[str, Any]:
    var = r.variable_values()
    hub = [wheel_row["node1"], wheel_row["node2"]]
    beams = [b for b in collect_beams(r) if len(b.nodes) == 2]
    rigid = []
    for b in beams:
        if btype(b) != "NORMAL":
            continue
        s = b.value("beamSpring", var, None)
        if s is None or (isinstance(s, (int, float)) and s >= RIGID):
            rigid.append(tuple(b.nodes))
    knuckle = sorted(
        {a if b in hub else b for a, b in rigid if (a in hub) != (b in hub)}
    )
    body = set(knuckle) | set(hub)
    # muelle y amortiguador: vigas BOUNDED con un nodo de la mangueta
    spring = damper = stop = None
    for b in beams:
        if btype(b) != "BOUNDED" or not (set(b.nodes) & set(knuckle)):
            continue
        p = {
            k: b.value(k, var, None)
            for k in (
                "beamSpring",
                "beamDamp",
                "beamDampRebound",
                "beamDampFast",
                "beamDampReboundFast",
                "beamDampVelocitySplit",
                "beamLimitSpring",
                "beamLimitDamp",
                "longBoundRange",
                "shortBoundRange",
                "boundZone",
                "precompressionRange",
            )
        }
        if (p["beamSpring"] or 0) > 0 and spring is None:
            spring = (tuple(b.nodes), p)
        elif (
            (p["beamDamp"] or 0) > 0
            and (p["beamDampRebound"] or 0) > 0
            and damper is None
        ):
            damper = (tuple(b.nodes), p)
        elif (
            (p["beamLimitSpring"] or 0) > 0 and p.get("longBoundRange") and stop is None
        ):
            stop = (tuple(b.nodes), p)
    coil = {frozenset(spring[0])} if spring else set()
    links: dict[str, set[str]] = {}
    for a, b in rigid:
        if frozenset((a, b)) in coil:
            continue
        for k, o in ((a, b), (b, a)):
            if k in knuckle and o not in body:
                links.setdefault(k, set()).add(o)
    return {
        "hub": hub,
        "knuckle": knuckle,
        "links": links,
        "spring": spring,
        "damper": damper,
        "stop": stop,
    }


def ac_mapping(s: dict, P: dict[str, tuple]) -> tuple[dict, list, str]:
    """Correspondencia con el DWB de AC: clásica (2 vértices + 1 bieleta) o R1 (brazos
    trapezoidales: superior al punto medio; inferior al nudo delantero; bieleta del
    pivote trasero al nudo trasero)."""
    links = {k: sorted(v) for k, v in s["links"].items()}
    arms = {k: v for k, v in links.items() if len(v) == 2}
    rods = {k: v for k, v in links.items() if len(v) == 1}
    z = lambda n: P[n][2]  # noqa: E731
    y = lambda n: P[n][1]  # noqa: E731
    if len(arms) == 2 and len(rods) == 1:
        (top, tp), (bot, bp) = sorted(arms.items(), key=lambda kv: -y(kv[0]))
        rk, (rc,) = next(iter(rods.items()))
        m = {
            "WBTYRE_TOP": top,
            "WBTYRE_BOTTOM": bot,
            "WBTYRE_STEER": rk,
            "WBCAR_STEER": rc,
        }
        kind = "DWB clásico (5 enlaces de BeamNG)"
    else:
        by_piv: dict[tuple, list[str]] = {}
        for k, v in arms.items():
            by_piv.setdefault(tuple(v), []).append(k)
        if len(by_piv) != 2 or any(len(ks) != 2 for ks in by_piv.values()) or rods:
            raise ValueError(f"estructura no soportada: {links}")
        (tpiv, tk), (bpiv, bk) = sorted(
            by_piv.items(), key=lambda kv: -sum(y(n) for n in kv[1])
        )
        P["_MT"] = tuple((a + b) / 2 for a, b in zip(P[tk[0]], P[tk[1]]))
        bf, br = sorted(bk, key=lambda n: -z(n))
        rear_piv = min(bpiv, key=z)
        m = {
            "WBTYRE_TOP": "_MT",
            "WBTYRE_BOTTOM": bf,
            "WBTYRE_STEER": br,
            "WBCAR_STEER": rear_piv,
        }
        tp, bp = list(tpiv), list(bpiv)
        kind = "R1: brazos trapezoidales (patrón del R8 LMS oficial)"
    tf, tr = sorted(tp, key=lambda n: -z(n))
    bf2, br2 = sorted(bp, key=lambda n: -z(n))
    m.update(
        {
            "WBCAR_TOP_FRONT": tf,
            "WBCAR_TOP_REAR": tr,
            "WBCAR_BOTTOM_FRONT": bf2,
            "WBCAR_BOTTOM_REAR": br2,
        }
    )
    ac_links = [
        (m["WBCAR_TOP_FRONT"], m["WBTYRE_TOP"]),
        (m["WBCAR_TOP_REAR"], m["WBTYRE_TOP"]),
        (m["WBCAR_BOTTOM_FRONT"], m["WBTYRE_BOTTOM"]),
        (m["WBCAR_BOTTOM_REAR"], m["WBTYRE_BOTTOM"]),
        (m["WBCAR_STEER"], m["WBTYRE_STEER"]),
    ]
    return m, ac_links, kind


def knuckle_model(s, P, links) -> K.Knuckle:
    hub = s["hub"]
    outer, inner = sorted(hub, key=lambda n: abs(P[n][0]), reverse=True)
    pts = {n: P[n] for n in set(s["knuckle"]) | set(hub) | {k for _, k in links}}
    pts["_C"] = tuple((a + b) / 2 for a, b in zip(P[hub[0]], P[hub[1]]))
    return K.Knuckle(pts, "_C", (outer, inner), [(P[c], k) for c, k in links])


def torsion_gain(kn: K.Knuckle, arm_node: str, pivots: tuple, P) -> float:
    """dθ/dh (rad/m) del brazo de la barra al mover solo esta rueda."""
    axis = tuple(b - a for a, b in zip(P[pivots[0]], P[pivots[1]]))
    n = math.sqrt(sum(x * x for x in axis))
    axis = tuple(x / n for x in axis)

    def ang(p):
        v = tuple(a - b for a, b in zip(kn.moved(p, arm_node), P[pivots[0]]))
        v = tuple(
            a - axis[i] * sum(x * y for x, y in zip(v, axis)) for i, a in enumerate(v)
        )
        ref = (0.0, 1.0, 0.0)
        return math.atan2(
            sum(
                axis[i] * c
                for i, c in enumerate(
                    (
                        ref[1] * v[2] - ref[2] * v[1],
                        ref[2] * v[0] - ref[0] * v[2],
                        ref[0] * v[1] - ref[1] * v[0],
                    )
                )
            ),
            sum(a * b for a, b in zip(ref, v)),
        )

    up, _ = K.solve(kn, 0.001)
    dn, _ = K.solve(kn, -0.001)
    return abs(ang(up) - ang(dn)) / 0.002


# ----------------------------------------------------------------- neumático (v6)
def sdk_street() -> list[tuple[dict, dict]]:
    out = []
    for f in sorted(SDK_TYRES.rglob("*.ini")):
        ini = read_ini(f)
        for sec, s in ini.items():
            if (
                re.fullmatch(r"(FRONT|REAR)(_\d+)?", sec)
                and s.get("SHORT_NAME") == "ST"
            ):
                out.append((s, ini.get("THERMAL_" + sec, {})))
    return out


def fnum(s: str) -> float:
    return float(str(s).split(",")[0].split(";")[0])


def linreg(xs, ys):
    mx, my = st.mean(xs), st.mean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum(
        (x - mx) ** 2 for x in xs
    )
    a = my - b * mx
    sd = st.pstdev([y - (a + b * x) for x, y in zip(xs, ys)])
    return a, b, sd


def tyre_values(width: float, pressure: float, street) -> dict[str, tuple[Any, str]]:
    med = lambda k: st.median(fnum(s[k]) for s, _ in street)  # noqa: E731
    W = [fnum(s["WIDTH"]) for s, _ in street]
    lib = f"mediana de {len(street)} neumáticos ST de sdk/dev/v1.5_tyres_ac"
    v: dict[str, tuple[Any, str]] = {}
    v["DAMP"] = (med("DAMP"), lib)
    rate_p = [
        fnum(s["RATE"])
        + (pressure - fnum(s["PRESSURE_STATIC"])) * fnum(s["PRESSURE_SPRING_GAIN"])
        for s, _ in street
    ]
    a, b, sd = linreg(W, rate_p)
    v["RATE"] = (
        round(a + b * width, -2),
        f"regresión RATE(P={pressure} psi) ~ WIDTH sobre los ST; sd {sd:.0f}",
    )
    for key in (
        "DY0",
        "DY1",
        "DX0",
        "DX1",
        "SPEED_SENSITIVITY",
        "RELAXATION_LENGTH",
        "ROLLING_RESISTANCE_0",
        "ROLLING_RESISTANCE_1",
        "ROLLING_RESISTANCE_SLIP",
        "DCAMBER_0",
        "DCAMBER_1",
        "FRICTION_LIMIT_ANGLE",
        "XMU",
        "PRESSURE_FLEX_GAIN",
        "PRESSURE_RR_GAIN",
        "PRESSURE_D_GAIN",
        "LS_EXPY",
        "LS_EXPX",
        "DX_REF",
        "DY_REF",
        "FLEX_GAIN",
        "FALLOFF_LEVEL",
        "FALLOFF_SPEED",
    ):
        v[key] = (med(key), lib)
    for key, nd in (
        ("FLEX", 7),
        ("CAMBER_GAIN", 4),
        ("PRESSURE_SPRING_GAIN", -1),
        ("FZ0", 0),
    ):
        a, b, sd = linreg(W, [fnum(s[key]) for s, _ in street])
        v[key] = (
            round(a + b * width, nd),
            f"regresión {key} ~ WIDTH (ST del SDK); sd {sd:.4g}",
        )
    d_ideal = st.median(
        fnum(s["PRESSURE_IDEAL"]) - fnum(s["PRESSURE_STATIC"]) for s, _ in street
    )
    v["PRESSURE_IDEAL"] = (
        round(pressure + d_ideal, 1),
        f"PRESSURE_STATIC + mediana(IDEAL−STATIC) {d_ideal}",
    )
    return v


def thermal_values(street) -> dict[str, tuple[Any, str]]:
    lib = f"mediana de {len(street)} térmicos ST del SDK"
    return {
        k: (st.median(fnum(t[k]) for _, t in street), lib)
        for k in (
            "SURFACE_TRANSFER",
            "PATCH_TRANSFER",
            "CORE_TRANSFER",
            "INTERNAL_CORE_TRANSFER",
            "FRICTION_K",
            "ROLLING_K",
            "GRAIN_GAMMA",
            "GRAIN_GAIN",
            "BLISTER_GAMMA",
            "BLISTER_GAIN",
            "COOL_FACTOR",
            "SURFACE_ROLLING_K",
        )
    }


# ----------------------------------------------------------------- aerodinámica
RHO_CEFAERO = 1.225  # cefaero.lua l. 17: densidad del módulo al calibrar (l. 157-163)


def aero_a1(r, var, P, cb) -> dict | None:
    """Tres alas de 1 m² con LUT planas que reproducen el cefaero de BeamNG:
    BODY (arrastre ``vehicleArea`` = CdA en el nodo origen de arrastre), FRONT y REAR
    (``ClA = objetivo / (0,5·ρ₀·v²)`` en los nodos de carga). POSITION = nodo − CdG de la
    carrocería. CL > 0 = carga en AC [C]; en cefaero el objetivo > 0 es carga [C, l. 136-142].
    """
    sec = lambda n: next((s for _, s in r.section_contributions(n)), None)  # noqa: E731
    drag, down, groups = (
        sec("dragModel"),
        sec("downforceModel"),
        sec("downforceNodeGroups"),
    )
    if not (drag and down and groups):
        return None
    cda = float(evaluate(drag["vehicleArea"], var))
    dnode = drag["dragNodes:"][0]
    v_ms = float(evaluate(down["targetSpeedKMH"], var)) / 3.6
    q0 = 0.5 * RHO_CEFAERO * v_ms * v_ms
    rows = parse_table(groups).rows
    front = next(
        row.values["ref:"] for row in rows if row.values.get("isFront") is True
    )
    rear = next(
        row.values["ref:"] for row in rows if row.values.get("isFront") is False
    )
    cla_f = float(evaluate(down["downforceTargetFront"], var)) / q0
    cla_r = float(evaluate(down["downforceTargetRear"], var)) / q0
    pos = lambda n: [round(P[n][i] - cb[i], 3) for i in range(3)]  # noqa: E731
    flat = lambda c: [[-90, round(c, 4)], [90, round(c, 4)]]  # noqa: E731
    why = (
        f"cefaero: CdA {cda} m² en {dnode}; ClA del. {cla_f:.4f} m² en {front}, tras. {cla_r:.4f} m² "
        f"en {rear} (objetivo / 0,5·ρ₀·v², ρ₀ {RHO_CEFAERO}, v {v_ms*3.6:g} km/h); ala de 1 m² "
        "(CHORD = SPAN = 1; área = CHORD·SPAN [H]); POSITION = nodo − CdG de la carrocería"
    )
    wings = []
    for name, node, cl, cd in (
        ("BODY", dnode, 0.0, cda),
        ("FRONT", front, cla_f, 0.0),
        ("REAR", rear, cla_r, 0.0),
    ):
        wings.append(
            {
                "NAME": name,
                "CHORD": 1,
                "SPAN": 1,
                "POSITION": pos(node),
                "CL_GAIN": 1 if cl else 0,
                "CD_GAIN": 1,
                "ANGLE": 0,
                "LUT_AOA_CL": flat(cl),
                "LUT_AOA_CD": flat(cd),
                "LUT_AOA_CL_file": f"beam2ac_{name.lower()}_cl.lut",
                "LUT_AOA_CD_file": f"beam2ac_{name.lower()}_cd.lut",
                "why": why,
            }
        )
    check = []
    for kmh in (50, 100, 150, 200, 250, 300):
        q = 0.5 * RHO_CEFAERO * (kmh / 3.6) ** 2
        check.append(
            {
                "kmh": kmh,
                "drag_N": round(q * cda, 1),
                "front_N": round(q * cla_f, 1),
                "rear_N": round(q * cla_r, 1),
                "note": "BeamNG y AC escalan ambos con ½ρv²: misma fuerza a la misma densidad",
            }
        )
    return {
        "wings": wings,
        "check": {
            "cda": cda,
            "cla_front": cla_f,
            "cla_rear": cla_r,
            "nodes": [dnode, front, rear],
            "forces": check,
        },
    }


# ----------------------------------------------------------------- principal
def derive(project) -> tuple[list[dict], dict]:
    from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip

    s = project.source
    src = (
        load_vehicle_dir(Path(s.path), s.pc)
        if s.kind == "dir"
        else load_vehicle_zip(
            Path(s.path), Path(s.common_zip) if s.common_zip else None, s.pc
        )
    )
    r = resolve(src.index, src.pc)
    var = r.variable_values()
    nodes = {n.id: n for n in collect_nodes(r)}
    P = {k: beamng_to_ac(n.pos) for k, n in nodes.items()}
    mg = compute_mass_geometry(r)
    run = [
        json.loads(x)
        for x in (project.root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    mkey = next(
        x["stages"]["model"]["key"]
        for x in reversed(run)
        if x["stages"].get("model", {}).get("status") in ("ok", "cached")
    )
    model = json.loads(
        (project.root / "stages" / "model" / mkey / "model.json").read_text(
            encoding="utf-8"
        )
    )
    akey = run[-1]["stages"]["attributes"]["key"]
    report = json.loads(
        (project.root / "stages" / "attributes" / akey / "report.json").read_text(
            encoding="utf-8"
        )
    )
    res = {x["target"]: x for x in report["results"]}
    D: list[dict] = []
    info: dict[str, Any] = {"axles": {}}

    def dec(target, value, method, group, conf, unit=None, action="manual_value"):
        D.append(
            {
                "target": target,
                "value": value,
                "unit": unit,
                "method": method,
                "group": group,
                "confidence": conf,
                "action": action,
            }
        )

    def accept(target, why, group, conf):
        x = res[target]
        D.append(
            {
                "target": target,
                "value": x["value"],
                "unit": x["unit"],
                "method": why,
                "group": group,
                "confidence": conf,
                "action": "accept",
            }
        )

    rows = {
        w["name"]: w
        for w in __import__("src.attrs.candidates_dwb", fromlist=["x"])._wheel_rows(r)
    }
    right = {}
    for name, w in rows.items():
        c = beamng_to_ac(
            tuple(
                (a + b) / 2
                for a, b in zip(nodes[w["node1"]].pos, nodes[w["node2"]].pos)
            )
        )
        if c[0] < 0:
            right[name] = (w, c)
    (fname, (fw, fc)), (rname, (rw, rc)) = sorted(
        right.items(), key=lambda kv: -kv[1][1][2]
    )
    wheelbase = fc[2] - rc[2]
    # ---------------- masas y CdG (Ibiza §3)
    hubs = {}
    for axle, w in (("FRONT", fw), ("REAR", rw)):
        sx = axle_structure(r, nodes, w)
        ws = next(x for x in mg.wheels if x.name == w["name"])
        hubs[axle] = round(
            ws.generated_mass
            + sum(nodes[n].mass for n in set(sx["knuckle"]) | {w["node1"], w["node2"]}),
            2,
        )
    eyes_kn5 = model["drivereyes"]["eyes_ac"]
    off = model["origin"]["offset_ac"]
    eyes = tuple(e - o for e, o in zip(eyes_kn5, off))
    drv = (eyes[0], eyes[1] - DRIVER_DROP, eyes[2])
    M, c = mg.total_mass, mg.cog_ac
    centres = {x.name: x.center_ac for x in mg.wheels}
    pts = [
        (
            -(hubs["FRONT"] if centres[n][2] > (fc[2] + rc[2]) / 2 else hubs["REAR"]),
            centres[n],
        )
        for n in centres
    ]
    pts.append((project.driver_mass_kg, drv))
    mb = M + sum(m for m, _ in pts)
    cb = tuple((M * c[i] + sum(m * p[i] for m, p in pts)) / mb for i in range(3))
    total = round(M + project.driver_mass_kg - 2 * hubs["FRONT"] - 2 * hubs["REAR"], 2)
    cgloc = round(1 - (fc[2] - cb[2]) / wheelbase, 4)
    info.update(
        {
            "hub_mass": hubs,
            "body_cog_ac": cb,
            "driver_point_ac": drv,
            "totalmass": total,
            "cg_location": cgloc,
            "wheelbase": wheelbase,
        }
    )
    gm = "Masas: rueda generada + nodos de buje y mangueta; CdG de la carrocería sin bujes y con el piloto 0,55 m bajo sus ojos (cámara de cabina) — ibiza_mass_suspension_proposal.md §2-3"
    dec(
        "ac:car.ini/BASIC/TOTALMASS",
        total,
        f"masa nodal {M:.2f} + piloto {project.driver_mass_kg:g} − ΣHUB_MASS. {gm}",
        "masas",
        "media",
        "kg",
    )
    dec(
        "ac:suspensions.ini/BASIC/CG_LOCATION",
        cgloc,
        f"1 − (z_eje_del − z_CdG)/batalla. {gm}",
        "masas",
        "media",
        "1",
    )
    go = [0.0, round(-(cb[1] + off[1]), 4), round(-(cb[2] + off[2]), 4)]
    dec(
        "ac:car.ini/BASIC/GRAPHICS_OFFSET",
        go,
        "−(CdG de la carrocería + desplazamiento del KN5), X = 0 (Ibiza §3)",
        "masas",
        "media",
        "m",
    )
    # inercia de la carrocería (v6): tensor nodal − bujes + piloto, Steiner
    It = mg.inertia_ac

    def S(d, i):  # noqa: E306
        return sum(x * x for x in d) - d[i] * d[i]

    dcb = tuple(c[i] - cb[i] for i in range(3))
    diag = [
        round(
            It[i][i]
            + M * S(dcb, i)
            + sum(m * S(tuple(p[j] - cb[j] for j in range(3)), i) for m, p in pts),
            1,
        )
        for i in range(3)
    ]
    dec(
        "ac:car.ini/EXPLICIT_INERTIA/INERTIA",
        diag,
        f"tensor nodal − bujes + piloto (Steiner), masa {mb:.1f} kg (v6)",
        "masas",
        "media",
        "kg·m²",
    )
    # ---------------- suspensión
    for axle, w, cw in (("FRONT", fw, fc), ("REAR", rw, rc)):
        sx = axle_structure(r, nodes, w)
        m, ac_links, kind = ac_mapping(sx, P)
        bng = knuckle_model(
            sx, P, [(o, k) for k, os in sx["links"].items() for o in os]
        )
        acm = knuckle_model(sx, P, ac_links)
        sp_nodes, sp = sx["spring"]
        chassis_end = next(n for n in sp_nodes if n not in sx["knuckle"])
        knuckle_end = next(n for n in sp_nodes if n in sx["knuckle"])
        spring = (P[chassis_end], knuckle_end)
        mr = K.motion_ratio(bng, spring)
        T = [-0.04, -0.02, 0.0, 0.02, 0.04]
        sb, sa = K.sweep(bng, T, spring), K.sweep(acm, T, spring)
        mr40 = [
            abs(sb[i + 1]["spring_len"] - sb[i]["spring_len"]) / 0.02 for i in range(4)
        ]
        dev = {
            "toe_deg": max(abs(a["dtoe_deg"] - b["dtoe_deg"]) for a, b in zip(sa, sb)),
            "camber_deg": max(
                abs(a["dcamber_deg"] - b["dcamber_deg"]) for a, b in zip(sa, sb)
            ),
            "mr_ac": K.motion_ratio(acm, spring),
        }
        cam0, toe0 = K.wheel_angles(bng, [0.0] * 6)
        info["axles"][axle] = {
            "kind": kind,
            "mapping": m,
            "mr": mr,
            "mr_variation_40mm": [min(mr40), max(mr40)],
            "beamng": sb,
            "ac": sa,
            "deviation": dev,
            "camber0": cam0,
            "toe0": toe0,
            "spring": sp,
            "damper": sx["damper"][1] if sx["damper"] else None,
            "stop": sx["stop"][1] if sx["stop"] else None,
        }
        pre = f"{axle}: {kind}; MR = {mr:.4f} (cinemática de los enlaces rígidos de BeamNG, src/lab/kinematics.py)"
        pfx = f"ac:suspensions.ini/{axle}/"
        if axle == "FRONT" or "R1" in kind:
            grp = "suspension_cinematica"
            dec(
                pfx + "TYPE",
                "DWB",
                pre
                + f"; desviación del modelo AC frente a BeamNG en ±40 mm: convergencia {dev['toe_deg']:.2f}°, caída {dev['camber_deg']:.2f}°",
                grp,
                "media",
                "enum",
            )
            for key, node in m.items():
                # rueda derecha (x < 0): punto − centro ya tiene +X hacia dentro (como el
                # candidato de candidates_dwb y el [REAR] del R8 LMS oficial)
                val = [round(P[node][i] - cw[i], 4) for i in range(3)]
                dec(
                    pfx + key,
                    val,
                    f"punto {node} − centro de rueda (ejes AC, rueda derecha, +X hacia dentro); {kind}",
                    grp,
                    "media" if "R1" in kind else "alta",
                    "m",
                )
        k_w = sp["beamSpring"] * mr * mr
        dec(
            pfx + "SPRING_RATE",
            round(k_w, 1),
            f"{pre}; muelle {sp['beamSpring']:g} N/m × MR²",
            "suspension_dinamica",
            "media",
            "N/m",
        )
        prog = (max(mr40) / min(mr40)) ** 2 - 1
        if prog < 0.03:
            dec(
                pfx + "PROGRESSIVE_SPRING_RATE",
                0,
                f"rigidez en rueda varía {prog*100:.1f} % en ±40 mm (< 3 %): 0 como en el Ibiza",
                "suspension_dinamica",
                "media",
                "undocumented",
            )
        if sx["damper"]:
            dp = sx["damper"][1]
            for key, src_key in (
                ("DAMP_BUMP", "beamDamp"),
                ("DAMP_REBOUND", "beamDampRebound"),
                ("DAMP_FAST_BUMP", "beamDampFast"),
                ("DAMP_FAST_REBOUND", "beamDampReboundFast"),
            ):
                dec(
                    pfx + key,
                    round(dp[src_key] * mr * mr, 1),
                    f"{pre}; {src_key} {dp[src_key]:g} N·s/m × MR² (DWB: en rueda, Ibiza)",
                    "suspension_dinamica",
                    "media",
                    "N·s/m",
                )
            for key in ("DAMP_FAST_BUMPTHRESHOLD", "DAMP_FAST_REBOUNDTHRESHOLD"):
                dec(
                    pfx + key,
                    round(dp["beamDampVelocitySplit"] / mr, 4),
                    f"{pre}; beamDampVelocitySplit {dp['beamDampVelocitySplit']} m/s / MR",
                    "suspension_dinamica",
                    "media",
                    "m/s",
                )
        if sx["stop"]:
            sp2 = sx["stop"][1]
            dec(
                pfx + "BUMP_STOP_RATE",
                round(sp2["beamLimitSpring"] * mr * mr / 2, -2),
                f"{pre}; beamLimitSpring {sp2['beamLimitSpring']:.0f} × MR² / 2 (opción B lineal del Ibiza; rampa boundZone {sp2['boundZone']})",
                "suspension_dinamica",
                "baja-media",
                "N/m",
            )
            dec(
                pfx + "BUMPSTOP_UP",
                round(sp2["shortBoundRange"] / mr, 4),
                f"{pre}; shortBoundRange {sp2['shortBoundRange']} / MR",
                "suspension_dinamica",
                "media",
                "m",
            )
            dec(
                pfx + "BUMPSTOP_DN",
                round(sp2["longBoundRange"] / mr, 4),
                f"{pre}; longBoundRange {sp2['longBoundRange']} / MR",
                "suspension_dinamica",
                "media",
                "m",
            )
        dec(
            pfx + "PACKER_RANGE",
            0,
            "0 desactiva los packers [Confirmed]; sin packer en la fuente",
            "suspension_dinamica",
            "media",
            "m",
        )
        frac = cgloc if axle == "FRONT" else 1 - cgloc
        load = total * G * frac / 2
        dec(
            pfx + "ROD_LENGTH",
            round(load / k_w, 4),
            f"carga suspendida {load:.1f} N / SPRING_RATE {k_w:.0f} (Ibiza §3); control: precompressionRange {sp.get('precompressionRange')} / MR = {(sp.get('precompressionRange') or 0)/mr:.4f}",
            "suspension_dinamica",
            "media",
            "m",
        )
        dec(
            pfx + "STATIC_CAMBER",
            round(cam0, 2),
            "caída del eje de rueda en los nodos de reposo (negativa = parte alta hacia dentro)",
            "suspension_cinematica",
            "media",
            "deg",
        )
        dec(
            pfx + "TOE_OUT",
            0,
            f"convergencia de los nodos {toe0:+.3f}° ⇒ 0 (como en el Ibiza)",
            "suspension_cinematica",
            "media",
            "m",
        )
        dec(pfx + "HUB_MASS", hubs[axle], gm, "masas", "media", "kg")
        dec(
            pfx + "BASEY",
            round(cw[1] - cb[1], 4),
            "altura del centro de rueda − altura del CdG de la carrocería (Ibiza §3)",
            "masas",
            "media",
            "m",
        )
        # barra estabilizadora
        for ap, sec in r.section_contributions("torsionbars"):
            for row in parse_table(sec).rows:
                ids = [row.values.get(k) for k in ("id1:", "id2:", "id3:", "id4:")]
                if ids[0] in sx["knuckle"]:
                    g = torsion_gain(bng, ids[0], (ids[1], ids[2]), P)
                    kt = float(row.modifiers.get("spring"))
                    dec(
                        f"ac:suspensions.ini/ARB/{axle}",
                        round(kt * g * g, -1),
                        f"torsionbar {row.inline.get('name')} {kt:g} N·m/rad × (dθ/dh = {g:.4f} rad/m)² (cinemática; como ARB FRONT del Ibiza)",
                        "suspension_dinamica",
                        "media",
                        "N/m",
                    )
        if axle == "FRONT":
            # dirección: desplazamiento de la cremallera por grado de giro de la rueda
            rack = m["WBCAR_STEER"]
            base = list(acm.links)

            def toe_at(dx):  # noqa: E306
                acm.links = [
                    (
                        tuple(
                            (
                                p[0]
                                + (dx if q == m["WBTYRE_STEER"] and p == P[rack] else 0)
                                if i == 0
                                else p[i]
                            )
                            for i in range(3)
                        ),
                        q,
                    )
                    for p, q in base
                ]
                p, _ = K.solve(acm, 0.0)
                return K.wheel_angles(acm, p)[1]

            lsrr = 0.002 / abs(toe_at(0.001) - toe_at(-0.001))
            acm.links = base
            hy = next(
                row
                for ap, sec in r.section_contributions("hydros")
                for row in parse_table(sec).rows
            )
            factor = abs(evaluate(hy.inline.get("factor"), var))
            L0 = math.dist(nodes[hy.values["id1:"]].pos, nodes[hy.values["id2:"]].pos)
            lock = float(evaluate(hy.inline.get("steeringWheelLock"), var))
            travel = factor * L0
            behind = P[m["WBTYRE_STEER"]][2] < cw[2]
            ratio = round((-1 if behind else 1) * lock / (travel / lsrr), 2)
            info["steering"] = {
                "lsrr": lsrr,
                "travel": travel,
                "lock": lock,
                "behind_axle": behind,
            }
            dec(
                "ac:car.ini/CONTROLS/STEER_LOCK",
                lock,
                "steeringWheelLock de BeamNG (como el Ibiza v6)",
                "direccion",
                "media",
                "deg",
            )
            dec(
                "ac:car.ini/CONTROLS/LINEAR_STEER_ROD_RATIO",
                round(lsrr, 6),
                "desplazamiento de la bieleta por grado de giro de la rueda, medido en la cinemática (Kunos: ≈ brazo·π/180)",
                "direccion",
                "media",
                "undocumented",
            )
            dec(
                "ac:car.ini/CONTROLS/STEER_RATIO",
                ratio,
                f"−LOCK/(recorrido {travel:.4f} m = |factor| {factor:.4f} × L0 {L0:.4f} / LSRR); signo negativo con la bieleta detrás del eje (convención de Kunos, Ibiza v6)",
                "direccion",
                "media",
                "1",
            )
    # ---------------- aerodinámica A1 (3 alas, convención de los coches oficiales)
    aero = aero_a1(r, var, P, cb)
    if aero:
        info["aero"] = aero["check"]
        dec(
            "ac:aero.ini/HEADER/VERSION",
            2,
            "VERSION=2 de los aero.ini oficiales legibles (AE86, R8 LMS)",
            "aerodinamica",
            "alta",
            "1",
        )
        for n, w in enumerate(aero["wings"]):
            pfx = f"ac:aero.ini/WING_{n}/"
            for key in (
                "NAME",
                "CHORD",
                "SPAN",
                "POSITION",
                "CL_GAIN",
                "CD_GAIN",
                "ANGLE",
            ):
                dec(
                    pfx + key,
                    w[key],
                    w["why"],
                    "aerodinamica",
                    "media" if key != "POSITION" else "media-baja",
                    None,
                )
            for key in ("LUT_AOA_CL", "LUT_AOA_CD"):
                dec(
                    pfx + key,
                    w[key + "_file"],
                    "LUT plana propia: cefaero no depende del ángulo",
                    "aerodinamica",
                    "alta",
                    "text",
                )
                dec(
                    pfx + key + "/table",
                    w[key],
                    w["why"],
                    "aerodinamica",
                    "media",
                    "table:deg->1",
                )
    # ---------------- frenos
    cand = report["candidates"].get("ac:brakes.ini/DATA/HANDBRAKE_TORQUE", [])
    if cand:
        dec(
            "ac:brakes.ini/DATA/HANDBRAKE_TORQUE",
            cand[0]["value"],
            "parkingTorque por rueda trasera; AC aplica HANDBRAKE_TORQUE completo a cada rueda trasera [C]",
            "frenos",
            "alta",
            "N·m",
        )
    dec(
        "ac:brakes.ini/DATA/COCKPIT_ADJUSTABLE",
        0,
        "BeamNG no ofrece ajuste del reparto en cabina (formato; como el Ibiza)",
        "frenos",
        "alta",
        "1",
    )
    # ---------------- motor y transmisión: reglas del Ibiza (familia camsoEngine)
    for t in (
        "ac:engine.ini/ENGINE_DATA/INERTIA",
        "ac:engine.ini/ENGINE_DATA/LIMITER",
        "ac:engine.ini/ENGINE_DATA/MINIMUM",
        "ac:engine.ini/COAST_REF/RPM",
        "ac:engine.ini/COAST_REF/TORQUE",
        "ac:engine.ini/COAST_REF/NON_LINEARITY",
        "ac:drivetrain.ini/CLUTCH/MAX_TORQUE",
        "ac:drivetrain.ini/DIFFERENTIAL/PRELOAD",
    ):
        if res.get(t, {}).get("requires_approval"):
            accept(
                t,
                f"regla {res[t]['rule']} del Ibiza (misma familia camsoEngine; usada en la base probada en el juego)",
                "motor_transmision",
                "media",
            )
    # ---------------- neumáticos (v6, NO CALIBRADO)
    street = sdk_street()
    therm = thermal_values(street)
    info["tyre_library"] = len(street)
    for axle, w in (("FRONT", fw), ("REAR", rw)):
        pfx = f"ac:tyres.ini/{axle}/"
        accept(
            pfx + "RADIUS",
            "regla de radio de nodos sin carga (la del Ibiza)",
            "neumatico",
            "media",
        )
        accept(
            pfx + "PRESSURE_STATIC",
            "pressurePSI de BeamNG (regla del Ibiza)",
            "neumatico",
            "alta",
        )
        width = res[pfx + "WIDTH"]["value"]
        pres = res[pfx + "PRESSURE_STATIC"]["value"]
        ws = next(x for x in mg.wheels if x.name == w["name"])
        dec(
            pfx + "ANGULAR_INERTIA",
            round(ws.spin_inertia, 4),
            "inercia de giro de los anillos de pressureWheels (sin disco ni buje), como v6",
            "neumatico",
            "media",
            "undocumented",
        )
        dec(
            pfx + "NAME",
            "beam2ac generico ST NO CALIBRADO",
            "nombre propio",
            "neumatico",
            "alta",
            "text",
        )
        dec(
            pfx + "SHORT_NAME",
            "DG",
            "nombre corto propio (como el Ibiza)",
            "neumatico",
            "alta",
            "text",
        )
        dec(
            pfx + "WEAR_CURVE",
            "beam2ac_flat_wear.lut",
            "curva neutra propia (V1)",
            "neumatico",
            "alta",
            "text",
        )
        dec(
            pfx + "WEAR_CURVE/table",
            [[0, 100], [1000, 100]],
            "sin desgaste (V1, curva neutra propia, no calibrada)",
            "neumatico",
            "baja",
            "table:km->%",
        )
        for key, (val, why) in tyre_values(width, pres, street).items():
            dec(
                pfx + key,
                val,
                why + " — método v6, NO CALIBRADO para este neumático",
                "neumatico",
                "baja",
                "undocumented",
            )
        tp = f"ac:tyres.ini/THERMAL_{axle}/"
        dec(
            tp + "PERFORMANCE_CURVE",
            "beam2ac_flat_tcurve.lut",
            "curva neutra propia (V1)",
            "neumatico",
            "alta",
            "text",
        )
        dec(
            tp + "PERFORMANCE_CURVE/table",
            [[0, 1.0], [50, 1.0], [100, 1.0], [150, 1.0], [200, 1.0]],
            "agarre independiente de la temperatura (V1)",
            "neumatico",
            "baja",
            "table:°C->1",
        )
        for key, (val, why) in therm.items():
            dec(
                tp + key,
                val,
                why + " — NO CALIBRADO",
                "neumatico",
                "baja",
                "undocumented",
            )
    dec(
        "ac:tyres.ini/HEADER/VERSION",
        7,
        "versión de los neumáticos del SDK usados (VERSION 7, como v6)",
        "neumatico",
        "alta",
        "1",
    )
    dec(
        "ac:tyres.ini/COMPOUND_DEFAULT/INDEX",
        0,
        "un solo compuesto",
        "neumatico",
        "alta",
        "1",
    )
    return D, info


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    from src.project.manifest import load_project

    project = load_project(a.project)
    D, info = derive(project)
    out = new_output_dir(a.out)
    (out / "derivation.json").write_text(
        json.dumps(
            {"decisions": D, "info": info}, indent=1, ensure_ascii=False, default=str
        ),
        encoding="utf-8",
    )
    groups: dict[str, int] = {}
    for d in D:
        groups[d["group"]] = groups.get(d["group"], 0) + 1
    print(json.dumps({"decisiones": len(D), "grupos": groups}, ensure_ascii=False))
    if not a.apply:
        return 0
    from src.app.service import ReviewService
    from src.attrs.store import Catalog
    from src.pipeline.physics_preview import (
        load_vehicle_zip,
        load_vehicle_dir,
        preview_physics,
    )

    s = project.source
    src = (
        load_vehicle_dir(Path(s.path), s.pc)
        if s.kind == "dir"
        else load_vehicle_zip(
            Path(s.path), Path(s.common_zip) if s.common_zip else None, s.pc
        )
    )
    svc = ReviewService(preview_physics(src, project.driver_mass_kg), project.root)
    cat = Catalog.load(project.catalog_path, project.root)
    done = {d.target for d in cat.decisions if d.id.startswith("ui.")}
    applied = 0
    for d in D:
        if d["target"] in done or d["target"] not in cat.attributes:
            continue
        unit = cat.attributes[d["target"]].unit
        body = {
            "action": d["action"],
            "target": d["target"],
            "scope": "vehicle",
            "status": "provisional",
            "author": "derive_physics_completion.py (fase C5)",
            "justification": f"[{d['group']}; confianza {d['confidence']}; PENDIENTE DE VALIDACIÓN] {d['method']}",
            "evidence": [
                {
                    "type": "report",
                    "path": str(out / "derivation.json"),
                    "locator": d["target"],
                }
            ],
        }
        if d["action"] == "accept":
            body["source"] = "result"
        else:
            body["value"], body["unit"] = d["value"], unit
        svc.decide(body)
        applied += 1
    print(f"decisiones registradas: {applied}")
    gates_path = project.root / "validation_gates.json"
    if gates_path.exists():
        print(f"ya existe {gates_path}: no se sobrescribe")
    else:
        from src.safe_paths import ensure_safe_output_path

        ensure_safe_output_path(gates_path).write_text(
            json.dumps(
                {"schema": "beam2ac.validation_gates/1", "gates": GATES},
                indent=1,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"puertas de validación: {sorted(GATES)}")
    return 0


GATES = {
    "neumatico": {
        "status": "pendiente",
        "reason": "modelo de neumático estadístico (medianas y regresión de los ST del SDK), NO CALIBRADO "
        "frente al neumático de BeamNG (modelo de fricción por nodos sin equivalencia demostrada)",
        "how": "medir en BeamNG (con autorización) Fx frente a deslizamiento longitudinal y Fy frente a ángulo "
        "de deriva a 3 cargas verticales; ajustar DY0/DX0/LS_EXPY/LS_EXPX/FRICTION_LIMIT_ANGLE y "
        "comparar con la telemetría de AC (picos de fuerza ±10 %, deslizamiento del pico ±20 %)",
    },
    "suspension_cinematica": {
        "status": "pendiente",
        "reason": "DWB delantero (5 enlaces medidos) y trasera R1 equivalente (convergencia hasta 0,46° de "
        "BeamNG en ±40 mm; caída 0,05°); solver de AC con puntos coincidentes sin probar",
        "how": "en AC: carga sin errores de suspensión; telemetría de caída y convergencia en compresión "
        "frente a src/lab/kinematics.py (±0,1° caída, ±0,5° convergencia)",
    },
    "suspension_dinamica": {
        "status": "pendiente",
        "reason": "muelles, amortiguadores, topes y barras en rueda con la relación de palanca medida "
        "(k·MR², c·MR², rango/MR, K_tope/2); topes con la opción B lineal del Ibiza",
        "how": "en AC: altura en reposo (el buje en la base: ROD_LENGTH), frecuencia de rebote y balanceo "
        "frente a BeamNG (±10 %)",
    },
    "masas": {
        "status": "pendiente",
        "reason": "CdG de la carrocería con el piloto aproximado (0,55 m bajo los ojos) y HUB_MASS = rueda + "
        "mangueta; inercia por masas puntuales",
        "how": "en AC: reparto de pesos en reposo (telemetría de carga por rueda) frente a BeamNG (±2 %)",
    },
    "direccion": {
        "status": "pendiente",
        "reason": "STEER_LOCK, STEER_RATIO y LINEAR_STEER_ROD_RATIO desde la cinemática y el hydro de BeamNG",
        "how": "en AC: ángulo de las ruedas a tope frente a BeamNG (±1°); sin roturas de la rueda interior",
    },
    "aerodinamica": {
        "status": "pendiente",
        "reason": "tres alas de 1 m² que reproducen cefaero (área = CHORD·SPAN [H]); sin arrastre lateral ni "
        "vertical; posiciones respecto al CdG aproximado",
        "how": "en AC: arrastre y cargas a 100 y 200 km/h en recta (telemetría) frente a §6.5 (±5 %)",
    },
    "motor_transmision": {
        "status": "pendiente",
        "reason": "inercia, limitador, mínimo, freno motor, embrague y PRELOAD con las reglas del Ibiza "
        "(aceptadas allí; aquí sin prueba)",
        "how": "en AC: aceleración en 2.ª y 3.ª y freno motor frente a BeamNG",
    },
}


if __name__ == "__main__":
    sys.exit(main())
