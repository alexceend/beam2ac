"""G2.22 F0 — viabilidad: ¿controla CSP el coche desde Python y llega telemetría válida?

    python -m tools.acbench.f0 --stage <preparación> --state <estado> --out output/<nueva>

Sin intervención humana: instala (si falta), configura la sesión, lanza AC, espera la
telemetría de la app y ``Car0.v0``, y prueba en orden (cada prueba queda en ``eventos.jsonl`` y
el resumen en ``f0_resultado.json``):

1. telemetría: fotogramas, ``phys_ms`` (pasos de física entre fotogramas), menú/pausa;
2. ``ACT_PROBE``: qué funciones ``physics.*`` y ``ac.overrideCarControls`` acepta CSP aquí;
3. Custom AI: subir a 1.ª (``gear_up``), gas 0,4 durante 3 s, frenar; ¿se mueve el coche y los
   mandos que ve AC son los enviados?; si no se mueve, teletransporte a la salida y repetir;
4. signo del volante: ``steer`` +0,2 a ~20 km/h → signo de la guiñada;
5. ``SimState.v1.time_scale`` 0,25: ¿más fotogramas por paso de física (registro a ritmo de
   física)?;
6. modo Lua (``ac.overrideCarControls``) con Custom AI en reposo.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from src.safe_paths import ensure_safe_output_path
from tools.acbench import bridge as B
from tools.acbench.session import AcSession, _brief


def hold(s: AcSession, seconds: float, samples: list | None = None, every: float = 0.01) -> list[dict]:
    out = [] if samples is None else samples
    t0 = time.time()
    while time.time() - t0 < seconds:
        s.heartbeat()
        s.write_controls()
        tel = s.bridge.telemetry()
        cd = s.car_data()
        if tel:
            rec = {"t": time.time() - t0, "frame": tel["frame"], "phys_ms": tel["phys_ms"], "gear": tel["gear"],
                   "speed": tel["speed_kmh"], "gas": tel["gas"], "brake": tel["brake"], "clutch": tel["clutch"],
                   "steer": tel["steer"], "yaw": tel["local_ang_vel"][1], "rpm": tel["rpm"],
                   "override": tel["override_active"], "in_menu": tel["in_menu"], "paused": tel["paused"],
                   "T": tel["drivetrain_torque"], "fbt": tel["fbt"], "shaftv": tel["shaftv"]}
            if cd is not None:
                rec.update(cai_packet=cd.packet_id, cai_gear=cd.gear, cai_steer=cd.steer, cai_gas=cd.gas,
                           cai_speed=cd.speed_kmh, cai_collisions=cd.collision_counter,
                           cai_valid=cd.wheels_valid_surface)
            out.append(rec)
        time.sleep(every)
    return out


def pulse(s: AcSession, attr: str, seconds: float = 0.12) -> None:
    setattr(s.controls, attr, True)
    hold(s, seconds)
    setattr(s.controls, attr, False)
    hold(s, 0.05)


def phys_steps(samples: list[dict]) -> dict:
    d = [b["phys_ms"] - a["phys_ms"] for a, b in zip(samples, samples[1:]) if b["frame"] != a["frame"]]
    d = [x for x in d if x >= 0]
    if not d:
        return {"n": 0}
    return {"n": len(d), "median_ms": statistics.median(d), "zero_fraction": sum(1 for x in d if x == 0) / len(d),
            "max_ms": max(d)}


def run(stage: Path, state: Path, out: Path, car_id: str) -> dict:
    out = ensure_safe_output_path(out)
    res: dict = {"car_id": car_id}
    with AcSession(car_id, state, stage, out) as s:
        try:
            tel = s.wait_ready(timeout=300, need_cai=False)
        except TimeoutError as e:
            res["telemetria"] = f"NO: {e}"
            return res
        res["telemetria"] = _brief(tel)
        t0 = time.time()
        while s.car_data() is None and time.time() - t0 < 30:
            hold(s, 0.2)
        cd = s.car_data()
        res["car0_aparece"] = cd is not None
        res["car0_inicial"] = None if cd is None else {"packet": cd.packet_id, "gear": cd.gear, "rpm": cd.rpm}
        base = hold(s, 3.0)
        res["reposo"] = {"pasos_fisica": phys_steps(base), "fps_med": None,
                         "cai_packet_crece": (len(base) > 2 and base[-1].get("cai_packet", 0) > base[0].get("cai_packet", 0)),
                         "in_menu": sorted({x["in_menu"] for x in base}), "paused": sorted({x["paused"] for x in base}),
                         "gear": sorted({x["gear"] for x in base}), "cai_gear": sorted({x.get("cai_gear") for x in base})}
        res["salir_menu"] = s.leave_menu()
        after = hold(s, 2.0)
        res["tras_menu"] = {"in_menu": sorted({x["in_menu"] for x in after}),
                            "cai_packet_crece": (len(after) > 2 and after[-1].get("cai_packet", 0) > after[0].get("cai_packet", 0))}
        res["probe_resultado"] = s.action(B.ACT_PROBE)
        res["phys_api"] = s.bridge.telemetry()["phys_api"]
        s.bridge.cmd.log_enable = 1
        res["abrir_csv"] = s.action(B.ACT_OPEN_LOG, run_id="f0")

        def drive_test(tag: str) -> dict:
            c = s.controls
            c.brake, c.gas, c.clutch, c.steer = 0.0, 0.0, 1.0, 0.0
            hold(s, 0.5)
            g0 = s.bridge.telemetry()["gear"]
            pulse(s, "gear_up")
            gear_seq = hold(s, 1.0)
            c.gas = 0.4
            acc = hold(s, 3.0)
            c.gas, c.brake = 0.0, 1.0
            stop = hold(s, 2.5)
            return {"tag": tag, "gear_antes": g0, "gear_despues": sorted({x["gear"] for x in gear_seq}),
                    "cai_gear_despues": sorted({x.get("cai_gear") for x in gear_seq}),
                    "v_max_kmh": max((x["speed"] for x in acc), default=None),
                    "gas_visto_med": statistics.median(x["gas"] for x in acc) if acc else None,
                    "rpm_max": max((x["rpm"] for x in acc), default=None),
                    "v_final": stop[-1]["speed"] if stop else None,
                    "in_menu": sorted({x["in_menu"] for x in acc})}

        r1 = drive_test("cai_inicial")
        res["cai_prueba_1"] = r1
        if (r1["v_max_kmh"] or 0) < 3:
            res["teleport_salida"] = s.action(B.ACT_TELEPORT_START)
            hold(s, 1.0)
            res["cai_prueba_2"] = drive_test("cai_tras_teleport")
        moved = max((res.get(k, {}) or {}).get("v_max_kmh") or 0 for k in ("cai_prueba_1", "cai_prueba_2")) >= 3
        res["cai_mueve_coche"] = moved
        if moved:
            c = s.controls
            c.brake, c.gas = 0.0, 0.35
            hold(s, 3.0)
            c.gas = 0.15
            c.steer = 0.2
            st = hold(s, 2.0)
            c.steer, c.gas, c.brake = 0.0, 0.0, 1.0
            hold(s, 2.5)
            yaw = statistics.median(x["yaw"] for x in st[len(st) // 2:]) if st else None
            res["volante"] = {"steer_cmd": 0.2, "steer_visto": statistics.median(x["steer"] for x in st) if st else None,
                              "cai_steer_grados": statistics.median(x.get("cai_steer", 0) for x in st) if st else None,
                              "yaw_rate": yaw, "v": statistics.median(x["speed"] for x in st) if st else None}
        s.sim_state.time_scale = 0.25
        s.write_sim_state()
        slow = hold(s, 3.0, every=0.002)
        s.sim_state.time_scale = 1.0
        s.write_sim_state()
        normal = hold(s, 2.0, every=0.002)
        res["time_scale"] = {"0.25": phys_steps(slow), "1.0": phys_steps(normal)}
        # modo Lua con Custom AI en reposo (gas 0, freno 0)
        c = s.controls
        c.gas, c.brake = 0.0, 0.0
        s.bridge.cmd.mode = B.MODE_LUA
        s.bridge.cmd.gas, s.bridge.cmd.brake, s.bridge.cmd.clutch, s.bridge.cmd.steer = 0.4, 0.0, 1.0, 0.0
        s.bridge.cmd.gear_req = 1
        lua = hold(s, 3.0)
        s.bridge.cmd.gas, s.bridge.cmd.brake = 0.0, 1.0
        c.brake = 1.0
        hold(s, 2.5)
        s.bridge.cmd.mode = B.MODE_CAI
        res["lua_override"] = {"override_activo": sorted({x["override"] for x in lua}),
                               "v_max_kmh": max((x["speed"] for x in lua), default=None),
                               "gas_visto_med": statistics.median(x["gas"] for x in lua) if lua else None,
                               "gear": sorted({x["gear"] for x in lua})}
        res["final"] = _brief(s.bridge.telemetry())
    (out / "f0_resultado.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), "utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--car", default="beam2ac_g221lsd_a0")
    a = ap.parse_args(argv)
    res = run(a.stage, a.state, a.out, a.car)
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
