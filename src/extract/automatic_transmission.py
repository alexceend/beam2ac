"""Cajas de doble embrague (``dctGearbox``) y automáticas con convertidor de par
(``automaticGearbox`` + ``torqueConverter``) de las exportaciones de Automation (G2.11).

Lógica de ORIGEN: lee los parámetros de los dispositivos ya resueltos por
:func:`src.extract.drivetrain.read_devices` y las opciones del ``vehicleController`` y
registra qué valores vienen del JBeam y cuáles son el valor por defecto del Lua del
juego (``defaults_used``, con archivo y línea). No sabe nada de Assetto Corsa.

Semántica (Lua del juego instalado, solo lectura; versiones en :data:`GAME_LUA`):

* ``dctGearbox`` (``powertrain/dctGearbox.lua``):
  - ``gearRatios`` se separa por signo, como ``manualGearbox``: los valores ≥ 0 son
    marchas hacia delante empezando en el índice 0 (el 0 de la lista = punto muerto),
    los negativos, marchas atrás (l. 417-445).
  - Dos embragues (marchas pares e impares, ``gearRatio1``/``gearRatio2``). El par de
    salida es ``(T1 − friction·sgn)·r1 + (T2 − friction·sgn)·r2`` (l. 79); con un
    embrague cerrado y el otro abierto, en régimen estable, es par de entrada · relación
    (sin pérdidas con ``friction`` = 0; ``torqueLossCoef`` se lee, l. 363, pero
    ``updateTorque`` no lo usa).
  - Capacidad de cada embrague: ``lockTorque`` o, si falta, ``maxTorque·1,25 +
    maxRPM·inertia·π/30`` del motor padre (l. 180; ``torqueData.maxTorque`` =
    ``getTorqueData``, combustionEngine.lua l. 1426-1428, 240).
  - El padre tiene que ser el motor (``validate``, l. 173-178).
  - Lógica de cambio (``controller/vehicleController/shiftLogic/dctGearbox.lua``):
    traspaso entre embragues en ``[dctClutchTime, 10·dctClutchTime]`` según la
    agresividad (l. 511, 698; por defecto 0,05 s, l. 840); salida con
    ``clutchLaunchStartRPM``/``clutchLaunchTargetRPM`` (l. 796-797; 2000/3000 por
    defecto); ``revMatchThrottle`` en reducciones (l. 799; 0,5).
* ``automaticGearbox`` (``powertrain/automaticGearbox.lua``): mismas reglas de
  ``gearRatios`` (l. 420-449). En marcha y fuera de un cambio, salida =
  ``(T_entrada·shiftLossCoef − fricción)·relación`` (l. 64-65), con ``shiftLossCoef``
  = 1 salvo durante el cambio (l. 126) y fricción 0 con ``friction``,
  ``dynamicFriction`` y ``torqueLossCoef`` = 0; el embrague unidireccional solo actúa
  al rodar contra la marcha (l. 58-60).
* ``torqueConverter`` (``powertrain/torqueConverter.lua``):
  - par absorbido por la bomba ``k(r)·kFactorCoef·ω_in²``, con
    ``k(r) = −0,004·s·(r − 1)/(1 + s·|r − 1|)``, ``r = ω_out/ω_in``, ``s`` =
    ``converterStiffness`` y ``kFactorCoef = 844·D⁵`` (l. 25-36, 179-181), limitado a
    ``converterTorque`` (por defecto ``maxTorque·1,25 + maxRPM·inertia·π/30``, l. 88);
  - multiplicación ``clamp(stall − (stall − 1)·r/couplingAVRatio, 1, stall)`` (l. 38);
  - embrague de bloqueo (``lockupClutchTorque``, l. 31-33) gobernado por la lógica de
    cambio: solo en marchas ≥ ``torqueConverterLockupMinGear`` y por encima de
    ``torqueConverterLockupRPM`` (+ ``torqueConverterLockupRange``), con bloqueo
    parcial ``1 − max(0, gas − 0,85)·1,5`` en modo D
    (``shiftLogic/automaticGearbox.lua`` l. 381-405, 659-671).
* ``rangeBox`` (``powertrain/rangeBox.lua``): relación ``gearRatios[índice]``;
  arranca en el índice 0 (l. 190), la primera relación ≥ 0 de la lista.

Ninguna de estas estructuras tiene equivalente en el AC analizado (G2.11): no
hay convertidor de par ni caja reductora, y el cambio automático de AC es una ayuda del
piloto (``[AUTO_SHIFTER]``), no una propiedad del coche.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.extract.drivetrain import Device, DrivetrainDataError

DCT_LUA = "lua/vehicle/powertrain/dctGearbox.lua"
AUTO_LUA = "lua/vehicle/powertrain/automaticGearbox.lua"
TC_LUA = "lua/vehicle/powertrain/torqueConverter.lua"
RANGE_LUA = "lua/vehicle/powertrain/rangeBox.lua"
SHIFT_DCT_LUA = "lua/vehicle/controller/vehicleController/shiftLogic/dctGearbox.lua"
SHIFT_AUTO_LUA = (
    "lua/vehicle/controller/vehicleController/shiftLogic/automaticGearbox.lua"
)
# 2026-11-19 (CVT, agus_1_nrt_1)
CVT_LUA = "lua/vehicle/powertrain/cvtGearbox.lua"
SHIFT_CVT_LUA = "lua/vehicle/controller/vehicleController/shiftLogic/cvtGearbox.lua"
CONTROLLER_LUA = "lua/vehicle/controller/vehicleController/vehicleController.lua"
# Versiones leídas (procedencia; la instalación del juego no es una entrada del
# conversor y no se comprueba en tiempo de ejecución).
GAME_LUA = {
    DCT_LUA: "d07d3ba3ab3919c383f593dec54e9afec02264e1a9337b0dae90ee81a1da0d63",
    AUTO_LUA: "51b48f3ae63cc4f6edf76b1f92833e12e3e2db29c4e91d4a33121f5bdf7199a5",
    TC_LUA: "f97bf94d2e8d76c778d4b1526ebd3fa64b0587f866367892ac3ca3273cca943f",
    RANGE_LUA: "86aba92a803bf3a6d36782312dba4efb2876b213660db35a987f48b43417297f",
    SHIFT_DCT_LUA: "99e9470c5252f2c3db8569583ef631c473c142e0c258a56035021c19439de5b0",
    SHIFT_AUTO_LUA: "db41eee31aeee086c0a7f2bd789eeb0d4b6ab22312e41f7db9241df08a47e557",
    CONTROLLER_LUA: "c25b6e5f96a310337e983be44187dba037ba195ab23205b968d7adf2c68ee5be",
    CVT_LUA: "10d84c4870d5b24be91090d1d6c36e78e7628d1b3c101882850a07cabd30c3a4",
    SHIFT_CVT_LUA: "a52436d1b93ba6487bb2275e576fd246ad70859ac5da2cee38b9dee7b3721555",
}
FLUID_DENSITY = 844.0  # torqueConverter.lua l. 179 (constante del Lua, no del JBeam)
DCT_CROSSOVER_MAX_FACTOR = 10.0  # shiftLogic/dctGearbox.lua l. 511, 698


@dataclass(frozen=True)
class Sourced:
    """Valor con su origen: ``jbeam`` (leído) o ``lua_default`` (valor por defecto del
    Lua, con archivo y línea)."""

    value: Any
    source: str  # jbeam | lua_default
    key: str  # ``<dispositivo>.<clave>`` o ``vehicleController.<clave>``
    locator: str = ""  # archivo:línea del valor por defecto

    @property
    def is_default(self) -> bool:
        return self.source == "lua_default"


@dataclass(frozen=True)
class DctSetup:
    device: str
    friction: Sourced
    lock_torque: (
        Sourced | None
    )  # None: valor por defecto que depende del motor (l. 180)
    clutch_time: Sourced  # dctClutchTime: traspaso mínimo (agresividad 1)
    shift_delay: Sourced  # transmissionShiftDelay
    launch_start_rpm: Sourced
    launch_target_rpm: Sourced
    rev_match_throttle: Sourced
    automatic_modes: Sourced
    defaults_used: dict[str, str] = field(default_factory=dict)

    @property
    def crossover_time_range(self) -> tuple[float, float]:
        t = float(self.clutch_time.value)
        return t, t * DCT_CROSSOVER_MAX_FACTOR


@dataclass(frozen=True)
class TorqueConverterSetup:
    device: str
    diameter: Sourced  # m
    stiffness: Sourced  # 1
    coupling_av_ratio: Sourced  # 1
    stall_torque_ratio: Sourced  # 1
    lockup_clutch_torque: Sourced  # N·m
    converter_torque: Sourced | None  # None: por defecto depende del motor (l. 88)
    k_factor_smoothing: Sourced
    lockup_rpm: Sourced  # rpm (0 = sin bloqueo)
    lockup_range: Sourced  # rpm
    lockup_min_gear: Sourced
    lockup_rate: Sourced
    has_partial_lockup: Sourced
    partial_lockup_tip_in_threshold: Sourced
    partial_lockup_tip_in_gain: Sourced
    gearbox_friction: Sourced  # automaticGearbox.friction
    gearbox_dynamic_friction: Sourced
    gearbox_torque_loss_coef: Sourced
    gear_change_time: Sourced  # automaticGearbox.gearChangeTime (s)
    shift_efficiency: Sourced  # automaticGearbox.shiftEfficiency
    automatic_modes: Sourced
    defaults_used: dict[str, str] = field(default_factory=dict)

    @property
    def k_factor_coef(self) -> float:
        """``kFactorCoef = 844·D⁵`` (torqueConverter.lua l. 181)."""
        return FLUID_DENSITY * float(self.diameter.value) ** 5

    @property
    def has_lockup(self) -> bool:
        """``hasLockup = lockupAV > 0`` (shiftLogic/automaticGearbox.lua l. 662)."""
        return float(self.lockup_rpm.value) > 0


@dataclass(frozen=True)
class CvtSetup:
    """Caja de relación continua (``cvtGearbox``) y su lógica de rpm objetivo
    (shiftLogic/cvtGearbox.lua): ``targetAV = lowAV + (highAV − lowAV)·agresividad`` y
    ``relación += (targetAV − engineAV)·cvtAggression/60`` (l. 229, 236, 437-440)."""

    device: str
    min_ratio: float  # minGearRatio
    max_ratio: float  # maxGearRatio
    low_rpm: Sourced  # cvtLowRPM
    high_rpm: Sourced  # cvtHighRPM
    aggression: Sourced  # cvtAggression (ganancia del lazo de rpm; 0,5 por defecto)
    aggression_down: (
        Sourced  # aggressionSmoothingDown (0,15 por defecto; vehicleController l. 1016)
    )
    automatic_modes: Sourced  # automaticModes ("PRND" por defecto)
    defaults_used: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RangeBoxSetup:
    device: str
    ratios: tuple[
        float, ...
    ]  # relaciones ≥ 0 en el orden del JBeam (índice 0 = inicial)
    initial_ratio: float  # gearRatios[0] (rangeBox.lua l. 190)


def _num(value: Any, what: str, *, positive: bool = False, nonneg: bool = False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DrivetrainDataError(f"{what}: se esperaba un número, no {value!r}")
    if not math.isfinite(value):
        raise DrivetrainDataError(f"{what}: valor no finito")
    if positive and value <= 0:
        raise DrivetrainDataError(f"{what}: debe ser > 0, es {value}")
    if nonneg and value < 0:
        raise DrivetrainDataError(f"{what}: debe ser ≥ 0, es {value}")
    return float(value)


def _get(
    params: dict[str, Any],
    prefix: str,
    key: str,
    default: Any,
    locator: str,
    defaults: dict[str, str],
    **checks: bool,
) -> Sourced:
    if key in params:
        value = params[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise DrivetrainDataError(f"{prefix}.{key}: se esperaba un booleano")
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise DrivetrainDataError(f"{prefix}.{key}: se esperaba un texto")
        else:
            value = _num(value, f"{prefix}.{key}", **checks)
        return Sourced(value, "jbeam", f"{prefix}.{key}")
    defaults[f"{prefix}.{key}"] = locator
    return Sourced(default, "lua_default", f"{prefix}.{key}", locator)


def read_dct(gearbox: Device, controller: dict[str, Any]) -> DctSetup:
    """Parámetros de un ``dctGearbox`` y de su lógica de cambio."""
    if gearbox.type != "dctGearbox":
        raise DrivetrainDataError(f"'{gearbox.name}' no es dctGearbox")
    d: dict[str, str] = {}
    p, c = gearbox.params, controller
    g, vc = gearbox.name, "vehicleController"
    lock = None
    if "lockTorque" in p:
        lock = Sourced(
            _num(p["lockTorque"], f"{g}.lockTorque", positive=True),
            "jbeam",
            f"{g}.lockTorque",
        )
    return DctSetup(
        device=g,
        friction=_get(p, g, "friction", 0.0, f"{DCT_LUA}:361", d, nonneg=True),
        lock_torque=lock,
        clutch_time=_get(
            c, vc, "dctClutchTime", 0.05, f"{SHIFT_DCT_LUA}:840", d, positive=True
        ),
        shift_delay=_get(
            c,
            vc,
            "transmissionShiftDelay",
            0.2,
            f"{CONTROLLER_LUA}:1059",
            d,
            nonneg=True,
        ),
        launch_start_rpm=_get(
            c,
            vc,
            "clutchLaunchStartRPM",
            2000.0,
            f"{SHIFT_DCT_LUA}:797",
            d,
            positive=True,
        ),
        launch_target_rpm=_get(
            c,
            vc,
            "clutchLaunchTargetRPM",
            3000.0,
            f"{SHIFT_DCT_LUA}:796",
            d,
            positive=True,
        ),
        rev_match_throttle=_get(
            c, vc, "revMatchThrottle", 0.5, f"{SHIFT_DCT_LUA}:799", d, nonneg=True
        ),
        automatic_modes=_get(
            c, vc, "automaticModes", "PRNDS21M", f"{SHIFT_DCT_LUA}:808", d
        ),
        defaults_used=d,
    )


def read_torque_converter(
    converter: Device, gearbox: Device, controller: dict[str, Any]
) -> TorqueConverterSetup:
    """Parámetros de un ``torqueConverter``, de la ``automaticGearbox`` que alimenta
    y del control de bloqueo del ``vehicleController``."""
    if converter.type != "torqueConverter":
        raise DrivetrainDataError(f"'{converter.name}' no es torqueConverter")
    cvt = gearbox.type == "cvtGearbox"
    if gearbox.type != "automaticGearbox" and not cvt:
        raise DrivetrainDataError(
            f"'{gearbox.name}' no es automaticGearbox ni cvtGearbox"
        )
    shift_lua = SHIFT_CVT_LUA if cvt else SHIFT_AUTO_LUA
    d: dict[str, str] = {}
    p, gp, c = converter.params, gearbox.params, controller
    t, g, vc = converter.name, gearbox.name, "vehicleController"
    conv = None
    if "converterTorque" in p:
        conv = Sourced(
            _num(p["converterTorque"], f"{t}.converterTorque", positive=True),
            "jbeam",
            f"{t}.converterTorque",
        )
    setup = TorqueConverterSetup(
        device=t,
        diameter=_get(
            p, t, "converterDiameter", 0.30, f"{TC_LUA}:177", d, positive=True
        ),
        stiffness=_get(
            p, t, "converterStiffness", 10.0, f"{TC_LUA}:176", d, positive=True
        ),
        coupling_av_ratio=_get(
            p, t, "couplingAVRatio", 0.85, f"{TC_LUA}:174", d, positive=True
        ),
        stall_torque_ratio=_get(
            p, t, "stallTorqueRatio", 2.0, f"{TC_LUA}:175", d, positive=True
        ),
        lockup_clutch_torque=_get(
            p, t, "lockupClutchTorque", 100.0, f"{TC_LUA}:170", d, nonneg=True
        ),
        converter_torque=conv,
        k_factor_smoothing=_get(
            p, t, "kFactorSmoothing", 75.0, f"{TC_LUA}:180", d, positive=True
        ),
        lockup_rpm=_get(
            c,
            vc,
            "torqueConverterLockupRPM",
            0.0,
            f"{shift_lua}:{425 if cvt else 659}",
            d,
            nonneg=True,
        ),
        lockup_range=_lockup_range(c, vc, d, cvt),
        lockup_min_gear=_get(
            c,
            vc,
            "torqueConverterLockupMinGear",
            0.0,
            f"{shift_lua}:{427 if cvt else 661}",
            d,
            nonneg=True,
        ),
        lockup_rate=_get(
            c,
            vc,
            "torqueConverterLockupRate",
            5.0,
            f"{SHIFT_AUTO_LUA}:668",
            d,
            positive=True,
        ),
        has_partial_lockup=_get(
            c, vc, "torqueConverterHasPartialLockup", True, f"{SHIFT_AUTO_LUA}:663", d
        ),
        partial_lockup_tip_in_threshold=_get(
            c,
            vc,
            "partialLockupTipInThreshold",
            0.85,
            f"{SHIFT_AUTO_LUA}:665",
            d,
            nonneg=True,
        ),
        partial_lockup_tip_in_gain=_get(
            c,
            vc,
            "partialLockupTipInGain",
            1.5,
            f"{SHIFT_AUTO_LUA}:666",
            d,
            nonneg=True,
        ),
        gearbox_friction=_get(
            gp, g, "friction", 0.0, f"{AUTO_LUA}:376", d, nonneg=True
        ),
        gearbox_dynamic_friction=_get(
            gp, g, "dynamicFriction", 0.0, f"{AUTO_LUA}:377", d, nonneg=True
        ),
        gearbox_torque_loss_coef=_get(
            gp, g, "torqueLossCoef", 0.0, f"{AUTO_LUA}:378", d, nonneg=True
        ),
        gear_change_time=_get(
            gp, g, "gearChangeTime", 0.5, f"{AUTO_LUA}:401", d, nonneg=True
        ),
        shift_efficiency=_get(
            gp, g, "shiftEfficiency", 0.5, f"{AUTO_LUA}:391", d, nonneg=True
        ),
        automatic_modes=_get(
            c,
            vc,
            "automaticModes",
            "PRND" if cvt else "PRNDS21M",
            f"{SHIFT_CVT_LUA}:399" if cvt else f"{SHIFT_AUTO_LUA}:619",
            d,
        ),
        defaults_used=d,
    )
    stall = float(setup.stall_torque_ratio.value)
    if stall < 1:
        raise DrivetrainDataError(f"{t}.stallTorqueRatio < 1: {stall}")
    coupling = float(setup.coupling_av_ratio.value)
    if not 0 < coupling <= 1:
        raise DrivetrainDataError(f"{t}.couplingAVRatio fuera de (0, 1]: {coupling}")
    return setup


def _lockup_range(
    c: dict[str, Any], vc: str, d: dict[str, str], cvt: bool = False
) -> Sourced:
    """``torqueConverterLockupRange`` o, si falta, ``0,2·lockupRPM``
    (shiftLogic/automaticGearbox.lua l. 660; cvtGearbox.lua l. 426)."""
    if "torqueConverterLockupRange" in c:
        return Sourced(
            _num(
                c["torqueConverterLockupRange"],
                f"{vc}.torqueConverterLockupRange",
                positive=True,
            ),
            "jbeam",
            f"{vc}.torqueConverterLockupRange",
        )
    rpm = c.get("torqueConverterLockupRPM", 0)
    rpm = _num(rpm, f"{vc}.torqueConverterLockupRPM", nonneg=True)
    loc = f"{SHIFT_CVT_LUA}:426" if cvt else f"{SHIFT_AUTO_LUA}:660"
    d[f"{vc}.torqueConverterLockupRange"] = loc
    return Sourced(0.2 * rpm, "lua_default", f"{vc}.torqueConverterLockupRange", loc)


def read_cvt(dt: Any, controller: dict[str, Any]) -> CvtSetup:
    """Parámetros de la ``cvtGearbox`` de ``dt`` (relaciones ya leídas en
    ``dt.cvt_range``) y de su lógica de rpm objetivo. ``cvtLowRPM``/``cvtHighRPM`` son
    obligatorios: sin ellos el juego usa los puntos de cambio de la primera marcha
    (l. 439-440), que una exportación de Automation no define; se rechaza en vez de
    inventarlos."""
    if dt.cvt_range is None:
        raise DrivetrainDataError("cvtGearbox sin rango de relaciones")
    vc = "vehicleController"
    d: dict[str, str] = {}
    for key in ("cvtLowRPM", "cvtHighRPM"):
        if key not in controller:
            raise DrivetrainDataError(f"{vc}.{key} ausente en una cvtGearbox")
    low = _get(controller, vc, "cvtLowRPM", 0.0, "", d, positive=True)
    high = _get(controller, vc, "cvtHighRPM", 0.0, "", d, positive=True)
    if low.value >= high.value:
        raise DrivetrainDataError(f"cvtLowRPM {low.value} ≥ cvtHighRPM {high.value}")
    return CvtSetup(
        device=dt.gearbox.name,
        min_ratio=dt.cvt_range[0],
        max_ratio=dt.cvt_range[1],
        low_rpm=low,
        high_rpm=high,
        aggression=_get(
            controller,
            vc,
            "cvtAggression",
            0.5,
            f"{SHIFT_CVT_LUA}:437",
            d,
            positive=True,
        ),
        aggression_down=_get(
            controller,
            vc,
            "aggressionSmoothingDown",
            0.15,
            f"{CONTROLLER_LUA}:1016",
            d,
            positive=True,
        ),
        automatic_modes=_get(
            controller, vc, "automaticModes", "PRND", f"{SHIFT_CVT_LUA}:399", d
        ),
        defaults_used=d,
    )


def read_range_box(device: Device) -> RangeBoxSetup:
    """Relaciones de un ``rangeBox`` (rangeBox.lua l. 155-190)."""
    raw = device.params.get("gearRatios")
    if not isinstance(raw, list) or not raw:
        raise DrivetrainDataError(f"'{device.name}' sin gearRatios")
    ratios = [_num(v, f"{device.name}.gearRatios") for v in raw]
    if any(v < 0 for v in ratios):
        raise DrivetrainDataError(
            f"'{device.name}': relaciones negativas en una caja reductora {ratios}: "
            "no visto en Automation"
        )
    return RangeBoxSetup(device.name, tuple(ratios), ratios[0])


def default_engine_coupling_torque(
    peak_torque: float, max_rpm: float, inertia: float
) -> float:
    """``maxTorque·1,25 + maxRPM·inertia·π/30``: capacidad por defecto de los embragues
    de la DCT (dctGearbox.lua l. 180) y límite del convertidor (torqueConverter.lua
    l. 88). ``maxTorque`` = par máximo de ``getTorqueData`` del motor padre, ``maxRPM``
    = su techo de rpm, ``inertia`` su inercia (kg·m²)."""
    return peak_torque * 1.25 + max_rpm * inertia * math.pi / 30


def converter_k_factor(setup: TorqueConverterSetup, speed_ratio: float) -> float:
    """``k(r) = −0,004·s·(r − 1)/(1 + s·|r − 1|)`` (torqueConverter.lua l. 35, régimen
    estable: el suavizado exponencial no cambia el valor final)."""
    s = float(setup.stiffness.value)
    x = speed_ratio - 1.0
    return -0.004 * s * x / (1.0 + s * abs(x))


def converter_torque_ratio(setup: TorqueConverterSetup, speed_ratio: float) -> float:
    """Multiplicación de par (torqueConverter.lua l. 38)."""
    stall = float(setup.stall_torque_ratio.value)
    coupling = float(setup.coupling_av_ratio.value)
    return min(max(stall - (stall - 1.0) * speed_ratio / coupling, 1.0), stall)


def converter_absorbed_torque(
    setup: TorqueConverterSetup, input_av: float, speed_ratio: float
) -> float:
    """Par que absorbe la bomba a ``input_av`` (rad/s) sin límite de
    ``converterTorque`` (l. 36)."""
    return converter_k_factor(setup, speed_ratio) * setup.k_factor_coef * input_av**2


def converter_steady_state(
    setup: TorqueConverterSetup, input_av: float, engine_torque: float
) -> tuple[float, float] | None:
    """Relación de velocidades ``r`` en [0, 1) a la que el convertidor sin bloquear
    absorbe ``engine_torque`` a ``input_av``, y su multiplicación de par. ``None`` si ni
    con la salida parada (``r`` = 0) la bomba absorbe ese par (el motor sube de rpm:
    por encima de la velocidad de calado). Bisección sobre ``k(r)``, que decrece con
    ``r`` en [0, 1]."""
    if engine_torque <= 0 or input_av <= 0:
        return None
    if converter_absorbed_torque(setup, input_av, 0.0) < engine_torque:
        return None
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if converter_absorbed_torque(setup, input_av, mid) > engine_torque:
            lo = mid
        else:
            hi = mid
    r = 0.5 * (lo + hi)
    return r, converter_torque_ratio(setup, r)


def stall_speed_rpm(setup: TorqueConverterSetup, engine_torque_at) -> float | None:
    """Velocidad de calado: rpm a la que el par del motor iguala al que absorbe la bomba
    con la salida parada, ``T(n) = k(0)·kFactorCoef·ω²``. ``engine_torque_at(rpm)``
    da el par a plena carga. Búsqueda por pasos de 1 rpm entre 1 y 20 000; ``None``
    si no se cruzan."""
    prev = None
    for rpm in range(1, 20001):
        av = rpm * math.pi / 30
        diff = converter_absorbed_torque(setup, av, 0.0) - engine_torque_at(rpm)
        if prev is not None and prev < 0 <= diff:
            return float(rpm)
        prev = diff
    return None


@dataclass(frozen=True)
class AutomaticTransmission:
    """Caja DCT o automática con convertidor de la configuración activa (G2.11)."""

    kind: str  # dctGearbox | automaticGearbox | cvtGearbox
    dct: DctSetup | None = None
    converter: TorqueConverterSetup | None = None
    range_box: RangeBoxSetup | None = None
    cvt: CvtSetup | None = None


def read_transmission(
    dt: Any, controller: dict[str, Any]
) -> AutomaticTransmission | None:
    """Parámetros de la caja ``dt.gearbox`` (``src.extract.drivetrain.Drivetrain``) y de
    su lógica de cambio. ``None`` con ``manualGearbox``/``sequentialGearbox``
    (camino sin cambios). La estructura ya la comprobó ``check_transmission_structure``.
    """
    kind = dt.gearbox.type
    range_box = read_range_box(dt.range_box) if dt.range_box is not None else None
    if kind == "dctGearbox":
        return AutomaticTransmission(
            kind, dct=read_dct(dt.gearbox, controller), range_box=range_box
        )
    if kind in ("automaticGearbox", "cvtGearbox"):
        if dt.converter is None:
            raise DrivetrainDataError(f"{kind} sin torqueConverter")
        return AutomaticTransmission(
            kind,
            converter=read_torque_converter(dt.converter, dt.gearbox, controller),
            range_box=range_box,
            cvt=read_cvt(dt, controller) if kind == "cvtGearbox" else None,
        )
    return None
