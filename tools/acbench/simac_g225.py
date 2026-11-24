"""G2.25: doble SINTÉTICO de AC para probar :mod:`tools.acbench.validate` (NUNCA da datos de AC).

Amplía :class:`tools.acbench.simac.SimAc` con lo que usa la batería de G2.25:

* rumbo coherente con AC: ψ = atan2(look.x, look.z) y dψ/dt = ω_y, con «volante + ⇒ ω_y < 0»
  (F0 de G2.22); el doble de G2.22 solo publicaba la guiñada;
* teletransporte a (x, z, rumbo), marcha atrás;
* rpm en punto muerto con la ley de AC (corte en LIMITER, freno de ``[COAST_REF]``);
* los dos muros de :mod:`tools.acbench.track_g225` con un coche-caja (``front``, ``rear``,
  ``half_width``): ``collided_with = 0`` al tocarlos;
* ``Geo.v1`` mínima y acciones propias (``geo_action``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tools.acbench import bridge as B
from tools.acbench import track_g225
from tools.acbench.simac import SimAc


@dataclass
class SimAcG225(SimAc):
    limiter: float = 6500.0
    minimum: float = 645.0
    coast_rpm: float = 6500.0
    coast_torque: float = 78.3
    inertia: float = 0.185
    front: float = 2.0
    rear: float = 2.2
    half_width: float = 0.95
    rpm_n: float = 645.0
    reverse_v: float = 0.0
    geo_actions: list[int] = field(default_factory=list)
    wall_hit: bool = False

    def action(self, act: int, **values) -> int:
        self.actions.append(act)
        if act == B.ACT_TELEPORT:
            self.x, self.z = values["pos"][0], values["pos"][2]
            d = values["dir"]
            self.psi = math.atan2(-d[0], -d[2])  # como AC (G2.25): el coche queda mirando a −dir
            self.v = self.reverse_v = 0.0
            return 1
        return super().action(act, **values)

    def geo_action(self, act: int, timeout: float = 30.0, **values) -> int:
        self.geo_actions.append(act)
        return 1

    def _advance(self, dt: float) -> None:
        self.t += dt
        if self.frozen():
            return
        c = self.controls
        h = dt
        # motor en punto muerto (o con el embrague abierto)
        if self.gear == 0 or c["clutch"] < 0.5:
            coast = 0.0 if self.rpm_n <= self.minimum else \
                self.coast_torque * (self.rpm_n - self.minimum) / (self.coast_rpm - self.minimum)
            drive = 400.0 * c["gas"] if self.rpm_n < self.limiter else 0.0
            self.rpm_n += (drive - coast) / self.inertia * h * 30 / math.pi
            self.rpm_n = max(self.minimum, min(self.limiter + 50, self.rpm_n))
        if self.gear == -1:
            acc = c["gas"] * 2.0 - c["brake"] * 8.0 - 0.2
            self.reverse_v = max(0.0, self.reverse_v + acc * h)
            self.v = 0.0
        else:
            if self.gear > 0 and c["clutch"] >= 0.5:
                acc = c["gas"] * 9.0 / self.gear - (1.2 if c["gas"] < 0.01 else 0.0)
            else:
                acc = -0.4 if self.v > 0 else 0.0
            acc -= c["brake"] * 8.0 + 0.1
            self.v = max(0.0, self.v + acc * h)
        speed = self.v - self.reverse_v
        delta = math.radians(c["steer"] * self.geo.steer_lock_deg / self.geo.steer_ratio)
        yaw_bicycle = speed * math.tan(delta) / self.geo.wheelbase
        w_y = -yaw_bicycle  # volante + ⇒ ω_y < 0 (F0)
        self.psi += w_y * h  # dψ/dt = ω_y
        self.x += speed * math.sin(self.psi) * h
        self.z += speed * math.cos(self.psi) * h
        self._yaw = w_y
        self._walls()
        self._acc += dt
        while self._acc >= self._frame_dt:
            self._acc -= self._frame_dt
            self.frame += 1

    def _corners(self):
        s, co = math.sin(self.psi), math.cos(self.psi)
        for fz in (self.front, -self.rear):
            for fx in (self.half_width, -self.half_width):
                # +X local = izquierda; mundo: x = x0 + fx·cos ψ + fz·sin ψ, z = z0 − fx·sin ψ + fz·cos ψ
                yield self.x + fx * co + fz * s, self.z - fx * s + fz * co

    def _walls(self) -> None:
        hit = False
        for cx, cz in self._corners():
            ax0, ax1 = track_g225.WALL_A_X
            if ax0 <= cx <= ax1 and track_g225.WALL_A_Z <= cz <= track_g225.WALL_A_Z + track_g225.WALL_T:
                hit = True
            bz0, bz1 = track_g225.WALL_B_Z
            if bz0 <= cz <= bz1 and track_g225.WALL_B_X <= cx <= track_g225.WALL_B_X + track_g225.WALL_T:
                hit = True
        if hit:
            self.v = self.reverse_v = 0.0
        self.wall_hit = hit

    def _tel(self) -> dict:
        tel = super()._tel()
        speed = self.v - self.reverse_v
        look = [math.sin(self.psi), 0.0, math.cos(self.psi)]
        tel.update(look=look, up=[0.0, 1.0, 0.0], vel=[speed * look[0], 0.0, speed * look[2]],
                   local_vel=[0.0, 0.0, speed], speed_kmh=abs(speed) * 3.6, watchdog=0,
                   collided_with=0 if self.wall_hit else -1, local_ang_vel=[0.0, getattr(self, "_yaw", 0.0), 0.0])
        if self.gear == 0 or self.controls["clutch"] < 0.5:
            tel["rpm"] = self.rpm_n
        tel["clutch"] = 0.0 if self.controls["clutch"] < 0.5 else 1.0
        tel["geo"] = {
            "act_done_seq": 0, "act_result": 1, "body_pos": [self.x, 0.0, self.z], "body_look": look,
            "body_up": [0.0, 1.0, 0.0], "phys_pos": [self.x, 0.45, self.z], "phys_look": look,
            "collision_pos": [0.0, 0.5, self.front], "collision_depth": 0.0, "graphics_offset": [0.0, -0.45, -0.3],
            "wheel_node_body": [0.8, 0.33, 1.4] * 4, "wheel_pos_body": [0.8, 0.33, 1.4] * 4, "cg_height": 0.45,
            "aabb_center": [0, 0.6, 0], "aabb_size": [1.9, 1.2, 4.2], "nodes_found": 15, "error": "",
            "last_file": "", "shots_done": 0}
        return tel
