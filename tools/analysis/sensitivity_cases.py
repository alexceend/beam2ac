"""Sensibilidad de frenos, diferencial, transmisión y embrague (laboratorio).

    python tools/analysis/sensitivity_cases.py
        --vehicles <investigación de frenos>/vehicles/vehicles.json
        --lab <investigación de frenos>/lab/lab_cases.json

(<investigación de frenos> = output/research_frenos_diferencial_transmision_2026-10-26)
        --out output/<nueva>

Usa solo src/lab (ecuaciones reconstruidas del código de ambos juegos) y datos
DECLARADOS de los JBeam. Cada bloque lleva ``kind``: ``calculado`` (datos reales +
ecuación), ``escenario_hipotetico`` (entradas elegidas y etiquetadas) o ``desconocido``.
Nada es una medición. No se aplica nada a la física de producción.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.lab import drivetrain_lab as L  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

PEDALS = (0.0, 0.25, 0.5, 0.75, 1.0)
PADS = {
    "seat_ibiza_acfr/35b84": "semi-race",
    "spicy_mandala/56a86": "sport",
    "composed_fox/4974f": "premium",
    "rune_susui_espol_vtec/03496": "sport",
    "porsche_gt3_rs/18547": "semi-race",
}
UNKNOWN_BRAKE = [
    "aplicación del par en el núcleo C++ de BeamNG (brakingTorque, brakeSpring): sin "
    "código",
    "filtrado de la entrada de pedal hasta electrics.brake (input → electrics): no "
    "reconstruido",
    "retardo de presión (0,04 s) y ABS: dinámicos, fuera de un cálculo estático",
]


def _rel(a: float, ref: float) -> float | None:
    return None if ref == 0 else (a - ref) / ref


def brakes(v: dict[str, Any]) -> dict[str, Any]:
    f = next(b for b in v["brakes"] if b["wheel"].startswith("F"))["declared"]
    r = next(b for b in v["brakes"] if b["wheel"].startswith("R"))["declared"]

    def bng(p: float, eta: float = 1.0) -> tuple[float, float]:
        tf = L.bng_wheel_brake_torque_nm(
            f["brakeTorque"],
            p,
            f["brakeInputSplit"],
            f["brakeSplitCoef"],
            thermal_efficiency=eta,
        )
        tr = L.bng_wheel_brake_torque_nm(
            r["brakeTorque"],
            p,
            r["brakeInputSplit"],
            r["brakeSplitCoef"],
            thermal_efficiency=eta,
        )
        return tf, tr

    conversions: dict[str, tuple[float, float]] = {}
    for p_ref in (1.0, 0.5):
        conversions[f"conversión a pedal {p_ref:g} (calculada)"] = (
            L.ac_brakes_from_bng_at_pedal(
                f["brakeTorque"],
                f["brakeInputSplit"],
                f["brakeSplitCoef"],
                r["brakeTorque"],
                r["brakeInputSplit"],
                r["brakeSplitCoef"],
                p_ref,
            )
        )
    mt1, fs1 = conversions["conversión a pedal 1 (calculada)"]
    for fs in (0.70, 0.75, 0.80):  # escenarios hipotéticos con MAX_TORQUE de pedal 1
        conversions[f"FRONT_SHARE={fs:.2f} (escenario hipotético)"] = (mt1, fs)
    rows = []
    for name, (mt, fs) in conversions.items():
        cfg = L.ACBrakeInputs(mt, fs, r["parkingTorque"])
        for p in PEDALS:
            bf, br = bng(p)
            af, _, ar, _ = L.ac_wheel_brake_torques_nm(cfg, p)
            rows.append(
                {
                    "conversión": name,
                    "MAX_TORQUE": round(mt, 2),
                    "FRONT_SHARE": round(fs, 4),
                    "pedal": p,
                    "bng_del": round(bf, 2),
                    "bng_tras": round(br, 2),
                    "ac_del": round(af, 2),
                    "ac_tras": round(ar, 2),
                    "err_abs_del": round(af - bf, 2),
                    "err_abs_tras": round(ar - br, 2),
                    "err_rel_del": (
                        None if _rel(af, bf) is None else round(_rel(af, bf), 4)
                    ),
                    "err_rel_tras": (
                        None if _rel(ar, br) is None else round(_rel(ar, br), 4)
                    ),
                }
            )
    pad = PADS[v["vehicle"]]
    eta20 = L.bng_thermal_efficiency(20, pad)
    thermal = [
        {
            "pedal": p,
            "bng_del_nominal": round(bng(p)[0], 2),
            "bng_del_20C": round(bng(p, eta20)[0], 2),
            "eta_20C": round(eta20, 4),
        }
        for p in PEDALS
    ]
    # Sensibilidad analítica de AC (frenos de AC):
    # T_del = M·s·p, T_tras = M·(1−s)·p
    sens = {
        "dT_del/dMAX_TORQUE": "s·p",
        "dT_del/dFRONT_SHARE": "M·p",
        "dT_tras/dFRONT_SHARE": "−M·p",
        "dT/dpedal": "M·s (del.), M·(1−s) (tras.): lineal",
        "BeamNG dT_tras/dpedal": f"{r['brakeTorque']:.1f} hasta "
        f"s={r['brakeInputSplit']}, "
        f"{r['brakeTorque'] * r['brakeSplitCoef']:.1f} después",
        "dT/dη (BeamNG)": "T_nominal (proporcional)",
    }
    missing_vs_zero = {
        "MAX_TORQUE ausente": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(None, fs1, 0), 1.0
        ),
        "MAX_TORQUE=0": L.ac_wheel_brake_torques_nm(L.ACBrakeInputs(0.0, fs1, 0), 1.0),
        "FRONT_SHARE ausente": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(mt1, None, 0), 1.0
        ),
        "FRONT_SHARE=0": L.ac_wheel_brake_torques_nm(L.ACBrakeInputs(mt1, 0.0, 0), 1.0),
        "HANDBRAKE ausente, freno de mano a fondo": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(mt1, fs1, None), 0.0, handbrake=1.0
        ),
        "nota": "en AC ausente = 0 (getFloat); el validador distingue: MAX_TORQUE=0 y "
        "ausente se rechazan; FRONT_SHARE=0 es válido (todo atrás) y ausente se "
        "rechaza",
    }
    handbrake = []
    for p in (0.0, 0.5, 1.0):
        cfg = L.ACBrakeInputs(
            mt1, conversions["conversión a pedal 1 (calculada)"][1], r["parkingTorque"]
        )
        _, _, ar, _ = L.ac_wheel_brake_torques_nm(cfg, p, handbrake=1.0)
        br = L.bng_wheel_brake_torque_nm(
            r["brakeTorque"],
            p,
            r["brakeInputSplit"],
            r["brakeSplitCoef"],
            parking_torque_nm=r["parkingTorque"],
            parkingbrake_input=1.0,
        )
        handbrake.append(
            {"pedal": p, "bng_tras": round(br, 2), "ac_tras": round(ar, 2)}
        )
    return {
        "kind": "calculado (escenarios FRONT_SHARE etiquetados)",
        "rows": rows,
        "termica": thermal,
        "sensibilidad": sens,
        "ausente_vs_cero": missing_vs_zero,
        "freno_de_mano": handbrake,
        "desconocido": UNKNOWN_BRAKE,
        "dato_que_falta": "régimen de pedal de uso y temperatura de referencia "
        "(decisión); "
        "medición del par por rueda en ambos juegos a pedal parcial",
    }


def diff(v: dict[str, Any], lab: dict[str, Any]) -> dict[str, Any] | None:
    d = next((x for x in v["powertrain"].values() if x["type"] == "differential"), None)
    if d is None:
        return None
    p = d["declared"]
    if p.get("diffType") != "lsd":
        return {
            "kind": "calculado",
            "nota": "diferencial abierto: sin tope de transferencia",
        }
    rows = []
    t_in_rows = [r for r in lab["diferencial"]["rows"]]
    low, high = 300.0, 4000.0  # escenario hipotético de adherencia (N·m en la rueda)
    for base in t_in_rows:
        t_in = base["T_in_nm"]
        eng_ratio = None  # T_in ya es lado rueda: se pasa T_motor·ratio = T_in
        bng_cap = L.bng_lsd_difference_cap_nm(
            t_in, p["lsdPreload"], p["lsdLockCoef"], p["lsdRevLockCoef"]
        )
        variants = {
            "BeamNG (referencia de fórmula)": bng_cap,
            "AC actual (PRELOAD=lsdPreload, sin "
            "POWER/COAST)": L.ac_lsd_difference_cap_nm(
                t_in, 1.0, p["lsdPreload"], None, None
            ),
            "AC PRELOAD=0, sin POWER/COAST": L.ac_lsd_difference_cap_nm(
                t_in, 1.0, 0, None, None
            ),
            "AC factor 1 (P, coef, rev)": L.ac_lsd_difference_cap_nm(
                t_in, 1.0, p["lsdPreload"], p["lsdLockCoef"], p["lsdRevLockCoef"]
            ),
            "AC factor 2 (2P, 2coef, 2rev)": L.ac_lsd_difference_cap_nm(
                t_in,
                1.0,
                2 * p["lsdPreload"],
                2 * p["lsdLockCoef"],
                2 * p["lsdRevLockCoef"],
            ),
            "AC factor 2 solo PRELOAD": L.ac_lsd_difference_cap_nm(
                t_in, 1.0, 2 * p["lsdPreload"], None, None
            ),
        }
        del eng_ratio
        for name, cap in variants.items():
            rows.append(
                {
                    "caso": base["caso"],
                    "T_in_nm": t_in,
                    "variante": name,
                    "tope_dif_nm": round(cap, 1),
                    "eje_rueda_baja_300_alta_4000": round(
                        L.max_axle_torque_with_low_grip_nm(t_in, low, high, cap), 1
                    ),
                    "eje_simetrico_alta_ambas": round(
                        L.max_axle_torque_with_low_grip_nm(t_in, high, high, cap), 1
                    ),
                }
            )
    return {
        "kind": "calculado (adherencia: escenario hipotético 300/4000 N·m)",
        "rows": rows,
        "niveles": {
            "1_tope_de_formula": "tope_dif_nm (E3)",
            "2_reparto_por_adherencia": "eje_* (modelo cuasiestático idealizado, E3)",
            "3_respuesta_dinamica": "desconocido: muelle+amortiguador (BeamNG) frente "
            "a umbral "
            "con ventana 0,1 rad/s (AC); requiere medición (E4)",
        },
    }


def transmission(v: dict[str, Any], lab: dict[str, Any]) -> dict[str, Any]:
    gb = lab["transmision"]
    rows = gb["rows"]
    span = [r["diferencia_pct"] for r in rows]
    t = lab["embrague"]["motor_pico_nm"]
    lock = lab["embrague"]["bng_lockTorque_por_defecto"]
    ratio_err = {
        f"error_relativo_FINAL_{e:+.0%}": {
            "par_rueda": f"{e:+.1%}",
            "velocidad_por_rpm": f"{1 / (1 + e) - 1:+.2%}",
        }
        for e in (-0.05, -0.01, 0.01, 0.05)
    }
    return {
        "kind": "calculado",
        "perdidas_pct_por_marcha": [(r["gear"], r["diferencia_pct"]) for r in rows],
        "rango_pct": [min(span), max(span)],
        "nota_perdidas": "solo fricción estática declarada; dynamicFriction y "
        "torqueLossCoef "
        "0 por defecto; no es una constante universal",
        "propagacion_error_relacion": ratio_err,
        "embrague": {
            "motor_pico_nm": t,
            "lockTorque_bng_defecto": lock,
            "margen_lockTorque_sobre_pico": round(lock / t, 3),
            "bng_estatico": "no desliza en régimen (lockTorque > par pico por "
            "construcción: "
            "1,25·Tmáx + término de inercia)",
            "ac_cerrado": "rígido: no limita",
            "ac_abierto_fraccion": lab["embrague"]["ac_fraccion_con_MAX_TORQUE_igual"],
            "no_inferible_estaticamente": "deslizamiento transitorio (suelta de "
            "embrague, cambio), "
            "par de inercia y modelo de pedal (BeamNG: ángulo, "
            "AC: lineal)",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vehicles", type=Path, required=True)
    ap.add_argument("--lab", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    vehicles = json.loads(args.vehicles.read_text(encoding="utf-8"))
    lab = json.loads(args.lab.read_text(encoding="utf-8"))["vehiculos"]
    res = {}
    for v in vehicles:
        name = v["vehicle"]
        res[name] = {
            "frenos": brakes(v),
            "diferencial": diff(v, lab[name]),
            "transmision_embrague": transmission(v, lab[name]),
        }
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "sensitivity.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(out / "sensitivity.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
