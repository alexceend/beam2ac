"""G2.22: doble SINTÉTICO de AC para probar la máquina de estados (NUNCA da datos de AC).

Coche cinemático (bicicleta) con reloj virtual: velocidad por gas/freno/retención, guiñada por
el volante con el signo medido en F0 (``control.YAW_SIGN``), marchas por pulsos, velocidades de
rueda geométricas más un deslizamiento artificial para que el contador de saturación tenga algo
que contar. Permite inyectar fallos: colisión (``collide_at``), telemetría congelada
(``freeze_at``), pérdida de contacto (``airborne_at``).

Su único uso son las pruebas (``tests/test_g222_bench.py``). Cualquier CSV que derive de aquí
lleva ``SINTETICO`` en el nombre y el análisis lo marca ``source = sintetico``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tools.acbench import bridge as B
from tools.acbench.control import YAW_SIGN, CarGeometry

GEARS = {-1: -3.3, 0: 0.0, 1: 3.14, 2: 1.87, 3: 1.3, 4: 0.99, 5: 0.8, 6: 0.66}
FINAL = 5.98
R_TYRE = 0.3
TRACK = 1.45


@dataclass
class SimAc:
    geo: CarGeometry = field(default_factory=CarGeometry)
    t: float = 0.0
    frame: int = 0
    x: float = 0.0
    z: float = 0.0
    psi: float = 0.0
    v: float = 0.0
    gear: int = 0
    controls: dict = field(default_factory=lambda: {"gas": 0.0, "brake": 1.0, "steer": 0.0, "clutch": 1.0,
                                                   "handbrake": 0.0, "gear_up": False, "gear_dn": False})
    seg: tuple[int, int, int] = (0, 0, 0)
    collisions: int = 0
    collide_at: float | None = None
    freeze_at: float | None = None
    airborne_at: tuple[float, float] | None = None
    actions: list[int] = field(default_factory=list)
    safe_stops: int = 0
    _prev_up: bool = False
    _prev_dn: bool = False
    _frame_dt: float = 1 / 150.0
    _acc: float = 0.0

    # --- Io ---------------------------------------------------------------------------------

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self._advance(max(s, 1e-4))

    def read(self) -> dict | None:
        self._advance(1e-4)
        return self._tel()

    def apply(self, gas, brake, steer, clutch=1.0, handbrake=0.0, gear_up=False, gear_dn=False) -> None:
        c = self.controls
        c.update(gas=gas, brake=brake, steer=steer, clutch=clutch, handbrake=handbrake)
        if gear_up and not self._prev_up:
            self.gear = min(6, self.gear + 1)
        if gear_dn and not self._prev_dn:
            self.gear = max(-1, self.gear - 1)
        self._prev_up, self._prev_dn = gear_up, gear_dn

    def set_segment(self, seg_uid: int, meas: int, state: int) -> None:
        self.seg = (seg_uid, meas, state)

    def action(self, act: int, **values) -> int:
        self.actions.append(act)
        if act == B.ACT_ENGAGE_GEAR:
            self.gear = values.get("gear_req", self.gear)
        if act == B.ACT_TELEPORT:
            self.x = self.z = self.v = self.psi = 0.0
        return 1

    def safe_stop(self) -> None:
        self.safe_stops += 1
        self.controls.update(gas=0.0, brake=1.0, steer=0.0)

    # --- física sintética ------------------------------------------------------------------

    def frozen(self) -> bool:
        return self.freeze_at is not None and self.t >= self.freeze_at

    def _advance(self, dt: float) -> None:
        self.t += dt
        if self.frozen():
            return
        c = self.controls
        h = dt
        if self.gear > 0:
            acc = c["gas"] * 9.0 / self.gear - (1.2 if c["gas"] < 0.01 else 0.0)
        else:
            acc = -0.8 if self.v > 0 else 0.0
        acc -= c["brake"] * 8.0 + 0.2
        self.v = max(0.0, self.v + acc * h)
        delta = math.radians(c["steer"] * self.geo.steer_lock_deg / self.geo.steer_ratio)
        yaw = self.v * math.tan(delta) / self.geo.wheelbase
        self.psi += yaw * h
        self.x += self.v * math.sin(self.psi) * h
        self.z += self.v * math.cos(self.psi) * h
        self._yaw = YAW_SIGN * yaw
        if self.collide_at is not None and self.t >= self.collide_at:
            self.collisions += 1
            self.collide_at = None
        self._acc += dt
        while self._acc >= self._frame_dt:
            self._acc -= self._frame_dt
            self.frame += 1

    def _tel(self) -> dict:
        c = self.controls
        yaw = getattr(self, "_yaw", 0.0)
        w = self.v / R_TYRE
        dw_kin = abs(yaw) * TRACK / R_TYRE
        slip = 0.6 + 1.5 * c["gas"]
        shaftv = [w + dw_kin / 2 + slip, w - dw_kin / 2, w + dw_kin / 2 + slip, w - dw_kin / 2 - 0.6]
        ratio = GEARS[self.gear]
        rpm = max(1400.0, abs(w) * ratio * FINAL * 60 / (2 * math.pi)) if self.gear else 1400.0
        load = [3500.0] * 4
        if self.airborne_at and self.airborne_at[0] <= self.t <= self.airborne_at[1]:
            load[1] = 0.0
        return {"frame": self.frame, "phys_ms": self.t * 1000.0, "sim_ms": self.t * 1000.0, "car_id": "SINTETICO",
                "gear": self.gear, "speed_kmh": self.v * 3.6, "rpm": rpm, "gas": c["gas"], "brake": c["brake"],
                "clutch": 1.0, "steer": c["steer"] * self.geo.steer_lock_deg, "handbrake": c["handbrake"],
                "drivetrain_torque": 150.0 * c["gas"] - (20.0 if self.gear else 0.0),
                "gear_ratio": ratio, "final_ratio": FINAL, "local_ang_vel": [0.0, yaw, 0.0],
                "pos": [self.x, 0.0, self.z], "shaftv": shaftv, "ang": shaftv, "load": load,
                "fbt": [0.0] * 4, "damage": [0.0] * 5, "collided_with": -1, "wheels_outside": 0,
                "cai_collisions": self.collisions, "cai_valid_surface": 15}
