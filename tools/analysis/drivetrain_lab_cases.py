"""Casos del laboratorio (src/lab) sobre los vehículos disponibles y casos hipotéticos.

    python tools/analysis/drivetrain_lab_cases.py
        --vehicles output/<…>/vehicles/vehicles.json
        --preload output/research_powerlut_preload_2026-10-26/preload_run2/preload_survey.json
        --out output/<nueva>

Todo es CÁLCULO con ecuaciones reconstruidas (``kind = "calculado"``) o EJEMPLO
matemático con entradas inventadas y etiquetadas (``kind = "ejemplo_hipotetico"``).
Nada aquí es una medición ni demuestra equivalencia física.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.extract.engine_camso import build_engine_model  # noqa: E402
from src.jbeam import PartIndex, load_pc, resolve  # noqa: E402
from src.lab import drivetrain_lab as L  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_zip  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

VEH = Path("G:/SteamLibrary/steamapps/common/BeamNG.drive/content/vehicles")
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
PADS = {  # wheel_props_flags.txt (props resueltas por rueda)
    "seat_ibiza_acfr/35b84": "semi-race",
    "spicy_mandala/56a86": "sport",
    "composed_fox/4974f": "premium",
    "rune_susui_espol_vtec/03496": "sport",
    "porsche_gt3_rs/18547": "semi-race",
}
PEDALS = (0.1, 0.3, 0.5, 0.75, 1.0)


def engines() -> dict[str, Any]:
    out = {}
    d = Path("sample_beamng/vehicles/seat_ibiza_acfr")
    m = build_engine_model(
        resolve(PartIndex.from_directories(d), load_pc(d / "35b84.pc"))
    )
    out["seat_ibiza_acfr/35b84"] = m
    for z in (
        "spicy_mandala",
        "composed_fox",
        "rune_susui_espol_vtec",
        "porsche_gt3_rs",
    ):
        s = load_vehicle_zip(MODS / f"{z}.zip", VEH / "common.zip")
        out[f"{z}/{s.variant}"] = build_engine_model(resolve(s.index, s.pc))
    return out


def brake_cases(v: dict[str, Any]) -> dict[str, Any]:
    f = next(b for b in v["brakes"] if b["wheel"].startswith("F"))
    r = next(b for b in v["brakes"] if b["wheel"].startswith("R"))
    fd, rd = f["declared"], r["declared"]
    mt, fs = L.ac_brakes_from_bng_at_pedal(
        fd["brakeTorque"],
        fd["brakeInputSplit"],
        fd["brakeSplitCoef"],
        rd["brakeTorque"],
        rd["brakeInputSplit"],
        rd["brakeSplitCoef"],
        1.0,
    )
    cfg = L.ACBrakeInputs(mt, fs, rd["parkingTorque"])
    rows = []
    for p in PEDALS:
        bf = L.bng_wheel_brake_torque_nm(
            fd["brakeTorque"], p, fd["brakeInputSplit"], fd["brakeSplitCoef"]
        )
        br = L.bng_wheel_brake_torque_nm(
            rd["brakeTorque"], p, rd["brakeInputSplit"], rd["brakeSplitCoef"]
        )
        af, _, ar, _ = L.ac_wheel_brake_torques_nm(cfg, p)
        rows.append(
            {
                "pedal": p,
                "bng_front": round(bf, 2),
                "bng_rear": round(br, 2),
                "ac_front": round(af, 2),
                "ac_rear": round(ar, 2),
                "bng_front_share": round(bf / (bf + br), 4),
                "ac_front_share": round(fs, 4),
                "rear_ratio_ac_bng": round(ar / br, 4) if br else None,
            }
        )
    pad = PADS[v["vehicle"]]
    temps = list(range(0, 801, 5))
    effs = [(L.bng_thermal_efficiency(t, pad), t) for t in temps]
    peak = max(effs)
    return {
        "kind": "calculado",
        "conversion_pedal_1": {
            "MAX_TORQUE": round(mt, 2),
            "FRONT_SHARE": round(fs, 4),
            "HANDBRAKE_TORQUE_por_rueda": rd["parkingTorque"],
        },
        "pedal_sweep": rows,
        "thermal": {
            "pad": pad,
            "eff_20C": round(L.bng_thermal_efficiency(20, pad), 4),
            "eff_max": round(peak[0], 4),
            "t_max_C": peak[1],
            "bng_front_20C_nm": round(
                fd["brakeTorque"] * L.bng_thermal_efficiency(20, pad), 1
            ),
            "ac_front_sin_TEMPS_nm": round(mt * fs, 1),
        },
    }


def diff_cases(v: dict[str, Any], m: Any) -> dict[str, Any] | None:
    diff = next(
        (d for d in v["powertrain"].values() if d["type"] == "differential"), None
    )
    gb = next(
        (d for d in v["powertrain"].values() if d["type"] == "manualGearbox"), None
    )
    if diff is None or gb is None:
        return None
    p = diff["declared"]
    final = p.get("gearRatio")
    gears = [g for g in gb["declared"]["gearRatios"] if g > 0]
    t_peak = m.peak_torque.net
    out: dict[str, Any] = {
        "kind": "calculado",
        "diffType": p.get("diffType"),
        "rows": [],
    }
    if p.get("diffType") != "lsd":
        out["note"] = (
            "abierto: BeamNG solo pierde 0,034·|T|·clamp(Δω, ±0,1) en cada salida (l. 35-38); sin transferencia"
        )
        return out
    pre, c, rc = p.get("lsdPreload"), p.get("lsdLockCoef"), p.get("lsdRevLockCoef")
    for label, g, sign in (
        ("1.ª tracción", gears[0], 1),
        (f"{len(gears)}.ª tracción", gears[-1], 1),
        ("1.ª retención 20 %", gears[0], -0.2),
    ):
        t_in = sign * t_peak * g * final  # lado rueda, sin pérdidas
        bng = L.bng_lsd_difference_cap_nm(t_in, pre, c, rc)
        h1 = L.ac_lsd_difference_cap_nm(sign * t_peak, g * final, pre, c, rc)
        h2 = (
            L.ac_lsd_difference_cap_nm(sign * t_peak, g * final, 2 * pre, 2 * c, 2 * rc)
            if not (2 * c >= 1 and 2 * rc >= 1)
            else "Spool"
        )
        base = L.ac_lsd_difference_cap_nm(sign * t_peak, g * final, pre, None, None)
        out["rows"].append(
            {
                "caso": label,
                "T_in_nm": round(t_in, 1),
                "bng_dif_max": round(bng, 1),
                "ac_solo_preload_factor1": round(base, 1),
                "ac_H1_factor1": round(h1, 1),
                "ac_H2_factor2": h2 if isinstance(h2, str) else round(h2, 1),
            }
        )
    return out


def clutch_cases(v: dict[str, Any], m: Any) -> dict[str, Any]:
    lock = L.bng_clutch_default_lock_torque_nm(
        m.peak_torque.net, m.rpm_ceiling, m.raw.inertia
    )
    return {
        "kind": "calculado",
        "bng_lockTorque_por_defecto": round(lock, 1),
        "nota": "maxTorque del motor = pico neto de la tabla (getTorqueData usa la curva con "
        "fricción en k+1; diferencia < 0,01 N·m); maxRPM = techo tras el limitador",
        "ac_fraccion_con_MAX_TORQUE_igual": {
            f"slip_{s}_rad_s": round(L.ac_clutch_torque_nm(s, 1, lock) / lock, 3)
            for s in (1, 4, 10, 40, 100)
        },
        "motor_pico_nm": round(m.peak_torque.net, 1),
    }


def gearing_cases(v: dict[str, Any], m: Any) -> dict[str, Any] | None:
    gb = next(
        (d for d in v["powertrain"].values() if d["type"] == "manualGearbox"), None
    )
    diff = next(
        (d for d in v["powertrain"].values() if d["type"] == "differential"), None
    )
    if gb is None or diff is None:
        return None
    fr = gb["declared"].get("friction", 0.0)
    final = diff["declared"]["gearRatio"]
    dfr = diff["declared"].get("friction", 0.0)
    t = m.peak_torque.net
    rows = []
    for g in [x for x in gb["declared"]["gearRatios"] if x > 0]:
        ac = L.wheel_torque_nm(t, g, final)
        # BeamNG: fricción de caja (ω > 1 rad/s) y del diferencial (en su entrada, l. 32-33)
        bng = (
            L.wheel_torque_nm(t, g, 1.0, gearbox_friction_nm=fr, input_rad_s=100) - dfr
        ) * final
        rows.append(
            {
                "gear": g,
                "ac_nm": round(ac, 1),
                "bng_sin_dinamica_nm": round(bng, 1),
                "diferencia_pct": round(100 * (ac - bng) / bng, 2),
            }
        )
    return {
        "kind": "calculado",
        "friccion_caja_nm": fr,
        "friccion_dif_nm": dfr,
        "nota": "solo fricciones estáticas declaradas; dynamicFriction y torqueLossCoef no "
        "declarados (0 por defecto); régimen de par máximo",
        "rows": rows,
    }


def hypothetical() -> dict[str, Any]:
    """Ejemplos matemáticos con entradas inventadas (etiquetados)."""
    ex = {"kind": "ejemplo_hipotetico"}
    # Ejemplo 1: LSD con precarga y bloqueo por par, rueda con poca adherencia
    t_in, low, high = 2000.0, 150.0, 2500.0
    bng = L.bng_lsd_difference_cap_nm(t_in, 100, 0.25, 0.025)
    ex["lsd_poca_adherencia"] = {
        "entradas": {
            "T_in": t_in,
            "lsdPreload": 100,
            "lsdLockCoef": 0.25,
            "rueda_baja": low,
            "rueda_alta": high,
        },
        "bng_dif_max": bng,
        "eje_bng": L.max_axle_torque_with_low_grip_nm(t_in, low, high, bng),
        "eje_ac_preload100_sin_power": L.max_axle_torque_with_low_grip_nm(
            t_in, low, high, 100
        ),
        "eje_ac_preload200_sin_power": L.max_axle_torque_with_low_grip_nm(
            t_in, low, high, 200
        ),
        "eje_abierto": L.max_axle_torque_with_low_grip_nm(t_in, low, high, 0),
    }
    # Ejemplo 2: precarga nula, POWER = COAST
    ex["precarga_nula_power_igual_coast"] = {
        "entradas": {
            "T_motor": 200,
            "ratio_total": 12,
            "PRELOAD": 0,
            "POWER": 0.3,
            "COAST": 0.3,
        },
        "ac_traccion": L.ac_lsd_difference_cap_nm(200, 12, 0, 0.3, 0.3),
        "ac_retencion_40": L.ac_lsd_difference_cap_nm(-40, 12, 0, 0.3, 0.3),
        "bng_traccion_preload0_coef0_15": L.bng_lsd_difference_cap_nm(
            2400, 0, 0.15, 0.15
        ),
    }
    # Ejemplo 3: POWER y COAST distintos
    ex["power_distinto_coast"] = {
        "entradas": {
            "T_motor": 200,
            "ratio_total": 12,
            "PRELOAD": 20,
            "POWER": 0.6,
            "COAST": 0.1,
        },
        "ac_traccion": L.ac_lsd_difference_cap_nm(200, 12, 20, 0.6, 0.1),
        "ac_retencion_200": L.ac_lsd_difference_cap_nm(-200, 12, 20, 0.6, 0.1),
    }
    # Ejemplo 4: reparto de freno extremo
    ex["reparto_extremo"] = {
        "FRONT_SHARE_1": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(2000, 1.0, 0), 1.0
        ),
        "FRONT_SHARE_0": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(2000, 0.0, 0), 1.0
        ),
        "FRONT_SHARE_ausente": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(2000, None, 0), 1.0
        ),
        "limitado_por_setup_0_6_0_8": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(2000, 1.0, 0, limit_down=0.6, limit_up=0.8), 1.0
        ),
    }
    # Ejemplo 5: freno de mano ausente
    ex["freno_mano_ausente"] = {
        "ac": L.ac_wheel_brake_torques_nm(
            L.ACBrakeInputs(2000, 0.7, None), 0.0, handbrake=1.0
        ),
        "bng_parking_ausente": L.bng_wheel_brake_torque_nm(
            500, 0.0, parking_torque_nm=None, parkingbrake_input=1.0
        ),
    }
    # Ejemplo 6: embrague con límite inferior al par del motor
    ex["embrague_debil"] = {
        "entradas": {"T_motor": 400, "lockTorque_BNG": 300, "MAX_TORQUE_AC": 300},
        "bng": "desliza: el muelle se recorta a ±lockTorque (frictionClutch.lua l. 104)",
        "ac_cerrado": "rígido: AC no compara el par con MAX_TORQUE con el embrague cerrado (el cálculo 2WD de AC)",
        "ac_cerrado_rigido_ejemplo": L.ac_clutch_is_rigid(1.0, 600.0, 590.0),
    }
    # Ejemplo 7: relaciones ausentes o inválidas
    bad = {}
    for label, g, f in (
        ("marcha_ausente", None, 4.0),
        ("final_0", 3.0, 0.0),
        ("marcha_nan", float("nan"), 4.0),
    ):
        try:
            L.wheel_torque_nm(200, g, f)
            bad[label] = "aceptada"
        except L.LabInputError as exc:
            bad[label] = f"rechazada: {exc}"
    ex["relaciones_invalidas"] = bad
    return ex


def lsd_spool_counterexamples(preload_survey: Path) -> dict[str, Any]:
    """Filas LSD cuya conversión con factor 2 daría POWER ≥ 1 y COAST ≥ 1 (Spool en AC)."""
    d = json.loads(preload_survey.read_text(encoding="utf-8"))
    hits, total = [], 0
    for r in d["rows"]:
        c, rc = r.get("lsdLockCoef"), r.get("lsdRevLockCoef")
        if not isinstance(c, (int, float)):
            continue
        total += 1
        rc = c if rc is None else rc
        if not isinstance(rc, (int, float)):
            continue
        if 2 * min(c, 1) >= 1 and 2 * min(rc, 1) >= 1:
            hits.append(
                {
                    "origen": r["origin"],
                    "pieza": r["part"],
                    "lsdLockCoef": c,
                    "lsdRevLockCoef": rc,
                }
            )
        if c >= 1 or rc >= 1:
            r["_coef_ge_1"] = True
    ge1 = [
        {
            "origen": r["origin"],
            "pieza": r["part"],
            "c": r["lsdLockCoef"],
            "rc": r.get("lsdRevLockCoef"),
        }
        for r in d["rows"]
        if r.get("_coef_ge_1")
    ]
    return {
        "kind": "calculado",
        "filas_con_coef_numerico": total,
        "spool_con_factor2": hits,
        "coef_mayor_o_igual_1": ge1,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vehicles", type=Path, required=True)
    ap.add_argument("--preload", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    vehicles = json.loads(args.vehicles.read_text(encoding="utf-8"))
    eng = engines()
    res: dict[str, Any] = {"vehiculos": {}}
    for v in vehicles:
        name = v["vehicle"]
        m = eng[name]
        res["vehiculos"][name] = {
            "frenos": brake_cases(v),
            "diferencial": diff_cases(v, m),
            "embrague": clutch_cases(v, m),
            "transmision": gearing_cases(v, m),
        }
    res["ejemplos"] = hypothetical()
    res["lsd_spool"] = lsd_spool_counterexamples(args.preload)
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "lab_cases.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(out / "lab_cases.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
