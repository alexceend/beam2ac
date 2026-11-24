"""Laboratorio G2.21: port de ``transmisión 4WD de AC`` de AC (TYPE=AWD), AISLADO.

Nada de producción importa este módulo (igual que el resto de ``src/lab``). Sirve para:

* fijar por escrito la lectura del análisis de AC
  (``output/g211_transmissions_2026-11-02/acs_drivetrain/c``);
* generar datos SINTÉTICOS con el mismo formato que registrará el banco de AC
  (``output/g221_2026-11-07/banco_ac/``) para comprobar que el método de análisis
  recupera
  el par de bloqueo. Es una comprobación del MÉTODO, no de AC: el port reproduce la
  lectura del código, y si la lectura fuera errónea el port lo sería también.

Notación (orden de coche de AC): 0 = FL (``outShaftLF``), 1 = FR (``outShaftRF``),
2 = RL (``outShaftL``), 3 = RR (``outShaftR``). ``fb[i]`` =
``tyres[i].status.feedbackTorque``
(par de reacción del neumático en la rueda, N·m). ``T`` = ``acEngine.status.outTorque``.
``r`` = ``|FINAL·marcha|``; ``s`` = ``[AWD] FRONT_SHARE·0,01``.

Lectura del código (embrague cerrado, ``v19 = 0``):

1. ``v18 = (ΣI_semiejes + I_drive)/r² + I_embrague + I_motor``; ``Δraíz = dt·T/v18``;
   el bloque de aceleración de la transmisión de AC: ``drive += a``; delanteros ``+2·s·a``;
   traseros ``+2·(1−s)·a``.
2. Reacción: ``v33 = dt·Σfb/v20`` (``v20 = la inercia desde las ruedas``) a la raíz (×r), a
   ``drive`` y a los 4 semiejes por igual.
3. Trasero: ``lim_R = PRELOAD + (POWER si (1−s)·T > 0 si no COAST)·|(1−s)·T|·r``;
   ``L += −dt·sat(ω_L−ω_R)·lim_R/I_L·0,5`` y ``R`` lo opuesto; después
   ``L −= dt·(fb_RR−fb_RL)/I_R·0,5``, ``R`` lo opuesto. ``sat(x) = x/(|x|+0,1)``
   (la constante es el float ``0.1000000014901161``).
4. Delantero: igual con ``s·T`` e ``I_LF``/``I_RF``.
5. Central: ``lim_C = PRELOAD + (POWER|COAST)·|T|·r``; ``v58 = 1/(I_R + I_L + I_LF +
I_R)``
   (el código suma ``I_R`` dos veces); ``Δ = (ω_L+ω_R)/2 − (ω_LF+ω_RF)/2``;
   ``v60 = −dt·sat(Δ)·lim_C·v58``: traseros ``+v60``, delanteros ``−v60``; después
   ``v64 = dt·((fb_RL+fb_RR) − (fb_FL+fb_FR))·v58``: traseros ``+v64``, delanteros
   ``−v64``.

Consecuencias que el banco debe medir (solo lectura del código, [C]):

* par que el diferencial de eje pasa entre las dos ruedas: ``±lim/2`` ⇒ diferencia
  ``lim·sat(Δω)``;
* central: ``∓lim_C·sat/4`` por rueda (inercias iguales) ⇒ diferencia entre la suma
  trasera y la delantera ``lim_C·sat(Δ)``;
* con las inercias iguales, en cada rueda ``I·Δω_i/dt = (motor) + fb_i +
(bloqueos)``: la
  reacción de cada neumático solo actúa sobre su rueda (diferenciales abiertos).

De ahí los estimadores del banco (``transfer_axle``, ``transfer_centre``): diferencia de
reacciones menos la diferencia de aceleraciones; no dependen del par del motor (en el
central solo si ``s`` = 0,5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SAT_WINDOW = 0.1000000014901161  # float 0.1f de el cálculo 4WD de AC


def sat(x: float) -> float:
    """Factor de la ventana de deslizamiento de AC: ``x/(|x| + 0,1)``."""
    return x / (abs(x) + SAT_WINDOW)


@dataclass(frozen=True)
class AcDiff:
    """Un diferencial de ``[AWD]``: POWER, COAST (adimensionales), PRELOAD (N·m,
    rueda)."""

    power: float = 0.0
    coast: float = 0.0
    preload: float = 0.0

    def limit(self, torque_ref: float, ratio: float) -> float:
        """``PRELOAD + (POWER si T_ref > 0 si no COAST)·|T_ref|·|r|`` (el cálculo 4WD de AC)."""
        r = abs(ratio)
        if torque_ref > 0.0:
            return self.preload + self.power * torque_ref * r
        return self.preload + abs(self.coast * torque_ref * r)


@dataclass(frozen=True)
class AwdSetup:
    front: AcDiff = AcDiff()
    rear: AcDiff = AcDiff()
    centre: AcDiff = AcDiff()
    front_share: float = 0.5  # FRONT_SHARE·0,01


@dataclass
class AwdState:
    """Velocidades (rad/s) de los semiejes en el orden de coche de AC y del árbol."""

    shafts: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    drive: float = 0.0
    root: float = 0.0


@dataclass(frozen=True)
class AwdInertia:
    shaft: float = 1.35  # igualada por loadINI con TYPE=AWD
    drive: float = 0.05
    engine: float = 0.12
    clutch: float = 0.02


@dataclass(frozen=True)
class StepTerms:
    """Pares equivalentes (``I·Δω/dt``) que aplicó cada término en el paso."""

    lim_front: float
    lim_rear: float
    lim_centre: float
    axle_front: tuple[float, float]  # (FL, FR) del bloqueo delantero
    axle_rear: tuple[float, float]  # (RL, RR) del bloqueo trasero
    centre: tuple[float, float, float, float]  # bloqueo central por rueda


def step4wd(
    st: AwdState,
    setup: AwdSetup,
    inertia: AwdInertia,
    out_torque: float,
    fb: tuple[float, float, float, float],
    ratio: float,
    dt: float,
    lock_scale: float = 1.0,
) -> StepTerms:
    """Un paso del bloque AWD de el cálculo 4WD de AC con el embrague cerrado.

    ``lock_scale`` (1 = lectura del código) solo existe para generar datos sintéticos de
    una hipótesis ALTERNATIVA (p. ej. 2 = cada rueda recibe ±lim en vez de ±lim/2) y
    comprobar que el análisis la distingue. No es un parámetro de AC.
    """
    sh = st.shafts
    i_s = inertia.shaft
    s = setup.front_share
    sum_shafts = 4.0 * i_s
    if ratio != 0.0:
        v18 = (
            (sum_shafts + inertia.drive) / (ratio * ratio)
            + inertia.clutch
            + inertia.engine
        )
        v20 = (
            ratio * ratio * (inertia.clutch + inertia.engine)
            + inertia.drive
            + sum_shafts
        )
        v25 = dt * (out_torque / v18)
        st.root += v25
        acc = v25 / ratio
        st.drive += acc
        sh[0] += s * acc * 2.0
        sh[1] += s * acc * 2.0
        sh[2] += (1.0 - s) * acc * 2.0
        sh[3] += (1.0 - s) * acc * 2.0
        v33 = dt * (sum(fb) / v20)
        st.root += v33 * ratio
    else:
        v20 = inertia.drive + sum_shafts
        v33 = dt * (sum(fb) / v20)
    st.drive += v33
    for k in range(4):
        sh[k] += v33

    # trasero (L = 2, R = 3)
    lim_r = setup.rear.limit((1.0 - s) * out_torque, ratio)
    v44 = -dt * sat(sh[2] - sh[3]) * lim_r * lock_scale / i_s * 0.5
    sh[2] += v44
    sh[3] -= v44
    v47 = dt * ((fb[3] - fb[2]) / i_s * 0.5)
    sh[2] -= v47
    sh[3] += v47
    # delantero (LF = 0, RF = 1)
    lim_f = setup.front.limit(s * out_torque, ratio)
    v50 = -dt * sat(sh[0] - sh[1]) * lim_f * lock_scale / i_s * 0.5
    sh[1] -= v50
    sh[0] += v50
    v53 = dt * ((fb[1] - fb[0]) / i_s * 0.5)
    sh[0] -= v53
    sh[1] += v53
    # central
    lim_c = setup.centre.limit(out_torque, ratio)
    v58 = 1.0 / (i_s + i_s + i_s + i_s)  # R + L + LF + R (iguales)
    d_c = (sh[2] + sh[3]) * 0.5 - (sh[0] + sh[1]) * 0.5
    v60 = -dt * sat(d_c) * lim_c * lock_scale * v58
    sh[0] -= v60
    sh[1] -= v60
    sh[2] += v60
    sh[3] += v60
    v64 = dt * ((fb[3] + fb[2]) - (fb[1] + fb[0])) * v58
    sh[2] += v64
    sh[3] += v64
    sh[0] -= v64
    sh[1] -= v64
    return StepTerms(
        lim_front=lim_f,
        lim_rear=lim_r,
        lim_centre=lim_c,
        axle_front=(i_s * v50 / dt, -i_s * v50 / dt),
        axle_rear=(i_s * v44 / dt, -i_s * v44 / dt),
        centre=(-i_s * v60 / dt, -i_s * v60 / dt, i_s * v60 / dt, i_s * v60 / dt),
    )


SAT_WINDOW_2WD = 0.009999999776482582  # float 0.01f de el cálculo 2WD de AC


def step2wd_axle(
    shafts: list[float],
    drive: list[float],
    diff: AcDiff,
    shaft_inertia: float,
    axle_torque: float,
    out_torque: float,
    ratio: float,
    fb: tuple[float, float],
    dt: float,
    lock_scale: float = 1.0,
) -> bool:
    """Modelo MÍNIMO del diferencial de ``transmisión 2WD de AC`` (l.
    280-326 de
    el análisis) para datos sintéticos; el bloque motor/árbol se reduce a un
    sólido
    ``drive`` con las dos ruedas (sin inercia del motor). Lectura [C]:

    * ``lim = PRELOAD + (POWER|COAST)·|locClutch·T|·r``;
    * si ``|ω_L − ω_drive| ≥ 0,1`` o ``|fb_R − fb_L| > lim``: desliza, ``±lim/2`` con
      ``sat`` de ventana 0,01 y cada rueda con su reacción;
    * si no: las dos ruedas a la velocidad del árbol (bloqueo ESTÁTICO; no existe en
      el cálculo 4WD de AC).

    Devuelve True si deslizó."""
    i_s = shaft_inertia
    acc = dt * (axle_torque + fb[0] + fb[1]) / (2.0 * i_s)
    drive[0] += acc
    shafts[0] += acc
    shafts[1] += acc
    lim = diff.limit(out_torque, ratio)
    if abs(shafts[0] - drive[0]) >= 0.1000000014901161 or abs(fb[1] - fb[0]) > lim:
        d = shafts[0] - shafts[1]
        v49 = -dt * (d / (abs(d) + SAT_WINDOW_2WD)) * lim * lock_scale / i_s * 0.5
        shafts[0] += v49
        shafts[1] -= v49
        v52 = dt * (fb[1] - fb[0]) / i_s * 0.5
        shafts[0] -= v52
        shafts[1] += v52
        return True
    shafts[0] = drive[0]
    shafts[1] = drive[0]
    return False


# --- estimadores del banco (los mismos que usa banco_ac/analizar_ac.py) ---------------


def transfer_axle(
    fb_l: float, fb_r: float, d_rel_acc: float, shaft_inertia: float
) -> float:
    """Par que el bloqueo de un eje pasa entre sus ruedas, desde la telemetría.

    ``I·d(ω_L−ω_R)/dt = (fb_L − fb_R) − lim·sat(ω_L−ω_R)`` (el par del motor y el
    central
    son iguales en las dos ruedas del eje) ⇒ ``X = (fb_L − fb_R) − I·d(ω_L−ω_R)/dt``.
    Con la lectura del código, ``X = lim·sat(ω_L − ω_R)``.
    """
    return (fb_l - fb_r) - shaft_inertia * d_rel_acc


def transfer_centre(
    fb: tuple[float, float, float, float], d_rel_acc: float, shaft_inertia: float
) -> float:
    """Par del bloqueo central entre ejes (suma trasera − suma delantera), con ``s``
    = 0,5.

    ``I·d(S_tras − S_del)/dt = (fb_tras − fb_del) − lim_C·sat(ω̄_tras − ω̄_del)``
    (con ``s`` = 0,5 el bloque del motor da lo mismo a los dos ejes; en punto muerto
    no da
    nada) ⇒ ``X_C = (fb_RL+fb_RR − fb_FL−fb_FR) − I·d(S_tras−S_del)/dt``.
    """
    return (fb[2] + fb[3] - fb[0] - fb[1]) - shaft_inertia * d_rel_acc


# --- escenario sintético: curva de radio constante, coche guiado ----------------------


@dataclass(frozen=True)
class Corner:
    """Coche en una curva de radio constante a velocidad constante (impuesta): cada
    rueda
    tiene su velocidad de suelo; el neumático da ``Fx = clamp(C·κ, ±μ·N)``."""

    speed: float = 12.0  # m/s del centro del coche
    radius: float = 25.0  # m
    track: float = 1.5  # m (las dos vías)
    wheelbase: float = 2.5  # m
    tyre_radius: float = 0.31  # m
    stiffness: float = 60000.0  # N por unidad de κ
    load: float = 3500.0  # N por rueda
    mu: float = 1.1

    def ground_speeds(self) -> tuple[float, float, float, float]:
        """Velocidad del suelo en cada rueda (giro a la izquierda: L = interior)."""
        w = self.speed / self.radius
        r_rear = self.radius  # centro de giro en la prolongación del eje trasero
        h = self.track * 0.5
        return (
            w * math.hypot(r_rear - h, self.wheelbase),
            w * math.hypot(r_rear + h, self.wheelbase),
            w * (r_rear - h),
            w * (r_rear + h),
        )

    def feedback(self, omega: float, v: float) -> float:
        kappa = (omega * self.tyre_radius - v) / max(abs(v), 1.0)
        fx = max(-self.mu * self.load, min(self.mu * self.load, self.stiffness * kappa))
        return -fx * self.tyre_radius


def simulate_corner(
    setup: AwdSetup,
    corner: Corner,
    out_torque: float,
    ratio: float,
    seconds: float = 6.0,
    dt: float = 1.0 / 333.0,
    sample_every: int = 5,
    lock_scale: float = 1.0,
    inertia: AwdInertia = AwdInertia(),
) -> list[dict[str, float]]:
    """Filas con el formato del registrador del banco (``t``, ``w0..w3``, ``fb0..fb3``,
    ``I``, ``T``, ``ratio``) tomadas cada ``sample_every`` pasos (frecuencia gráfica).
    """
    v = corner.ground_speeds()
    st = AwdState(shafts=[vi / corner.tyre_radius for vi in v])
    rows: list[dict[str, float]] = []
    n = int(seconds / dt)
    for k in range(n):
        fb = tuple(corner.feedback(st.shafts[i], v[i]) for i in range(4))
        step4wd(st, setup, inertia, out_torque, fb, ratio, dt, lock_scale)
        if k % sample_every == 0:
            row = {
                "t": (k + 1) * dt,
                "I": inertia.shaft,
                "T": out_torque,
                "ratio": ratio,
            }
            fb_after = tuple(corner.feedback(st.shafts[i], v[i]) for i in range(4))
            for i in range(4):
                row[f"w{i}"] = st.shafts[i]
                row[f"fb{i}"] = fb_after[i]
            rows.append(row)
    return rows
