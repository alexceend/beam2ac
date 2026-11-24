"""G2.22: máquina de estados del banco LSD autónomo.

Estados (``State``) y transiciones por repetición::

    PREPARAR → [por cada repetición] RECOLOCAR? → ARRANCAR? → ESTABLECER → MEDIR → RECUPERAR
             → (siguiente repetición | siguiente maniobra) → FIN
    cualquier estado → PARADA_SEGURA → (reanudar | ABORTAR)

* **Tiempo máximo** por estado (``TIMEOUTS``); agotarlo invalida la repetición.
* **Vigilante**: si el número de fotograma de la telemetría no avanza en ``STALE_S`` →
  parada segura; si no vuelve en ``LOST_S`` → ``CommLost`` (aborta y suelta los mandos).
  La app Lua tiene su propio vigilante (sin latido de Python en 0,5 s → mandos en reposo).
* **Validez** de la ventana de medida (``Rep.reasons``): sin freno ni freno de mano, sin
  colisión (contador de Custom AI y ``collidedWith``), sin daño nuevo, las 4 ruedas sobre la
  superficie válida y cargadas (> 100 N), lecturas finitas, gas aplicado = gas pedido,
  embrague cerrado en marcha, sin trompo. Una repetición inválida se repite (tope de intentos).
* **Saturación real** en línea (``control.online_saturation``, criterio pre-registrado): solo
  decide cuándo hay bastantes repeticiones; el veredicto lo da el análisis fuera de línea.

``Io`` abstrae AC (``AcIo`` sobre :class:`tools.acbench.session.AcSession`) y el doble
sintético (:mod:`tools.acbench.simac`), que solo sirve para probar esta lógica.
"""

from __future__ import annotations

import enum
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from tools.acbench import bridge as B
from tools.acbench.control import YAW_SIGN, CarGeometry, Circle, Speed, online_saturation
from tools.acbench.plan import Item, VariantPlan


class State(enum.IntEnum):
    PREPARAR = 1
    RECOLOCAR = 2
    ARRANCAR = 3
    ESTABLECER = 4
    MEDIR = 5
    RECUPERAR = 6
    PARADA_SEGURA = 7
    FIN = 8
    ABORTAR = 9


TIMEOUTS = {State.RECOLOCAR: 25.0, State.ARRANCAR: 25.0, State.ESTABLECER: 35.0, State.RECUPERAR: 20.0}
STALE_S = 0.5
LOST_S = 15.0
PAD_RADIUS_M = 150.0  # recolocar si el coche se aleja más del centro de la explanada
MIN_LOAD_N = 100.0
MAX_CONSECUTIVE_INVALID = 5


class CommLost(RuntimeError):
    pass


class Io(Protocol):
    def now(self) -> float: ...

    def sleep(self, s: float) -> None: ...

    def read(self) -> dict | None: ...

    def apply(self, gas: float, brake: float, steer: float, clutch: float = 1.0, handbrake: float = 0.0,
              gear_up: bool = False, gear_dn: bool = False) -> None: ...

    def set_segment(self, seg_uid: int, meas: int, state: int) -> None: ...

    def action(self, act: int, **values) -> int: ...

    def safe_stop(self) -> None: ...


@dataclass
class Rep:
    seg_uid: int
    item: str
    kind: str
    direction: float
    attempt: int
    valid: bool = False
    saturated: bool = False
    reasons: list[str] = field(default_factory=list)
    t_start_ms: float = math.nan
    t_end_ms: float = math.nan
    frames: int = 0
    sat_frames: dict[str, int] = field(default_factory=dict)
    v_mean_kmh: float = math.nan
    t_mean: float = math.nan
    gas_cmd: float = 0.0


@dataclass
class Runner:
    io: Io
    plan: VariantPlan
    variant: object  # g221 Variant (límites de los diferenciales)
    out: Path
    run_id: str
    geo: CarGeometry = field(default_factory=CarGeometry)
    seg_base: int = 0
    reps: list[Rep] = field(default_factory=list)
    state: State = State.PREPARAR
    events: list[dict] = field(default_factory=list)
    _last_frame: int = -1
    _last_frame_t: float = 0.0
    _seg_counter: int = 0
    _base_collisions: int | None = None
    _base_damage: float | None = None

    # --- utilidades ---------------------------------------------------------------------

    def ev(self, kind: str, **data) -> None:
        rec = {"t": round(self.io.now(), 3), "state": self.state.name, "kind": kind, **data}
        self.events.append(rec)
        with (self.out / "eventos_runner.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def goto(self, st: State) -> None:
        if st != self.state:
            self.ev("estado", nuevo=st.name)
        self.state = st

    def tel(self) -> dict:
        """Telemetría nueva con vigilante (fotograma creciente)."""
        t0 = self.io.now()
        while True:
            tel = self.io.read()
            now = self.io.now()
            if tel is not None and tel["frame"] != self._last_frame:
                self._last_frame, self._last_frame_t = tel["frame"], now
                return tel
            if self._last_frame >= 0 and now - self._last_frame_t > STALE_S:
                self.io.safe_stop()
            if now - t0 > LOST_S:
                self.goto(State.ABORTAR)
                raise CommLost(f"telemetría parada {now - t0:.1f} s")
            self.io.sleep(0.002)

    def drive(self, gas: float, brake: float, steer: float, **kw) -> None:
        self.io.apply(gas=gas, brake=brake, steer=max(-1.0, min(1.0, steer)), **kw)

    def shift_to(self, gear: int, circle: Circle | None, gas: float = 0.0, timeout: float = 4.0) -> bool:
        """Pulsos de ``gear_up``/``gear_dn`` hasta ``ac.StateCar.gear == gear``; punto muerto (y
        cualquier marcha que no entre con pulsos) con ``physics.engageGear`` (F1)."""
        if self._engage_only:
            return self._engage(gear, circle, gas)
        # punto muerto: pulsos de gear_dn CON EL EMBRAGUE ABIERTO (sondeo F1: 1.ª → N solo así;
        # engageGear(0) en marcha no se mantiene)
        clutch = 0.0 if gear == 0 else 1.0
        t0 = self.io.now()
        while self.io.now() - t0 < timeout:
            tel = self.tel()
            if tel["gear"] == gear:
                return True
            up = tel["gear"] < gear
            steer = circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], 0.01) if circle else 0.0
            for _ in range(15):  # ~150 ms con el pulso activo
                self.drive(gas, 0.0, steer, clutch=clutch, gear_up=up, gear_dn=not up)
                self.io.sleep(0.01)
            for _ in range(25):
                self.drive(gas, 0.0, steer)
                self.io.sleep(0.01)
                if self.tel()["gear"] == gear:
                    return True
        self.ev("cambio_pulsos_fallido", objetivo=gear)
        return self._engage(gear, circle, gas)

    _engage_only = False

    def _engage(self, gear: int, circle: Circle | None, gas: float) -> bool:
        r = self.io.action(B.ACT_ENGAGE_GEAR, gear_req=gear)
        t0 = self.io.now()
        while self.io.now() - t0 < 1.5:
            tel = self.tel()
            steer = circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], 0.01) if circle else 0.0
            self.drive(gas, 0.0, steer)
            if tel["gear"] == gear:
                return True
            self.io.sleep(0.01)
        self.ev("cambio_fallido", objetivo=gear, r=r)
        return False

    # --- estados -------------------------------------------------------------------------

    def prepare(self) -> None:
        self.goto(State.PREPARAR)
        tel = self.tel()
        self.ev("inicio", car=tel.get("car_id"), gear=tel["gear"])
        self.ev("sin_colisiones", r=self.io.action(B.ACT_NO_COLLISIONS))
        self.ev("abrir_csv", r=self.io.action(B.ACT_OPEN_LOG, run_id=self.run_id))
        self._base_collisions = tel.get("cai_collisions")
        self._base_damage = sum(tel["damage"])

    def recentre(self) -> bool:
        tel = self.tel()
        dist = math.hypot(tel["pos"][0], tel["pos"][2])
        if dist < PAD_RADIUS_M:
            return True
        self.goto(State.RECOLOCAR)
        if not self.stop_car():
            return False
        r = self.io.action(B.ACT_TELEPORT, pos=(0.0, 0.0, 0.0), dir=(0.0, 0.0, 1.0), vel=(0.0, 0.0, 0.0))
        self.ev("teletransporte", r=r, dist=round(dist, 1))
        for _ in range(100):
            self.drive(0.0, 1.0, 0.0)
            self.io.sleep(0.01)
        return r == 1

    def stop_car(self, timeout: float = 20.0) -> bool:
        t0 = self.io.now()
        while self.io.now() - t0 < timeout:
            tel = self.tel()
            if tel["speed_kmh"] < 1.0:
                return True
            self.drive(0.0, 0.6, 0.0)
            self.io.sleep(0.01)
        return False

    def launch(self) -> bool:
        """Desde parado: 1.ª y gas hasta 12 km/h (embrague automático de salida de Custom AI)."""
        self.goto(State.ARRANCAR)
        if not self.shift_to(1, None):
            return False
        t0 = self.io.now()
        while self.io.now() - t0 < TIMEOUTS[State.ARRANCAR]:
            tel = self.tel()
            if tel["speed_kmh"] > 12.0:
                return True
            self.drive(0.6, 0.0, 0.0)
            self.io.sleep(0.005)
        return False

    def establish(self, it: Item, circle: Circle) -> bool:
        self.goto(State.ESTABLECER)
        sp = Speed(it.v_start_kmh)
        t0 = self.io.now()
        stable_since = None
        diag: dict = {}
        last = self.io.now()
        while self.io.now() - t0 < TIMEOUTS[State.ESTABLECER]:
            tel = self.tel()
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            v = tel["speed_kmh"]
            want_gear = 1 if (it.gear >= 2 and v < 14.0) else it.gear
            if tel["gear"] != want_gear and tel["gear"] >= 0:
                self.shift_to(want_gear, circle, gas=0.2)
                continue
            if it.kind == "S":
                steer = -0.5 * YAW_SIGN * tel["local_ang_vel"][1]
                err_r = 0.0
            else:
                steer = circle.steer(v / 3.6, tel["local_ang_vel"][1], dt)
                err_r = circle.radius_error(v / 3.6, tel["local_ang_vel"][1])
            over = v - it.v_start_kmh
            gas = sp.gas(v, dt)
            brake = min(0.4, 0.05 * (over - 3.0)) if over > 3.0 else 0.0
            self.drive(0.0 if brake else gas, brake, steer)
            ok = abs(over) < 2.0 and abs(err_r) < 0.15 and tel["gear"] == it.gear
            stable_since = (stable_since or now) if ok else None
            diag = {"v": round(v, 1), "err_r": round(err_r, 3) if math.isfinite(err_r) else None,
                    "gear": tel["gear"], "steer": round(steer, 3), "gas": round(gas, 2), "brake": round(brake, 2),
                    "yaw": round(tel["local_ang_vel"][1], 3)}
            if stable_since and now - stable_since > 1.5:
                return True
            if now - getattr(self, "_diag_t", 0.0) > 2.0:
                self._diag_t = now
                self.ev("establecer_traza", item=it.key, **diag)
            self.io.sleep(0.004)
        self.ev("no_establece", item=it.key, **diag)
        return False

    def limits(self, tel: dict) -> dict[str, float]:
        """Límite de cada diferencial con la lectura [C] (solo para el criterio en línea)."""
        v = self.variant
        T = tel["drivetrain_torque"] if tel["gear"] != 0 else 0.0
        r = abs(tel.get("gear_ratio", 0.0) * tel.get("final_ratio", 0.0)) if tel["gear"] != 0 else 0.0
        s = v.front_share / 100.0

        def lim(d, tr):
            if d is None:
                return 0.0
            return d.preload + (d.power * tr * r if tr > 0 else abs(d.coast * tr * r))

        return {"front": lim(v.front, s * T), "rear": lim(v.rear, (1 - s) * T), "centre": lim(v.centre, T),
                "axle2wd": lim(v.diff2wd, T)}

    def measure(self, it: Item, circle: Circle, rep: Rep) -> None:
        self.goto(State.MEDIR)
        if it.kind == "N":
            if not self.shift_to(0, circle):
                rep.reasons.append("no_entra_punto_muerto")
                return
        self.io.set_segment(rep.seg_uid, 1, int(State.MEDIR))
        t0 = self.io.now()
        last = t0
        sums = {"v": 0.0, "T": 0.0}
        gas = it.gas if it.kind in ("D", "S") else 0.0
        rep.gas_cmd = gas
        traction = getattr(self.variant, "traction", "AWD")
        min_dw = 0.5 if traction == "AWD" else 0.1
        while True:
            tel = self.tel()
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            if math.isnan(rep.t_start_ms):
                rep.t_start_ms = tel["phys_ms"]
            rep.t_end_ms = tel["phys_ms"]
            v = tel["speed_kmh"]
            if it.kind == "S":
                steer = -0.5 * YAW_SIGN * tel["local_ang_vel"][1]
            else:
                steer = circle.steer(v / 3.6, tel["local_ang_vel"][1], dt)
            self.drive(gas, 0.0, steer)
            rep.frames += 1
            sums["v"] += v
            sums["T"] += tel["drivetrain_torque"]
            self._check(tel, it, rep, now - t0, gas)
            lims = self.limits(tel)
            for d in it.diffs:
                if online_saturation(tel, d, lims[d], traction, min_dw):
                    rep.sat_frames[d] = rep.sat_frames.get(d, 0) + 1
            el = now - t0
            if rep.reasons:
                break
            if el > it.t_max_s:
                break
            if it.kind in ("N", "C") and v < it.v_end_kmh:
                break
            if it.kind in ("D", "S") and (v > it.v_max_kmh or tel["rpm"] > self.geo.limiter_rpm - 400):
                break
            if it.kind == "C" and tel["rpm"] < 2200:  # el embrague automático abre a bajas rpm (F1)
                break
            if it.kind != "S" and el > 1.0 and circle.radius_error(v / 3.6, tel["local_ang_vel"][1]) > 1.5:
                break  # se abre la trayectoria (subviraje fuerte): fin de la ventana, no invalida
            self.io.sleep(0.002)
        self.io.set_segment(rep.seg_uid, 0, int(State.RECUPERAR))
        if rep.frames:
            rep.v_mean_kmh = sums["v"] / rep.frames
            rep.t_mean = sums["T"] / rep.frames
        if rep.t_end_ms - rep.t_start_ms < 500.0 and not rep.reasons:
            rep.reasons.append("ventana_corta")

    def _check(self, tel: dict, it: Item, rep: Rep, el: float, gas: float) -> None:
        r = rep.reasons
        if tel["brake"] > 0.01 or tel["handbrake"] > 0.01:
            r.append("freno")
        if tel.get("cai_collisions") is not None and self._base_collisions is not None \
                and tel["cai_collisions"] != self._base_collisions:
            r.append("colision")
            self._base_collisions = tel["cai_collisions"]
        if tel["collided_with"] not in (-1,):
            r.append("colision_lua")
        dmg = sum(tel["damage"])
        if self._base_damage is not None and dmg > self._base_damage + 1e-6:
            r.append("dano")
            self._base_damage = dmg
        if tel.get("cai_valid_surface") is not None and tel["cai_valid_surface"] != 15:
            r.append("fuera_de_superficie")
        if tel["wheels_outside"] > 0:
            r.append("ruedas_fuera")
        if min(tel["load"]) < MIN_LOAD_N:
            r.append("perdida_de_contacto")
        vals = list(tel["shaftv"]) + list(tel["fbt"]) + list(tel["load"]) + [tel["drivetrain_torque"]]
        if not all(math.isfinite(x) for x in vals):
            r.append("lectura_invalida")
        if el > 0.3 and abs(tel["gas"] - gas) > 0.02:
            r.append("gas_no_aplicado")
        if it.kind in ("D", "C", "S") and tel["gear"] != 0 and tel["clutch"] < 0.99 and el > 0.3:
            r.append("embrague_abierto")
        if it.kind != "S" and abs(tel["local_ang_vel"][1]) > 3.0:
            r.append("trompo")
        if r:
            seen, out = set(), []
            for x in r:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            rep.reasons[:] = out

    def recover(self, it: Item, circle: Circle, rep: Rep) -> None:
        self.goto(State.RECUPERAR)
        if "trompo" in rep.reasons or "colision" in rep.reasons or "perdida_de_contacto" in rep.reasons:
            self.stop_car()
            return
        if it.kind == "S":
            self.stop_car()
            return
        t0 = self.io.now()
        while self.io.now() - t0 < 1.0:
            tel = self.tel()
            self.drive(0.0, 0.0, circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], 0.01))
            self.io.sleep(0.005)

    # --- bucle principal -------------------------------------------------------------------

    def repetition(self, it: Item, attempt: int) -> Rep:
        self._seg_counter += 1
        rep = Rep(self.seg_base + self._seg_counter, it.key, it.kind, it.direction, attempt)
        circle = Circle(self.geo, it.radius, it.direction if it.direction else 1.0)
        try:
            if not self.recentre():
                rep.reasons.append("no_recoloca")
                return rep
            tel = self.tel()
            if tel["speed_kmh"] < 5.0 and not self.launch():
                rep.reasons.append("no_arranca")
                return rep
            if not self.establish(it, circle):
                rep.reasons.append("no_establece")
                self.stop_car()
                return rep
            self.measure(it, circle, rep)
            self.recover(it, circle, rep)
        finally:
            self.io.set_segment(rep.seg_uid, 0, int(self.state))
        min_frames = 10
        rep.valid = not rep.reasons
        rep.saturated = rep.valid and all(rep.sat_frames.get(d, 0) >= min_frames for d in it.diffs)
        return rep

    def run(self) -> list[Rep]:
        self.out.mkdir(parents=True, exist_ok=True)
        self.prepare()
        try:
            for it in self.plan.items:
                good = bad_streak = attempts = 0
                while good < it.target and attempts < it.max_attempts:
                    attempts += 1
                    rep = self.repetition(it, attempts)
                    self.reps.append(rep)
                    self.save()
                    self.ev("repeticion", rep=asdict(rep))
                    if rep.saturated:
                        good += 1
                    bad_streak = 0 if rep.valid else bad_streak + 1
                    if bad_streak >= MAX_CONSECUTIVE_INVALID:
                        self.ev("maniobra_abandonada", item=it.key, motivo="inválidas consecutivas")
                        break
                self.ev("maniobra_fin", item=it.key, saturadas=good, intentos=attempts)
            self.goto(State.FIN)
        finally:
            self.io.safe_stop()
            self.io.action(B.ACT_CLOSE_LOG)
            self.save()
        return self.reps

    def save(self) -> None:
        data = {"schema": "beam2ac.g222.segments/1", "run_id": self.run_id, "car_id": self.plan.car_id,
                "state": self.state.name, "segments": [asdict(r) for r in self.reps]}
        (self.out / f"{self.plan.car_id}_{self.run_id}.segments.json").write_text(
            json.dumps(data, indent=1, ensure_ascii=False, default=str), "utf-8")


# --- E/S sobre AC ------------------------------------------------------------------------------


class AcIo:
    """E/S real: mandos por Custom AI, segmento y acciones por el puente de la app Lua."""

    def __init__(self, session) -> None:
        self.s = session

    def now(self) -> float:
        return time.perf_counter()

    def sleep(self, s: float) -> None:
        self.s.heartbeat()
        time.sleep(s)

    def read(self) -> dict | None:
        self.s.heartbeat()
        tel = self.s.bridge.telemetry()
        if tel is None:
            return None
        cd = self.s.car_data()
        if cd is not None:
            tel["cai_collisions"] = cd.collision_counter
            tel["cai_valid_surface"] = cd.wheels_valid_surface
            tel["cai_packet"] = cd.packet_id
        return tel

    def apply(self, gas, brake, steer, clutch=1.0, handbrake=0.0, gear_up=False, gear_dn=False) -> None:
        c = self.s.controls
        c.gas, c.brake, c.steer, c.clutch, c.handbrake = gas, brake, steer, clutch, handbrake
        c.gear_up, c.gear_dn = gear_up, gear_dn
        self.s.write_controls()

    def set_segment(self, seg_uid: int, meas: int, state: int) -> None:
        cmd = self.s.bridge.cmd
        cmd.seg_uid, cmd.seg_meas, cmd.state = seg_uid, meas, state
        self.s.bridge.publish()

    def action(self, act: int, **values) -> int:
        return self.s.action(act, **values)

    def safe_stop(self) -> None:
        self.s.safe_stop()
