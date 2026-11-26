"""G2.22: sesión de AC para el banco (lanzar, esperar telemetría, mandar, parar con seguridad).

Una sesión = un coche. ``AcSession`` junta:

* :class:`tools.acbench.bridge.Bridge` (app Lua: telemetría con ``feedbackTorque``, CSV,
  acciones de ``physics.*`` y vigilante propio);
* Custom AI: ``CarControls0.v0`` (lo crea Python ANTES de lanzar AC, con el freno a 1),
  ``Car0.v0`` (lo crea CSP al aceptar el control) y ``SimState.v1``.

Parada segura (``safe_stop``): gas 0, freno 1, volante 0, fuera de la ventana de medida, en
los dos canales (Custom AI y puente). Se llama siempre al salir (``__exit__``), también con
excepciones; el vigilante de la app Lua cubre el caso de que Python muera (0,5 s sin latido).
"""

from __future__ import annotations

import ctypes
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from tools.acbench import bridge as B
from tools.acbench import cai, install, shm

START_IMMEDIATELY = {sec: {"__START_IMMEDIATELY": "1", "FORCE_START": "1"} for sec in ("HEADER", "RACE", "OPTIONS")}


@dataclass
class EventLog:
    path: Path

    def __call__(self, kind: str, **data) -> None:
        rec = {"t": round(time.time(), 3), "kind": kind, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


@dataclass
class AcSession:
    car_id: str
    state_dir: Path
    stage_dir: Path
    out_dir: Path
    registry: shm.Registry = field(default_factory=shm.WinRegistry)
    custom_ai: bool = True
    launch: bool = True  # False: AC ya lanzado por el usuario (o doble sintético)
    # claves de CSP (módulo de interfaz de CSP) que evitan el menú de salida de AC; F0 intento
    # 5: con ellas in_menu = 0 y Car0.v0 se actualiza sin pulsar «Drive»
    race_extra: dict | None = field(default_factory=lambda: dict(START_IMMEDIATELY))
    proc: object = None
    track_id: str | None = None  # None = circuito de G2.22 (``track.TRACK_ID``)

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log = EventLog(self.out_dir / "eventos.jsonl")
        self.controls = cai.initial_controls()
        self.controls.autoclutch_on_start = True
        self.controls.autoclutch_on_change = True
        self.controls.autoblip_active = True
        self._ctl_region = None
        self._car_region = None
        self.sim_state = cai.SimStateV1()
        self.sim_state.time_scale = 1.0
        self._sim_region = None
        self.bridge: B.Bridge | None = None

    # --- ciclo de vida ------------------------------------------------------------------

    def _lock(self) -> None:
        """Una sola sesión a la vez (F4: dos controladores llegaron a mandar el mismo AC). Bloqueo
        exclusivo de Windows sobre ``<estado>/sesion.lock``: el sistema lo suelta si el proceso muere."""
        import msvcrt
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock_f = open(self.state_dir / "sesion.lock", "a+b")
        try:
            msvcrt.locking(self._lock_f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._lock_f.close()
            raise RuntimeError("otra sesión del banco está activa (sesion.lock)") from None

    def _unlock(self) -> None:
        import msvcrt
        f = getattr(self, "_lock_f", None)
        if f is not None:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                f.close()
                self._lock_f = None

    def __enter__(self) -> "AcSession":
        if self.launch:
            self._lock()
        if self.launch:
            install.backup_configs(self.state_dir)
            install.install(self.stage_dir, self.state_dir)
            kw = {"track_id": self.track_id} if self.track_id else {}
            install.configure_session(self.state_dir, self.car_id, custom_ai=self.custom_ai,
                                      race_extra=self.race_extra, **kw)
            self.log("configurado", car_id=self.car_id, custom_ai=self.custom_ai)
        if self.custom_ai:
            self._ctl_region = self.registry.create(cai.controls_name(0), ctypes.sizeof(cai.CarControls),
                                                    cai.to_bytes(self.controls))
            self._ctl_region.write(cai.to_bytes(self.controls))
            # SimState.v1 NO se crea: en F0 (intento 5) escribir time_scale = 0,25 congeló la física
            # y no se recuperó con 1,0. Las colisiones se desactivan con physics.disableCarCollisions.
        self.bridge = B.Bridge(self.registry)
        self.bridge.cmd.mode = B.MODE_CAI if self.custom_ai else B.MODE_NONE
        self.bridge.publish()
        if self.launch:
            self.proc = install.launch_ac()
            self.log("ac_lanzado", pid=getattr(self.proc, "pid", None))
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.safe_stop()
            if self.bridge is not None:
                self.bridge.request(B.ACT_CLOSE_LOG)
                time.sleep(1.0)
        finally:
            if self.launch:
                install.close_ac(self.proc)  # type: ignore[arg-type]
                install.copy_ac_logs(self.out_dir / "logs_ac")
                self.log("ac_cerrado", excepcion=repr(exc[1]) if exc[1] else None)
                self._unlock()

    # --- espera -------------------------------------------------------------------------

    def wait_ready(self, timeout: float = 240.0, need_cai: bool | None = None, on_tel=None) -> dict:
        """Espera a que la app publique telemetría con fotogramas crecientes y (si se usa
        Custom AI) a que CSP cree ``Car0.v0``. Devuelve la primera telemetría válida.
        ``on_tel`` (G2.25, opcional): se llama con cada telemetría leída (solo registro)."""
        need_cai = self.custom_ai if need_cai is None else need_cai
        t0 = time.time()
        first_frame = None
        tel = None
        last_check = 0.0
        while time.time() - t0 < timeout:
            self.heartbeat()
            if self.launch and time.time() - last_check > 1.0:
                last_check = time.time()
                if install.launcher_running():  # G2.25: Steam relanzó AC con el lanzador oficial
                    closed = install.close_launcher()
                    self.log("lanzador_oficial_cerrado", pids=closed)
                    raise RuntimeError("Steam abrió el lanzador oficial en vez de acs.exe")
            tel = self.bridge.telemetry()
            if tel is not None:
                if on_tel is not None:
                    on_tel(tel)
                if first_frame is None:
                    first_frame = tel["frame"]
                    self.log("telemetria_aparece", s=round(time.time() - t0, 1), tel=_brief(tel))
                cai_ok = (not need_cai) or self.car_data() is not None
                if tel["frame"] > first_frame + 30 and cai_ok:
                    self.log("listo", s=round(time.time() - t0, 1), cai=self.car_data() is not None)
                    return tel
            time.sleep(0.05)
        raise TimeoutError(f"sin telemetría lista en {timeout} s (tel={tel is not None}, "
                           f"car0={self.car_data() is not None})")

    def leave_menu(self, timeout: float = 20.0) -> dict:
        """Sale del menú de salida de AC sin ratón: pulsa (simulado por CSP) «Start race» y,
        si no basta, «Start/setup». Devuelve qué funcionó."""
        tried = []
        for act in (B.ACT_START_SESSION, B.ACT_START_STOP):
            for _ in range(3):
                r = self.action(act)
                tried.append((act, r))
                t0 = time.time()
                while time.time() - t0 < timeout / 6:
                    self.heartbeat()
                    self.write_controls()
                    tel = self.bridge.telemetry()
                    if tel and tel["in_menu"] == 0:
                        self.log("menu_cerrado", accion=act, intentos=tried)
                        return {"ok": True, "accion": act, "intentos": tried}
                    time.sleep(0.05)
        self.log("menu_sigue_abierto", intentos=tried)
        return {"ok": False, "intentos": tried}

    # --- Custom AI ------------------------------------------------------------------------

    def car_data(self) -> cai.CarData | None:
        if self._car_region is None:
            self._car_region = self.registry.open_existing(cai.car_name(0), ctypes.sizeof(cai.CarData))
            if self._car_region is None:
                return None
        return cai.from_bytes(cai.CarData, self._car_region.read())

    def write_controls(self) -> None:
        if self._ctl_region is not None:
            self._ctl_region.write(cai.to_bytes(self.controls))

    def write_sim_state(self) -> None:
        if self._sim_region is not None:
            self._sim_region.write(cai.to_bytes(self.sim_state))

    # --- latido y parada -----------------------------------------------------------------------

    def heartbeat(self) -> None:
        if self.bridge is not None:
            self.bridge.publish()

    def safe_stop(self) -> None:
        c = self.controls
        c.gas, c.brake, c.steer, c.handbrake = 0.0, 1.0, 0.0, 0.0
        c.gear_up = c.gear_dn = False
        self.write_controls()
        if self.bridge is not None:
            self.bridge.safe_stop()
        self.log("parada_segura")

    def action(self, act: int, timeout: float = 3.0, **values) -> int:
        """Acción puntual en la app Lua; espera la confirmación (``action_done_seq``)."""
        seq = self.bridge.request(act, **values)
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.heartbeat()
            tel = self.bridge.telemetry()
            if tel and tel["action_done_seq"] == seq:
                return tel["action_result"]
            time.sleep(0.02)
        return 0


def _brief(tel: dict) -> dict:
    keys = ("frame", "phys_ms", "sim_ms", "car_id", "gear", "speed_kmh", "is_live", "paused", "in_menu",
            "phys_available", "phys_api", "override_active", "error", "file", "rows")
    return {k: tel.get(k) for k in keys}
