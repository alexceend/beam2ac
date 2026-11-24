"""G2.22 F1 — control básico sin intervención humana (mandos y vigilante), medido en AC.

    python -m tools.acbench.f1 --stage … --state … --out output/<nueva>

Comprueba en el juego, con la telemetría de la app (no con lo enviado): arranque desde parado,
gas (lo que ve AC = lo pedido), freno (deceleración), volante en los dos sentidos (signo de la
guiñada), subir y bajar marchas (y punto muerto), embrague cerrado en marcha, y el VIGILANTE: con
el coche a ~30 km/h Python deja de latir 2,5 s; la app debe marcar ``watchdog = 1`` y el coche
decelerar sin gas; al volver el latido, ``watchdog = 0``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from src.safe_paths import ensure_safe_output_path
from tools.acbench import analysis
from tools.acbench.plan import VariantPlan
from tools.acbench.runner import AcIo, Runner
from tools.acbench.session import AcSession


def collect(r: Runner, seconds: float, gas: float, brake: float, steer: float) -> list[dict]:
    out = []
    t0 = r.io.now()
    while r.io.now() - t0 < seconds:
        tel = r.tel()
        r.drive(gas, brake, steer)
        out.append({k: tel[k] for k in ("speed_kmh", "gas", "brake", "steer", "gear", "clutch", "rpm", "watchdog")}
                   | {"yaw": tel["local_ang_vel"][1], "t": r.io.now() - t0})
        r.io.sleep(0.005)
    return out


def med(xs, k):
    return statistics.median(x[k] for x in xs) if xs else None


def run(stage: Path, state: Path, out: Path, car: str) -> dict:
    out = ensure_safe_output_path(out)
    res: dict = {"car_id": car}
    with AcSession(car, state, stage, out / "sesion") as s:
        s.wait_ready(timeout=300)
        res["menu"] = s.leave_menu()["ok"]
        r = Runner(AcIo(s), VariantPlan(car, []), analysis.BY_ID[car], out, "f1")
        r.prepare()
        res["arranque_ok"] = r.launch()
        res["subir_a_2"] = r.shift_to(2, None, gas=0.2)
        acc = collect(r, 3.0, 0.5, 0.0, 0.0)
        res["gas"] = {"pedido": 0.5, "visto_med": med(acc, "gas"), "v_ini": acc[0]["speed_kmh"],
                      "v_fin": acc[-1]["speed_kmh"], "embrague_med": med(acc, "clutch"), "marcha": med(acc, "gear")}
        brk = collect(r, 1.0, 0.0, 0.5, 0.0)
        res["freno"] = {"pedido": 0.5, "visto_med": med(brk, "brake"), "v_ini": brk[0]["speed_kmh"],
                        "v_fin": brk[-1]["speed_kmh"],
                        "decel_ms2": (brk[0]["speed_kmh"] - brk[-1]["speed_kmh"]) / 3.6 / max(brk[-1]["t"], 1e-3)}
        collect(r, 2.0, 0.35, 0.0, 0.0)
        for sgn in (1, -1):
            st = collect(r, 2.0, 0.3, 0.0, 0.15 * sgn)
            res[f"volante_{'+' if sgn > 0 else '-'}"] = {"pedido": 0.15 * sgn, "visto_grados_med": med(st, "steer"),
                                                        "yaw_med_ultimo_s": statistics.median(
                                                            x["yaw"] for x in st if x["t"] > 1.0)}
            collect(r, 1.0, 0.3, 0.0, 0.0)
        res["subir_a_3"] = r.shift_to(3, None, gas=0.4)
        res["bajar_a_2"] = r.shift_to(2, None, gas=0.2)
        res["punto_muerto"] = r.shift_to(0, None)
        res["volver_a_2"] = r.shift_to(1, None, gas=0.2) and r.shift_to(2, None, gas=0.3)
        collect(r, 3.0, 0.6, 0.0, 0.0)
        # vigilante: Python deja de latir y de escribir mandos (gas 0,6 queda escrito en Custom AI)
        r.drive(0.6, 0.0, 0.0)
        before = s.bridge.telemetry()
        t0 = time.time()
        trace = []
        while time.time() - t0 < 2.5:
            tel = s.bridge.telemetry()  # solo lectura: sin publish(), sin write_controls()
            trace.append({"t": time.time() - t0, "v": tel["speed_kmh"], "gas": tel["gas"], "brake": tel["brake"],
                          "watchdog": tel["watchdog"]})
            time.sleep(0.02)
        after_resume = collect(r, 1.0, 0.0, 0.3, 0.0)
        tripped = [x for x in trace if x["watchdog"] == 1]
        res["vigilante"] = {"v_antes": before["speed_kmh"], "v_final_sin_latido": trace[-1]["v"],
                            "disparo_s": tripped[0]["t"] if tripped else None,
                            "gas_tras_disparo_med": statistics.median(x["gas"] for x in tripped) if tripped else None,
                            "freno_tras_disparo_med": statistics.median(x["brake"] for x in tripped) if tripped else None,
                            "watchdog_tras_volver": after_resume[-1]["watchdog"]}
        r.stop_car()
        r.io.action(7)  # sondeo final
    (out / "f1_resultado.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), "utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--car", default="beam2ac_g221lsd_a1")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.stage, a.state, a.out, a.car), indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
