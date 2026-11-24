"""G2.25: batería de validación funcional autónoma de un coche en AC (una sesión = un coche).

    python -m tools.acbench.validate --stage <prep> --state <estado> --out output/<nueva>/runs
        --car atlanta [--run-id r01] [--only static engine pass curves walls] [--reps 3]

Reutiliza el banco de G2.22 sin cambiar su comportamiento: :class:`tools.acbench.session.AcSession`
(instalación con hashes, respaldo de la configuración, Custom AI, salida inmediata del menú,
bloqueo de instancia única, parada segura y cierre de AC), el puente ``Cmd/Tel.v1``, el vigilante
de Python y el de Lua, y los auxiliares de :class:`tools.acbench.runner.Runner` (telemetría con
vigilante, cambios de marcha, arranque, parada). Añade la estructura ``Geo.v1``
(:mod:`tools.acbench.bridge_geo`) y estas pruebas (``docs/research/g225_*.md`` §3):

========  ==================================================================================
static    reposo 8 s con freno, volcado de geometría (``_geometria.json``) y 8 capturas
engine    ×reps: ralentí 4 s, gas a fondo en punto muerto hasta el corte, 1,5 s en el corte,
          gas 0 durante 8 s (caída de rpm: freno motor de ``[COAST_REF]``)
pass_hi   ×reps en la recta: aceleración a fondo con cambios hasta 110 km/h, gas 0 en marcha
          2,5 s (freno motor a rpm altas), embrague abierto 2,5 s (sin motor), frenada
          progresiva (0 → 0,8 en 1,5 s) hasta parar
pass_lo   ×reps en la recta: aceleración suave cambiando a ~2800 rpm hasta 4.ª, gas 0 en marcha
          3 s (rpm bajas), embrague abierto 3 s, frenada
curves    ×reps por sentido: círculos de R 150 m a 60 km/h (giro suave), R 60 m a 60 km/h (radio
          amplio) y R 20 m a 30 km/h (cerrada); ventana de 6 s estable
walls     ×reps: muro A de frente a 4 km/h, muro A en marcha atrás a 3 km/h, muro B de lado
          (rumbo 4°) a 6 km/h; primer contacto con ``collidedWith = 0``
final     reposo y capturas al terminar (¿cambió algo?)
========  ==================================================================================

Cada ensayo es un segmento (``seg_uid``) con ventanas de medida (``seg_meas = 1``); un ensayo
inválido (freno, colisión, rueda sin carga, lectura no finita, trompo, fuera de la explanada,
no se establece) se repite hasta ``reps`` válidos con un tope de intentos. Los criterios de
aprobado se aplican DESPUÉS, fuera de línea (:mod:`tools.acbench.validate_analysis`) con los
umbrales pre-registrados (``output/g225_2026-11-09/preflight/preregistro.json``).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ac.car_validate import ac_get_float, read_ini_ac  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402
from tools.acbench import bridge as B  # noqa: E402
from tools.acbench import bridge_geo as G  # noqa: E402
from tools.acbench import install, policy, track_g225  # noqa: E402
from tools.acbench.control import PI, YAW_SIGN, CarGeometry, Circle, Speed  # noqa: E402
from tools.acbench.plan import VariantPlan  # noqa: E402
from tools.acbench.runner import AcIo, CommLost, Runner, State  # noqa: E402

PAD_LIMIT = track_g225.SIZE / 2 - 15.0
MIN_LOAD_N = 100.0
TESTS = ("static", "engine", "pass_hi", "pass_lo", "curves", "walls", "final")
CURVES = (("G150", 150.0, 60.0), ("A60", 60.0, 60.0), ("C20", 20.0, 30.0))
# Protocolo mínimo (auditoría de r01): sin muros (no sirven como instrumento: los coches convertidos los
# atraviesan y el Kunos de referencia solo registra contacto tras ~0,9 m) ni curva cerrada; pasada rápida +
# lenta en el mismo recorrido; curvas encadenadas sin parar ni recolocar entre repeticiones.
PROTOCOLS = {
    "minimo": ("static", "engine", "pass", "curves_min", "final"),
    # G2.27 (B0): recta a caballo de la franja de bajo agarre de ``track_split`` (diferencial 2WD)
    "b0": ("static", "split", "final"),
    # G2.40: POWER del LSD 2WD. Con el reparto de b0 el bloqueo no satura (la diferencia entre ruedas
    # ~0,48·T·r queda bajo lim = PRELOAD + POWER·T·r): hace falta 1.ª a fondo (T·r mucho mayor).
    "b0p": ("static", "split_power", "final"),
    # G2.52: frenada a FONDO (pedal 1,0) para que el ABS pueda intervenir; se compara PRESENT=1 con PRESENT=0
    "abs": ("static", "pass", "final"),
    "extendido": TESTS,
}
CURVES_MIN = (("G150", 150.0, 60.0), ("A60", 60.0, 60.0))
SPLIT_ITEMS = ((1, 0.5), (2, 0.6), (2, 1.0))  # (marcha, gas) de la prueba ``split``
SPLIT_POWER_ITEMS = ((1, 1.0),)  # protocolo b0p
CURVE_REPS_MIRROR = 1  # repeticiones en el sentido contrario (comprobación de simetría)


class SessionAborted(RuntimeError):
    pass


# --- coche ------------------------------------------------------------------------------------


def _f(ini, sec: str, key: str) -> float:
    v = ac_get_float(ini, sec, key)
    if v.value is None:
        raise ValueError(f"[{sec}] {key} ilegible: {v}")
    return float(v.value)


@dataclass
class CarSpec:
    """Valores del paquete leídos como AC (``read_ini_ac``); MINIMUM y LIMITER con ``getInt``."""

    car_id: str
    steer_lock: float
    steer_ratio: float
    wheelbase: float
    limiter: int
    minimum: int
    coast_rpm: float
    coast_torque: float
    coast_nl: float
    inertia: float
    gears: list[float]
    final: float
    tyre_radius: float
    totalmass: float
    graphics_offset: tuple[float, float, float]
    wheel_inertia_sum: float = 0.0  # Σ ANGULAR_INERTIA de las 4 ruedas (adenda 4)
    gearbox_inertia: float = 1.0  # [GEARBOX] INERTIA; ausente o 0 = 1,0 (Drivetrain [C])

    @classmethod
    def from_package(cls, car_id: str, pkg: Path) -> "CarSpec":
        d = pkg / "data"
        car, eng, dt, tyr, sus = (read_ini_ac(d / n) for n in
                                  ("car.ini", "engine.ini", "drivetrain.ini", "tyres.ini", "suspensions.ini"))
        n = int(_f(dt, "GEARS", "COUNT"))
        off = car.sections["BASIC"]["GRAPHICS_OFFSET"].split(";")[0].split(",")
        return cls(car_id=car_id, steer_lock=_f(car, "CONTROLS", "STEER_LOCK"),
                   steer_ratio=abs(_f(car, "CONTROLS", "STEER_RATIO")), wheelbase=_f(sus, "BASIC", "WHEELBASE"),
                   limiter=int(_f(eng, "ENGINE_DATA", "LIMITER")), minimum=int(_f(eng, "ENGINE_DATA", "MINIMUM")),
                   coast_rpm=_f(eng, "COAST_REF", "RPM"), coast_torque=_f(eng, "COAST_REF", "TORQUE"),
                   coast_nl=_f(eng, "COAST_REF", "NON_LINEARITY"), inertia=_f(eng, "ENGINE_DATA", "INERTIA"),
                   gears=[_f(dt, "GEARS", f"GEAR_{k}") for k in range(1, n + 1)], final=_f(dt, "GEARS", "FINAL"),
                   tyre_radius=_f(tyr, "REAR", "RADIUS"), totalmass=_f(car, "BASIC", "TOTALMASS"),
                   graphics_offset=tuple(float(x) for x in off),  # type: ignore[arg-type]
                   wheel_inertia_sum=2 * _f(tyr, "FRONT", "ANGULAR_INERTIA") + 2 * _f(tyr, "REAR", "ANGULAR_INERTIA"),
                   gearbox_inertia=(ac_get_float(dt, "GEARBOX", "INERTIA").value or 1.0))

    def geometry(self) -> CarGeometry:
        return CarGeometry(steer_lock_deg=self.steer_lock, steer_ratio=self.steer_ratio,
                           wheelbase=self.wheelbase, limiter_rpm=float(self.limiter))

    def rpm_at(self, v_kmh: float, gear: int) -> float:
        if gear < 1:
            return 0.0
        w = v_kmh / 3.6 / self.tyre_radius
        return w * self.gears[gear - 1] * self.final * 30.0 / math.pi

    def gear_for(self, v_kmh: float, rpm_min: float = 2500.0, top: int | None = None) -> int:
        """Marcha más larga con rpm ≥ ``rpm_min`` a ``v_kmh`` (mínimo 1.ª)."""
        top = top or len(self.gears)
        g = 1
        for k in range(1, top + 1):
            if self.rpm_at(v_kmh, k) >= rpm_min:
                g = k
        return g

    def tau_pred_s(self) -> float:
        """Constante de tiempo de la caída de rpm en punto muerto con la ley de AC (NL = 0):
        d(rpm)/dt = −(30/π)·TORQUE·(rpm − MIN)/(I·(RPM − MIN)) ⇒ τ = I·(RPM − MIN)·π/(30·TORQUE)."""
        return self.inertia * (self.coast_rpm - self.minimum) * math.pi / (30.0 * self.coast_torque)


# --- control auxiliar ------------------------------------------------------------------------


def heading_of(look: list[float]) -> float:
    """Rumbo en el plano XZ de AC: ψ = atan2(x, z); dψ/dt = ω_y (giro hacia +X = izquierda)."""
    return math.atan2(look[0], look[2])


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class Heading:
    """Mantiene un rumbo (y, hacia delante, una línea) con un PI sobre la guiñada; el signo del
    volante es el medido en F0 (``YAW_SIGN``) y se invierte en marcha atrás."""

    psi: float
    p0: tuple[float, float] | None = None
    k_psi: float = 1.5
    k_ct: float = 0.06
    max_ct: float = 0.12
    pi: PI = field(default_factory=lambda: PI(0.25, 0.4, -1.0, 1.0))

    def steer(self, tel: dict, dt: float, reverse: bool = False) -> float:
        psi_t = self.psi
        if self.p0 is not None and not reverse:
            dx, dz = math.sin(self.psi), math.cos(self.psi)
            s = (tel["pos"][0] - self.p0[0]) * dz - (tel["pos"][2] - self.p0[1]) * dx  # >0: a la izquierda
            psi_t -= max(-self.max_ct, min(self.max_ct, self.k_ct * s))
        w_target = max(-0.25, min(0.25, self.k_psi * wrap(psi_t - heading_of(tel["look"]))))
        sign = -1.0 if reverse else 1.0
        return self.pi(sign * YAW_SIGN * (w_target - tel["local_ang_vel"][1]), dt)


# --- E/S con Geo ------------------------------------------------------------------------------


class GeoIo(AcIo):
    """E/S real de G2.22 + ``Geo.v1`` (la telemetría lleva ``tel['geo']``)."""

    def __init__(self, session) -> None:
        super().__init__(session)
        self.geo = G.GeoReader(session.registry)

    def read(self) -> dict | None:
        tel = super().read()
        if tel is not None:
            tel["geo"] = self.geo.read()
        return tel

    def geo_action(self, act: int, timeout: float = 30.0, **values) -> int:
        seq = self.s.bridge.request(act, **values)
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.s.heartbeat()
            g = self.geo.read()
            if g and g["act_done_seq"] == seq:
                return g["act_result"]
            time.sleep(0.02)
        return 0


# --- ensayos ----------------------------------------------------------------------------------

# Ventanas del motor (s). extendido = G2.25 r01/r11; mínimo = auditoría de r01 (τ ≈ 1,5 s ⇒ MIN + 300 rpm
# en ≈ 4,4 s; el ajuste usa [MIN + 1000, inicio − 300], que cabe en 6,5 s).
ENGINE_WINDOWS = {
    "extendido": (("ralenti", 4.0, 0.0), ("subida", 3.0, 1.0), ("corte", 1.5, 1.0), ("caida", 8.0, 0.0)),
    "minimo": (("ralenti", 2.5, 0.0), ("subida", 3.0, 1.0), ("corte", 0.6, 1.0), ("caida", 6.5, 0.0)),
}


@dataclass
class Trial:
    test: str
    item: str
    attempt: int
    seg_uid: int
    valid: bool = False
    outcome: str = ""  # policy.VALID / *_failure / invalid_trial
    explanation: str = ""
    reasons: list[str] = field(default_factory=list)
    windows: dict[str, list[float]] = field(default_factory=dict)  # nombre → [phys_ms inicio, fin]
    seg_windows: dict[str, int] = field(default_factory=dict)  # nombre → seg_uid de la ventana
    info: dict = field(default_factory=dict)


@dataclass
class Validator(Runner):
    spec: CarSpec | None = None
    reps: int = 3
    trials: list[Trial] = field(default_factory=list)
    policy: policy.RetryPolicy | None = None
    protocol: str = "extendido"
    aborted: str = ""
    _seg: int = 0
    _ground_hits: int = 0

    # ---- utilidades ----

    def new_seg(self) -> int:
        self._seg += 1
        return self.seg_base + self._seg

    def window(self, tr: Trial, name: str) -> int:
        uid = self.new_seg()
        tr.seg_windows[name] = uid
        self.io.set_segment(uid, 1, int(self.state))
        return uid

    def end_window(self, tr: Trial, name: str, t0_ms: float, tel: dict) -> None:
        tr.windows[name] = [t0_ms, tel["phys_ms"]]
        self.io.set_segment(tr.seg_uid, 0, int(self.state))

    def hold(self, seconds: float, gas: float = 0.0, brake: float = 1.0, steer: float = 0.0,
             clutch: float = 1.0) -> dict:
        t0 = self.io.now()
        tel = self.tel()
        while self.io.now() - t0 < seconds:
            self.drive(gas, brake, steer, clutch=clutch)
            self.io.sleep(0.005)
            tel = self.tel()
        return tel

    def check(self, tel: dict, tr: Trial, *, allow_brake: bool = False, wall_ok: bool = False,
              yaw_max: float = 1.5, contact: bool = True) -> None:
        r = tr.reasons
        if not allow_brake and (tel["brake"] > 0.01 or tel["handbrake"] > 0.01):
            r.append("freno")
        if tel["collided_with"] == 0 and not wall_ok:
            self._ground_hits += 1
            r.append("colision_carroceria_pista")
        elif tel["collided_with"] > 0:
            r.append("colision_coche")
        if contact and min(tel["load"]) < MIN_LOAD_N:
            r.append("perdida_de_contacto")
        vals = list(tel["load"]) + [tel["rpm"], tel["speed_kmh"]] + list(tel["pos"])
        if not all(math.isfinite(x) for x in vals):
            r.append("lectura_invalida")
        if abs(tel["local_ang_vel"][1]) > yaw_max:
            r.append("trompo")
        if max(abs(tel["pos"][0]), abs(tel["pos"][2])) > PAD_LIMIT:
            r.append("fuera_de_explanada")
        if tel.get("watchdog"):
            r.append("vigilante_lua")
        tr.reasons[:] = list(dict.fromkeys(r))
        if tr.reasons and "pos_motivo" not in tr.info:  # dónde estaba el coche al primer motivo
            tr.info["pos_motivo"] = [round(float(v), 3) for v in tel["pos"]]

    def place(self, x: float, z: float, psi: float, _retry: bool = True) -> bool:
        """Para el coche y lo teletransporta (origen del modelo a ras de suelo) con rumbo ψ. Si no
        queda bien (r11: tras hundirse en el suelo contra un muro), ``physics.resetCarState`` y
        un segundo intento."""
        ok = self._place(x, z, psi)
        if not ok and _retry:
            self.ev("reinicio_estado", r=self.io.action(B.ACT_RESET_STATE))
            self.hold(1.0, brake=1.0)
            ok = self._place(x, z, psi)
        return ok

    def _place(self, x: float, z: float, psi: float) -> bool:
        self.goto(State.RECOLOCAR)
        self.stop_car()
        # Medido en G2.25 (atlanta r01): physics.setCarPosition(pos, dir) deja el coche mirando a −dir
        # (look·dir = −0,999 en las 6 recolocaciones de la recta); por eso se pasa −(rumbo deseado).
        r = self.io.action(B.ACT_TELEPORT, pos=(x, 0.0, z), dir=(-math.sin(psi), 0.0, -math.cos(psi)),
                           vel=(0.0, 0.0, 0.0))
        self.hold(2.0 if self.protocol == "extendido" else 1.2, brake=1.0)
        tel = self.tel()
        err_psi = math.degrees(wrap(heading_of(tel["look"]) - psi))
        ok = (r == 1 and math.hypot(tel["pos"][0] - x, tel["pos"][2] - z) < 3.0 and tel["speed_kmh"] < 1.0
              and abs(err_psi) < 5.0)
        self.ev("recolocar", objetivo=[x, z, round(math.degrees(psi), 1)], r=r, ok=ok,
                pos=[round(p, 3) for p in tel["pos"]], error_rumbo_deg=round(err_psi, 2))
        return ok

    def _autoclutch(self, on: bool, change: bool | None = None) -> None:
        s = getattr(self.io, "s", None)
        if s is not None:
            s.controls.autoclutch_on_start = on
            if change is not None:
                s.controls.autoclutch_on_change = change
            s.write_controls()
        self.ev("embrague_automatico", salida=on, cambio=change)

    def _probe_clutch_open(self, hd: "Heading") -> float | None:
        """Valor del mando ``clutch`` de Custom AI que deja ``ac.StateCar.clutch`` < 0,05 (abierto)
        en 0,4 s; se prueba 0 y, si no, 1. ``None`` si ninguno abre."""
        for c in (0.0, 1.0):
            t0 = last = self.io.now()
            while self.io.now() - t0 < 0.4:
                tel = self.tel()
                now = self.io.now()
                self.drive(0.0, 0.0, hd.steer(tel, max(1e-3, min(0.05, now - last))), clutch=c)
                last = now
                self.io.sleep(0.004)
            if self.tel()["clutch"] < 0.05:
                self.ev("embrague_abierto_con", mando=c)
                return c
        self.ev("embrague_no_abre")
        return None

    def save(self) -> None:  # sustituye al de Runner (otro esquema)
        data = {"schema": "beam2ac.g225.trials/1", "run_id": self.run_id, "car_id": self.plan.car_id,
                "state": self.state.name, "spec": asdict(self.spec) if self.spec else None,
                "protocol": self.protocol, "aborted": self.aborted,
                "policy": asdict(self.policy) if self.policy else None,
                "ground_hits": self._ground_hits, "trials": [asdict(t) for t in self.trials]}
        (self.out / f"{self.plan.car_id}_{self.run_id}.trials.json").write_text(
            json.dumps(data, indent=1, ensure_ascii=False, default=str), "utf-8")

    def record(self, tr: Trial) -> Trial:
        tr.valid = not tr.reasons
        tr.outcome, tr.explanation = policy.classify(tr.reasons, tr.info.get("pos_motivo"), track_g225.WALLS,
                                                     PAD_LIMIT)
        self.trials.append(tr)
        self.save()
        self.ev("ensayo", test=tr.test, item=tr.item, intento=tr.attempt, valido=tr.valid, motivos=tr.reasons,
                resultado=tr.outcome, explicacion=tr.explanation, info=tr.info)
        return tr

    def repeat(self, test: str, item: str, fn, next_fn=None, reps: int | None = None) -> None:
        """Repeticiones con la política de :mod:`tools.acbench.policy`. ``next_fn`` (opcional) hace la
        repetición siguiente SIN recolocar si la anterior fue válida (curvas encadenadas)."""
        if self.policy is None:
            self.policy = policy.RetryPolicy(reps=self.reps)
        pol = self.policy
        pol.start_maneuver()
        pol.reps = reps or self.reps
        prev_valid = False
        while True:
            tr = Trial(test, item, pol.attempts + 1, self.new_seg())
            use = next_fn if (next_fn is not None and prev_valid) else fn
            try:
                use(tr)
            except CommLost:
                raise
            except Exception as e:  # el ensayo falla, la sesión sigue
                tr.reasons.append(f"excepcion: {e!r}")
            finally:
                self.io.set_segment(tr.seg_uid, 0, int(self.state))
            self.record(tr)
            prev_valid = tr.valid
            decision = pol.decide(tr.outcome, tr.reasons, recovered=bool(tr.info.get("recuperado")))
            self.ev("politica", test=test, item=item, resultado=tr.outcome, decision=decision,
                    motivo=pol.history[-1]["why"])
            if decision == "abortar":
                raise SessionAborted(pol.history[-1]["why"])
            if decision == "fin":
                break

    # ---- static / final ----

    def t_static(self, tag: str) -> None:
        tr = Trial("static" if tag == "inicio" else "final", tag, 1, self.new_seg())
        self.goto(State.MEDIR)
        tel0 = self.tel()
        if tel0["gear"] != 0:
            self.shift_to(0, None)
        uid = self.window(tr, "reposo")
        series = []
        t0 = self.io.now()
        window_s = 8.0 if self.protocol == "extendido" else 5.0  # el asentamiento usa los 2 últimos s
        while self.io.now() - t0 < window_s:
            self.drive(0.0, 1.0, 0.0)
            tel = self.tel()
            self.check(tel, tr, allow_brake=True)
            g = tel.get("geo") or {}
            series.append((tel["phys_ms"], g.get("body_pos", [math.nan] * 3)[1]))
            self.io.sleep(0.01)
        self.end_window(tr, "reposo", tel0["phys_ms"], tel)
        tr.info["seg_reposo"] = uid
        tr.info["geo_dump"] = self.io.geo_action(G.ACT_GEO_DUMP, run_id=f"{self.run_id}_{tag}")
        tr.info["capturas"] = self.io.geo_action(G.ACT_CAPTURE, timeout=60.0, run_id=f"{self.run_id}_{tag}",
                                                 run_no=G.VIEWS_FULL)
        g = self.tel().get("geo") or {}
        tr.info["geo"] = {k: g.get(k) for k in ("graphics_offset", "wheel_node_body", "wheel_pos_body", "body_pos",
                                                "body_up", "body_look", "phys_pos", "cg_height", "aabb_center",
                                                "aabb_size", "nodes_found", "error", "last_file", "shots_done")}
        if tr.info["capturas"] != 1:
            tr.info["capturas_error"] = g.get("error")
        self.record(tr)

    # ---- motor ----

    def t_engine(self, tr: Trial) -> None:
        sp = self.spec
        self.goto(State.MEDIR)
        if self.tel()["gear"] != 0 and not self.shift_to(0, None):
            tr.reasons.append("no_entra_punto_muerto")
            return
        self.hold(1.0 if self.protocol == "extendido" else 0.5)
        for name, secs, gas in ENGINE_WINDOWS[self.protocol]:
            self.window(tr, name)
            t0 = self.io.now()
            tel = self.tel()
            t0_ms = tel["phys_ms"]
            while self.io.now() - t0 < secs:
                self.drive(gas, 1.0, 0.0)
                self.io.sleep(0.004)
                tel = self.tel()
                self.check(tel, tr, allow_brake=True)
                if tel["speed_kmh"] > 1.0:
                    tr.reasons.append("se_mueve")
                if tel["gear"] != 0:
                    tr.reasons.append("no_esta_en_punto_muerto")
                if self.io.now() - t0 > 0.3 and abs(tel["gas"] - gas) > 0.02:
                    tr.reasons.append("gas_no_aplicado")
                if name == "subida" and tel["rpm"] >= sp.limiter - 300:
                    break
                if tr.reasons:
                    break
            self.end_window(tr, name, t0_ms, tel)
            tr.info[f"rpm_fin_{name}"] = round(tel["rpm"], 1)
            if tr.reasons:
                return
        self.drive(0.0, 1.0, 0.0)

    # ---- pasadas en recta ----

    def _straight(self, tr: Trial, hd: Heading, gas_fn, until, name: str, *, clutch: float = 1.0,
                  allow_brake: bool = False, brake_fn=None, t_max: float = 40.0,
                  shift_rpm: float | None = None, top_gear: int | None = None) -> dict:
        self.window(tr, name)
        t0 = self.io.now()
        last = t0
        tel = self.tel()
        t0_ms = tel["phys_ms"]
        while True:
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            el = now - t0
            top = top_gear or len(self.spec.gears)
            if shift_rpm and 1 <= tel["gear"] < top and tel["rpm"] >= shift_rpm:
                g = tel["gear"]
                self.shift_to(g + 1, None, gas=gas_fn(el, tel))
                tr.info.setdefault(f"cambios_{name}", []).append(
                    [g, g + 1, round(tel["speed_kmh"], 1), round(tel["rpm"])])
                tel = self.tel()
                continue
            brake = brake_fn(el, tel) if brake_fn else 0.0
            self.drive(gas_fn(el, tel), brake, hd.steer(tel, dt), clutch=clutch)
            self.io.sleep(0.004)
            tel = self.tel()
            self.check(tel, tr, allow_brake=allow_brake)
            if tr.reasons or until(el, tel) or el > t_max:
                if el > t_max:
                    tr.reasons.append(f"tiempo_maximo_{name}")
                break
        self.end_window(tr, name, t0_ms, tel)
        tr.info[f"fin_{name}"] = {"v": round(tel["speed_kmh"], 2), "rpm": round(tel["rpm"]), "gear": tel["gear"],
                                  "z": round(tel["pos"][2], 1)}
        return tel

    def t_pass_combined(self, tr: Trial) -> None:
        """Protocolo mínimo: pasada rápida y, sin recolocar, la lenta a continuación en la misma recta
        (la rápida para hacia z ≈ −200; la lenta necesita ~150 m)."""
        self.t_pass(tr, low=False)
        if not tr.reasons:
            self.t_pass(tr, low=True, place=False, suffix="_bajas")

    def t_pass(self, tr: Trial, low: bool, place: bool = True, suffix: str = "") -> None:
        sp = self.spec
        if place and not self.place(track_g225.LANE_X, track_g225.LANE_Z0, 0.0):
            tr.reasons.append("no_recoloca")
            return
        hd = Heading(0.0, (track_g225.LANE_X, track_g225.LANE_Z0))
        self.goto(State.ARRANCAR)
        if not self.shift_to(1, None):
            tr.reasons.append("no_entra_1a")
            return
        self.goto(State.MEDIR)
        if low:
            gas_acc, shift_at, top, v_end = 0.5, 2800.0, min(4, len(sp.gears)), None
        else:
            gas_acc, shift_at, top, v_end = 1.0, sp.limiter - 300.0, len(sp.gears), 110.0
        if low:
            # hasta 4.ª con rpm de crucero bajas: se acelera hasta la velocidad de 4.ª a ~2500 rpm
            v_target = 2500.0 / max(1e-6, sp.rpm_at(1.0, top))
        else:
            v_target = v_end
        self._straight(tr, hd, lambda el, t: gas_acc if el > 0.4 or not low else 0.4,
                       lambda el, t: t["speed_kmh"] >= v_target and t["gear"] == (top if low else t["gear"]),
                       "aceleracion" + suffix, shift_rpm=shift_at, top_gear=top, t_max=60.0)
        if tr.reasons:
            return
        tag = "bajas" if low else "altas"
        dur = 3.0 if low else 2.5
        # F1 (G2.22): el embrague automático de salida de Custom AI desliza por debajo de ~2200 rpm;
        # para medir el freno motor en marcha a rpm bajas se desactiva durante la ventana.
        self._autoclutch(False)
        try:
            self._straight(tr, hd, lambda el, t: 0.0, lambda el, t: el >= dur, f"gas0_marcha_{tag}")
        finally:
            self._autoclutch(True)
        if tr.reasons:
            return
        # G2.25 r01: con el embrague automático de Custom AI activo, clutch = 0 NO abre el embrague
        # (ac.StateCar.clutch siguió en 1 y el par del motor en −22 N·m). Se desactiva todo el automático
        # durante la ventana y se exige que el embrague esté abierto de verdad.
        self._autoclutch(False, change=False)
        try:
            c_open = self._probe_clutch_open(hd)
            tr.info[f"embrague_abierto_mando_{tag}"] = c_open
            if c_open is None:
                tr.reasons.append("embrague_no_abre")
                return
            self._straight(tr, hd, lambda el, t: 0.0, lambda el, t: el >= dur, f"embrague_abierto_{tag}",
                           clutch=c_open)
        finally:
            self._autoclutch(True, change=True)
        if tr.reasons:
            return
        pk = 1.0 if self.protocol == "abs" else 0.8
        self._straight(tr, hd, lambda el, t: 0.0, lambda el, t: t["speed_kmh"] < 3.0, "frenada" + suffix,
                       clutch=0.0, allow_brake=True, brake_fn=lambda el, t: min(pk, pk * el / (0.3 if pk == 1.0 else 1.5)), t_max=30.0)
        self.hold(1.0 if self.protocol == "extendido" else 0.5)

    # ---- agarre partido (G2.27, B0) ----

    def t_split(self, tr: Trial, gear: int, gas: float) -> None:
        """Recta con las ruedas de un lado sobre la franja de bajo agarre: gas fijo en ``gear`` (POWER) y
        después retención (COAST). Ventanas ``split_gas`` y ``split_retencion``; el análisis del
        diferencial se hace aparte con las columnas por rueda del CSV."""
        from tools.acbench import track_split

        if not self.place(track_g225.LANE_X, track_g225.LANE_Z0, 0.0):
            tr.reasons.append("no_recoloca")
            return
        hd = Heading(0.0, (track_g225.LANE_X, track_g225.LANE_Z0))
        self.goto(State.ARRANCAR)
        if not self.shift_to(1, None):
            tr.reasons.append("no_entra_1a")
            return
        self.goto(State.MEDIR)
        z_in = track_split.STRIP_Z[0] + 15.0
        self._straight(tr, hd, lambda el, t: 0.5 if t["rpm"] < 4000 else 0.15,
                       lambda el, t: t["pos"][2] >= z_in and t["gear"] == gear, "aproximacion",
                       shift_rpm=3500.0, top_gear=gear, t_max=60.0)
        if tr.reasons:
            return
        top_rpm = self.spec.limiter - 500
        self._autoclutch(False)  # sin él, el embrague de Custom AI desliza a bajas rpm (F1)
        try:
            self._straight(tr, hd, lambda el, t: gas, lambda el, t: el >= 4.0 or t["rpm"] > top_rpm,
                           "split_gas")
            if not tr.reasons:
                self._straight(tr, hd, lambda el, t: 0.0, lambda el, t: el >= 3.0 or t["rpm"] < 1800,
                               "split_retencion")
        finally:
            self._autoclutch(True)
        if tr.reasons:
            return
        self._straight(tr, hd, lambda el, t: 0.0, lambda el, t: t["speed_kmh"] < 3.0, "frenada", clutch=0.0,
                       allow_brake=True, brake_fn=lambda el, t: min(0.3, 0.3 * el / 1.5), t_max=40.0)
        self.hold(0.5)

    # ---- curvas ----

    def t_curve(self, tr: Trial, radius: float, v: float, direction: float, chained: bool = False) -> None:
        """``chained``: la repetición anterior de este círculo fue válida y el coche sigue en él; en vez de
        parar y recolocar se perturba (−10 km/h durante 2,5 s) y se vuelve a establecer (protocolo mínimo)."""
        sp = self.spec
        circle = Circle(sp.geometry(), radius, direction)
        if not chained:
            if not self.place(0.0, 0.0, 0.0):
                tr.reasons.append("no_recoloca")
                return
            if not self.shift_to(1, None) or not self.launch():
                tr.reasons.append("no_arranca")
                return
        else:
            tr.info["encadenada"] = True
            slow = Speed(max(10.0, v - 10.0))
            t0 = last = self.io.now()
            while self.io.now() - t0 < 2.5:
                now = self.io.now()
                dt = max(1e-3, min(0.05, now - last))
                last = now
                tel = self.tel()
                over = tel["speed_kmh"] - slow.target_kmh
                brake = min(0.3, 0.05 * over) if over > 1.0 else 0.0
                self.drive(0.0 if brake else slow.gas(tel["speed_kmh"], dt), brake,
                           circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], dt))
                self.io.sleep(0.004)
        self.goto(State.ESTABLECER)
        spd = Speed(v)
        timeout = 40.0 if self.protocol == "extendido" else 25.0
        t0 = last = self.io.now()
        stable = None
        top = sp.gear_for(v)
        while True:
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            tel = self.tel()
            want = min(top, sp.gear_for(tel["speed_kmh"]))
            if tel["gear"] >= 1 and tel["gear"] != want:
                self.shift_to(want, circle, gas=0.2)
                continue
            over = tel["speed_kmh"] - v
            gas = spd.gas(tel["speed_kmh"], dt)
            brake = min(0.3, 0.05 * (over - 3.0)) if over > 3.0 else 0.0
            self.drive(0.0 if brake else gas, brake, circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], dt))
            err = circle.radius_error(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1])
            ok = abs(over) < 2.0 and abs(err) < 0.08 and tel["gear"] == top
            stable = (stable or now) if ok else None
            if stable and now - stable > 1.5:
                break
            if now - t0 > timeout:
                tr.reasons.append("no_establece")
                tr.info["diag"] = {"v": round(tel["speed_kmh"], 1), "err_r": round(err, 3) if math.isfinite(err)
                                   else None, "gear": tel["gear"], "quiere": top}
                self.stop_car()
                return
            check_tr = Trial("x", "x", 0, 0)
            self.check(tel, check_tr, yaw_max=3.0)
            if any(r in ("trompo", "fuera_de_explanada", "lectura_invalida") for r in check_tr.reasons):
                tr.reasons += check_tr.reasons
                self.stop_car()
                return
            self.io.sleep(0.004)
        self.goto(State.MEDIR)
        self.window(tr, "circulo")
        tel = self.tel()
        t0_ms = tel["phys_ms"]
        t0 = last = self.io.now()
        while self.io.now() - t0 < 6.0:
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            self.drive(spd.gas(tel["speed_kmh"], dt), 0.0,
                       circle.steer(tel["speed_kmh"] / 3.6, tel["local_ang_vel"][1], dt))
            self.io.sleep(0.004)
            tel = self.tel()
            self.check(tel, tr, yaw_max=3.0)
            if tr.reasons:
                break
        self.end_window(tr, "circulo", t0_ms, tel)
        tr.info["marcha"] = top
        if self.protocol == "minimo" and not tr.reasons:
            return  # el coche sigue en el círculo para la repetición encadenada
        self.stop_car()

    # ---- muros (collider) ----

    def _creep(self, tr: Trial, hd: Heading, v: float, reverse: bool, name: str, t_max: float) -> None:
        """Avanza (o retrocede) a ``v`` km/h hasta el primer ``collidedWith = 0``; registra ese
        fotograma completo (Tel + Geo) y empuja 0,8 s más antes de frenar."""
        spd = Speed(v)
        spd.pi.hi = 0.35
        self.window(tr, name)
        tel = self.tel()
        t0_ms = tel["phys_ms"]
        t0 = last = self.io.now()
        contact = None
        while True:
            now = self.io.now()
            dt = max(1e-3, min(0.05, now - last))
            last = now
            gas = spd.gas(tel["speed_kmh"], dt)
            self.drive(gas, 0.0, hd.steer(tel, dt, reverse=reverse))
            self.io.sleep(0.004)
            tel = self.tel()
            if tel["collided_with"] == 0 and contact is None:
                g = tel.get("geo") or {}
                contact = {"phys_ms": tel["phys_ms"], "speed_kmh": tel["speed_kmh"], "pos": tel["pos"],
                           "look": tel["look"], "geo": {k: g.get(k) for k in (
                               "body_pos", "body_look", "body_up", "phys_pos", "phys_look", "collision_pos",
                               "collision_depth", "graphics_offset")}}
                t_contact = now
            if contact is not None and now - t_contact > 0.8:
                break
            # una rueda que toca la cara del muro pierde carga (r01): no invalida el ensayo de muro
            self.check(tel, tr, wall_ok=True, yaw_max=1.0, contact=False)
            if tr.reasons or now - t0 > t_max:
                if now - t0 > t_max:
                    tr.reasons.append("sin_contacto")
                break
        self.end_window(tr, name, t0_ms, tel)
        self.hold(1.5, brake=1.0)
        if contact is None and not tr.reasons:
            tr.reasons.append("sin_contacto")
        tr.info["contacto"] = contact

    def t_wall(self, tr: Trial, kind: str) -> None:
        if kind == "frontal":
            x, z, psi, v, rev = 300.0, track_g225.WALL_A_Z - 15.0, 0.0, 4.0, False
        elif kind == "trasero":
            x, z, psi, v, rev = 300.0, track_g225.WALL_A_Z - 15.0, math.pi, 3.0, True
        else:  # lateral izquierdo contra WALL_B con rumbo 4° hacia +X
            x, z, psi, v, rev = track_g225.WALL_B_X - 4.0, 40.0, math.radians(4.0), 6.0, False
        if not self.place(x, z, psi):
            tr.reasons.append("no_recoloca")
            return
        gear = -1 if rev else 1
        if not self.shift_to(gear, None):
            tr.reasons.append("no_entra_marcha")
            return
        self.goto(State.MEDIR)
        self._creep(tr, Heading(psi, None if rev else (x, z)), v, rev, f"muro_{kind}", t_max=60.0)
        tr.info["salida"] = {"x": x, "z": z, "psi_deg": round(math.degrees(psi), 3)}
        if self.tel()["gear"] != 0:
            self.shift_to(0, None)

    # ---- bucle ----

    def run_tests(self, only: tuple[str, ...] = TESTS) -> list[Trial]:
        self.out.mkdir(parents=True, exist_ok=True)
        self.goto(State.PREPARAR)
        tel = self.tel()
        self.ev("inicio", car=tel.get("car_id"), gear=tel["gear"], geo=bool(tel.get("geo")))
        self.ev("abrir_csv", r=self.io.action(B.ACT_OPEN_LOG, run_id=self.run_id))
        try:
            if "static" in only:
                self.t_static("inicio")
            if "engine" in only:
                self.repeat("engine", "N", self.t_engine)
            if "pass_hi" in only:
                self.repeat("pass_hi", "altas", lambda tr: self.t_pass(tr, low=False))
            if "pass_lo" in only:
                self.repeat("pass_lo", "bajas", lambda tr: self.t_pass(tr, low=True))
            if "pass" in only:
                self.repeat("pass", "altas+bajas", self.t_pass_combined)
            if "split" in only:
                for gear, gas in SPLIT_ITEMS:
                    self.repeat("split", f"g{gear}_gas{int(gas * 100)}",
                                lambda tr, g=gear, x=gas: self.t_split(tr, g, x))
            if "split_power" in only:
                for gear, gas in SPLIT_POWER_ITEMS:
                    self.repeat("split", f"g{gear}_gas{int(gas * 100)}",
                                lambda tr, g=gear, x=gas: self.t_split(tr, g, x))
            if "curves_min" in only:
                for key, radius, v in CURVES_MIN:
                    for d, n in ((1.0, self.reps), (-1.0, CURVE_REPS_MIRROR)):
                        self.repeat("curves", f"{key}{'+' if d > 0 else '-'}",
                                    lambda tr, r=radius, vv=v, dd=d: self.t_curve(tr, r, vv, dd),
                                    next_fn=lambda tr, r=radius, vv=v, dd=d: self.t_curve(tr, r, vv, dd, chained=True),
                                    reps=n)
            if "curves" in only:
                for key, radius, v in CURVES:
                    for d in (1.0, -1.0):
                        self.repeat("curves", f"{key}{'+' if d > 0 else '-'}",
                                    lambda tr, r=radius, vv=v, dd=d: self.t_curve(tr, r, vv, dd))
            if "walls" in only:
                for kind in ("frontal", "trasero", "lateral"):
                    self.repeat("walls", kind, lambda tr, k=kind: self.t_wall(tr, k))
            if "final" in only:
                self.place(0.0, 0.0, 0.0)
                self.t_static("final")
            self.goto(State.FIN)
        except SessionAborted as e:
            self.goto(State.ABORTAR)
            self.ev("sesion_abortada", motivo=str(e))
            self.aborted = str(e)
        finally:
            self.io.safe_stop()
            self.io.action(B.ACT_CLOSE_LOG)
            self.save()
        return self.trials


# --- sesión -----------------------------------------------------------------------------------

APP_DIR = install.AC_ROOT / "apps" / "lua" / "beam2ac_g225_bench"
STARTUP_UNTIL_MS = 12000.0  # registro de arranque hasta este tiempo de física (Prothean: NaN hasta ~7,4 s)
STARTUP_MAX_S = 20.0


class StartupRecorder:
    """G2.25 §1.3: registro de SOLO LECTURA desde la primera telemetría de la app hasta
    ``STARTUP_UNTIL_MS`` de física (Tel + Geo, un registro por fotograma de la app) en
    ``sesion/arranque.jsonl``. No manda nada al coche (Custom AI sigue con el freno a 1 que escribe
    ``AcSession``); igual en los cuatro coches. No forma parte de ningún ensayo."""

    def __init__(self, session, path: Path) -> None:
        self.s = session
        self.geo = G.GeoReader(session.registry)
        self.path = path
        self.last_frame = None
        self.n = 0
        self.last_phys = 0.0

    def __call__(self, tel: dict) -> None:
        if tel.get("frame") == self.last_frame:
            return
        self.last_frame = tel.get("frame")
        g = self.geo.read() or {}
        keys = ("frame", "phys_ms", "sim_ms", "gear", "rpm", "speed_kmh", "brake", "collided_with",
                "collision_depth", "wheels_outside", "pos", "vel", "look", "up", "local_ang_vel", "load", "shaftv",
                "in_menu", "is_live", "paused")
        gk = ("body_pos", "body_look", "body_up", "phys_pos", "phys_up", "wheel_pos", "wheel_contact",
              "wheel_pos_body", "susp_travel", "loaded_radius", "collision_pos", "collision_depth", "cg_height",
              "graphics_offset", "ground_distance", "accel")
        rec = {"t": round(time.time(), 3), **{k: tel.get(k) for k in keys},
               "geo": {k: g.get(k) for k in gk} if g else None}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=_nan_str) + "\n")
        self.n += 1
        self.last_phys = float(tel.get("phys_ms") or 0.0)

    def follow(self) -> dict:
        """Sigue registrando (latido incluido) hasta ``STARTUP_UNTIL_MS`` de física."""
        t0 = time.time()
        while self.last_phys < STARTUP_UNTIL_MS and time.time() - t0 < STARTUP_MAX_S:
            self.s.heartbeat()
            self.s.write_controls()
            tel = self.s.bridge.telemetry()
            if tel is not None:
                self(tel)
            time.sleep(0.004)
        return {"registros": self.n, "hasta_phys_ms": round(self.last_phys, 1)}


def _nan_str(v):
    return str(v)


def summarize_startup(path: Path) -> dict:
    """Resumen del arranque: primer y último fotograma no finito, recolocación de AC (salto de
    posición) y estado al terminar."""
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]

    def fin(r):
        vals = list(r.get("pos") or []) + list(r.get("load") or [])
        return bool(vals) and all(isinstance(x, (int, float)) and math.isfinite(x) for x in vals)

    bad = [r for r in rows if not fin(r)]
    out = {"registros": len(rows), "desde_phys_ms": rows[0]["phys_ms"] if rows else None,
           "hasta_phys_ms": rows[-1]["phys_ms"] if rows else None, "no_finitos": len(bad),
           "primer_no_finito_ms": bad[0]["phys_ms"] if bad else None,
           "ultimo_no_finito_ms": bad[-1]["phys_ms"] if bad else None}
    if rows:
        last = rows[-1]
        out["final"] = {"finito": fin(last), "pos": last.get("pos"), "load": last.get("load"),
                        "suma_cargas_N": round(sum(last["load"]), 1) if fin(last) else None,
                        "speed_kmh": last.get("speed_kmh"), "up": last.get("up")}
        r0 = rows[0]
        out["primero"] = {"finito": fin(r0), "load": r0.get("load"),
                          "loaded_radius": (r0.get("geo") or {}).get("loaded_radius")}
    return out


def run_car(short: str, stage: Path, state: Path, out: Path, run_id: str, only: tuple[str, ...], reps: int,
            seg_base: int = 0, car_dir: Path | None = None, protocol: str = "extendido",
            abort_rate: float = 0.5, track_id: str = track_g225.TRACK_ID) -> dict:
    """``car_dir``: coche de REFERENCIA ya instalado en AC (p. ej. un Kunos con ``data/`` legible);
    se usa su id y solo se lee su ``data/``."""
    from tools.acbench import app_g225
    from tools.acbench.session import AcSession

    if car_dir is not None:
        cid = car_dir.name
        spec = CarSpec.from_package(cid, car_dir)
    else:
        cid = app_g225.car_id(short)
        spec = CarSpec.from_package(cid, stage / "content" / "cars" / cid)
    run_dir = ensure_safe_output_path(out / cid / run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    t0 = time.time()
    status, trials, v = "ok", [], None
    try:
        kw = {}
        if car_dir is not None:  # los Kunos no tienen la piel «default1» de configure_session
            from tools.acbench.session import START_IMMEDIATELY
            skin = sorted(p.name for p in (car_dir / "skins").iterdir() if p.is_dir())[0]
            extra = {k: dict(v) for k, v in START_IMMEDIATELY.items()}
            extra["RACE"]["SKIN"] = skin
            extra["CAR_0"] = {"SKIN": skin}
            kw["race_extra"] = extra
        with AcSession(cid, state, stage, run_dir / "sesion", track_id=track_id, **kw) as s:
            startup = StartupRecorder(s, run_dir / "sesion" / "arranque.jsonl")
            s.wait_ready(timeout=300, on_tel=startup)
            menu = s.leave_menu()
            if not menu["ok"]:
                raise RuntimeError("AC sigue en el menú de salida")
            s.log("arranque_registrado", **startup.follow())
            v = Validator(io=GeoIo(s), plan=VariantPlan(cid, []), variant=None, out=run_dir, run_id=run_id,
                          geo=spec.geometry(), seg_base=seg_base, spec=spec, reps=reps, protocol=protocol,
                          policy=policy.RetryPolicy(reps=reps, abort_invalid_rate=abort_rate))
            trials = v.run_tests(only)
            if v.aborted:
                status = f"abortada: {v.aborted}"
    except Exception as e:
        status = f"error: {e!r}"
    if v is not None:  # también tras una excepción (r21: CommLost dejaba «0 ensayos» en el resumen)
        trials = v.trials
    for sub in ("registros", "capturas"):
        src = APP_DIR / sub
        if src.is_dir():
            dst = run_dir / "app" / sub
            dst.mkdir(parents=True, exist_ok=True)
            for f in sorted(src.glob(f"{cid}_{run_id}*")):
                shutil.copy2(f, dst / f.name)
    from collections import Counter
    res = {"car_id": cid, "run_id": run_id, "protocol": protocol, "status": status,
           "minutes": round((time.time() - t0) / 60, 1), "trials": len(trials),
           "valid": sum(t.valid for t in trials), "outcomes": dict(Counter(t.outcome for t in trials))}
    sp = run_dir / "sesion" / "arranque.jsonl"
    if sp.is_file():
        try:
            res["arranque"] = summarize_startup(sp)
        except Exception as e:  # el resumen nunca tumba la sesión
            res["arranque"] = {"error": repr(e)}
    (run_dir / "resumen.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--car", required=True)
    ap.add_argument("--run-id", default=time.strftime("r%Y%m%d_%H%M%S"))
    ap.add_argument("--protocol", choices=sorted(PROTOCOLS), default="minimo")
    ap.add_argument("--only", nargs="*", default=None, help="por defecto, las pruebas del protocolo")
    ap.add_argument("--abort-rate", type=float, default=0.5,
                    help="aborta la sesión si la fracción de inválidos la supera (con ≥ 6 ensayos)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--seg-base", type=int, default=0)
    ap.add_argument("--car-dir", type=Path, default=None, help="coche de referencia instalado (solo lectura)")
    ap.add_argument("--track-id", default=track_g225.TRACK_ID, help="circuito (G2.27: beam2ac_g227_split)")
    a = ap.parse_args(argv)
    only = tuple(a.only) if a.only else PROTOCOLS[a.protocol]
    r = run_car(a.car, a.stage, a.state, ensure_safe_output_path(a.out), a.run_id, only, a.reps, a.seg_base,
                a.car_dir, a.protocol, a.abort_rate, a.track_id)
    print(json.dumps(r, ensure_ascii=False), flush=True)
    return 0 if r["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
