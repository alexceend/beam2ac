"""Modelo intermedio (IR) del vehículo, independiente del origen y del destino.

Subsistemas separados (dataclasses pequeñas); cada dato es un :class:`Param`. Los
nombres son físicos y neutros (``idle_speed``, ``wot_torque_curve``…), no claves de
BeamNG ni de Assetto Corsa. Lo que falta queda ``missing`` con su motivo; lo que no
existe en el vehículo, ``not_applicable``; lo que no se pudo interpretar, en
``unknown`` con el valor crudo.

Rutas: ``engine.idle_speed``, ``drivetrain.gearbox.forward_ratios.1`` (marchas desde 1),
``drivetrain.differentials.front.kind``, ``suspension.axles.rear.label``,
``formats.code_versions.camsoEngine``. Ver :meth:`VehicleIR.get` y :meth:`walk`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Iterator

from .param import IRError, Param

AXLES = ("front", "rear")
DIFF_ROLES = ("front", "rear", "center")
SCHEMA = "beam2ac.vehicle_ir/1"


def _p(unit: str, reason: str = "") -> Any:
    return field(default_factory=lambda: Param.missing(unit, reason))


@dataclass
class Identity:
    vehicle_id: Param = _p("text")
    variant: Param = _p("text")
    name: Param = _p("text")
    source_tool: Param = _p("text")  # p. ej. "Automation" (info.json Type)
    author: Param = _p("text")


@dataclass
class Formats:
    source_format: Param = _p("text")
    exporter_version: Param = _p("text")
    exporter_version_declared: Param = _p(
        "text"
    )  # literal del origen (puede diferir de la anterior)
    code_versions: dict[str, Param] = field(default_factory=dict)  # nombre -> sha256


@dataclass
class Declared:
    """Valores que el origen DECLARA (interfaz), no medidos: solo para contrastar."""

    weight: Param = _p("kg")
    power: Param = _p("hp")
    torque: Param = _p("N·m")
    power_peak_speed: Param = _p("rpm")
    torque_peak_speed: Param = _p("rpm")
    drivetrain: Param = _p("enum")
    transmission: Param = _p("enum")
    implied_power: Param = _p("hp")
    top_speed: Param = _p("m/s")
    accel_0_100: Param = _p("s")


@dataclass
class Dimensions:
    wheelbase: Param = _p("m")
    track_front: Param = _p("m")
    track_rear: Param = _p("m")
    cog_height: Param = _p("m")
    front_axle_fraction: Param = _p("1")
    # G2.18: altura media de los centros de rueda de cada eje MENOS la del CdG del
    # vehículo entero (ejes AC, + arriba). Diferencia medida en los nodos: no depende
    # de la hipótesis del suelo de ``cog_height``. Solo con 2 ejes.
    wheel_center_height_rel_cog_front: Param = _p("m")
    wheel_center_height_rel_cog_rear: Param = _p("m")


@dataclass
class Mass:
    total: Param = _p("kg")
    # masa Σ obj:getNodeMass medida en BeamNG por el banco de neumáticos (cierre de
    # fuerzas, ``closure.weight``/g): es la masa del juego, la que debe reproducir el
    # conversor (el ``Weight`` de la ficha de Automation puede quedar por debajo)
    in_game_total: Param = _p("kg")
    # G2.26: diagonal del tensor de inercia del vehículo ENTERO respecto a su CdG, en
    # ejes AC [X cabeceo, Y guiñada, Z balanceo], de las masas puntuales de los nodos
    # (+ masa generada de las ruedas en su centro).
    inertia_about_cog: Param = _p("kg·m²")
    wheel_assembly: Param = _p("kg")
    driver: Param = _p("kg")
    # G2.45: centro de masas del piloto, ESTIMADO (no medido), ejes AC [x izquierda,
    # y arriba, z adelante] relativo al CdG del vehículo entero (sin piloto).
    driver_position: Param = _p("m")
    fuel_capacity: Param = _p("L")
    fuel_start: Param = _p("L")


@dataclass
class Engine:
    device_type: Param = _p("enum")
    idle_speed: Param = _p("rpm")
    inertia: Param = _p("kg·m²")
    clutch_additional_inertia: Param = _p("kg·m²")
    rev_limiter_mode: Param = _p("enum")
    speed_ceiling: Param = _p("rpm")
    wot_torque_curve: Param = _p("table:rpm->N·m")  # par neto a plena carga
    peak_torque: Param = _p("N·m")
    peak_torque_speed: Param = _p("rpm")
    peak_power: Param = _p("W")
    peak_power_speed: Param = _p("rpm")
    # Referencia de Automation (solo camsoEngine): misma curva con la fila 100 del mapa de par, la que
    # Automation usa a gas a fondo y BeamNG no lee (``throttleLookup`` ≤ 99). Valida los ``info_*.json``;
    # NO es la curva que se convierte (``wot_torque_curve``).
    reference_wot_torque_curve: Param = _p("table:rpm->N·m")
    reference_peak_torque: Param = _p("N·m")
    reference_peak_power: Param = _p("W")
    # Sobrealimentación con boost (solo camsoEngine con presión > 1 en el mapa): tipo, presión ABSOLUTA del mapa
    # a gas a fondo (fila 99) por rpm, boost manométrico máximo y rpm de boost completo. Alimentan el turbo
    # ajustable opcional (src/ac/adjustable_turbo.py); no cambian la curva convertida.
    boost_kind: Param = _p("enum")
    boost_pressure_curve: Param = _p("table:rpm->bar")
    boost_max: Param = _p("bar")
    boost_full_rpm: Param = _p("rpm")
    forced_induction: Param = _p("enum")
    coast_curve: Param = _p("table:rpm->N·m")  # freno motor a gas cerrado (positivo)
    coast_basis: Param = _p("text")  # versión y fila del mapa usada


@dataclass
class Gearbox:
    kind: Param = _p("enum")
    reverse_ratio: Param = _p("1")
    forward_count: Param = _p("1")
    forward_ratios: list[Param] = field(default_factory=list)
    shift_delay: Param = _p("s")
    # G2.11: traspaso entre los dos embragues de una caja de doble embrague con
    # agresividad máxima (``dctClutchTime``); «no aplica» en otras cajas
    shift_crossover_time: Param = _p("s")
    # 2026-11-19: caja de relación continua (``cvtGearbox``): rango de relaciones y rpm
    # objetivo (``cvtLowRPM``/``cvtHighRPM``), ganancia del lazo de rpm
    # (``cvtAggression``) y modos del selector; «no aplica» con cajas de marchas fijas.
    # Con CVT, ``forward_*`` y ``reverse_ratio`` son «no aplica» (no hay marchas).
    cvt_ratio_min: Param = _p("1")
    cvt_ratio_max: Param = _p("1")
    cvt_rpm_low: Param = _p("rpm")
    cvt_rpm_high: Param = _p("rpm")
    cvt_aggression: Param = _p("1")
    cvt_aggression_down: Param = _p("1")  # aggressionSmoothingDown (1/s)
    automatic_modes: Param = _p("text")


@dataclass
class Clutch:
    lock_torque: Param = _p("N·m")
    lock_torque_default: Param = _p("N·m")


@dataclass
class TorqueConverter:
    """Convertidor de par delante de una caja automática (G2.11; ``torqueConverter`` de
    BeamNG). «No aplica» sin convertidor."""

    stall_torque_ratio: Param = _p("1")  # multiplicación de par con la salida parada
    coupling_speed_ratio: Param = _p("1")  # salida/entrada desde la que multiplica 1
    diameter: Param = _p("m")
    stiffness: Param = _p("1")
    torque_limit: Param = _p("N·m")  # par máximo que transmite el fluido
    lockup_clutch_torque: Param = _p("N·m")
    lockup_speed: Param = _p("rpm")  # rpm de entrada de la caja; 0 = sin bloqueo
    lockup_range: Param = _p("rpm")
    lockup_min_gear: Param = _p("1")


@dataclass
class Differential:
    kind: Param = _p("enum")
    final_ratio: Param = _p("1")
    preload: Param = _p("N·m")
    lock_coef: Param = _p("1")
    rev_lock_coef: Param = _p("1")
    # G2.5c: fracción del par del diferencial central que recibe este eje (AWD);
    # «no aplica» en un eje motriz sin central.
    torque_share: Param = _p("1")
    # G2.44: modo con el que arranca el diferencial (``diffType`` literal, o el primero
    # de una lista: differential.lua l. 307-309) y ``lockTorque`` DECLARADO (sin él, el
    # juego lo calcula a partir de las inercias de salida: l. 235-238).
    initial_mode: Param = _p("enum")
    lock_torque: Param = _p("N·m")
    # ``viscousTorque`` DECLARADO (differential.lua l. 68-83, 399-404: techo del par que el fluido
    # transfiere entre salidas); solo en un diferencial ``viscous``
    viscous_torque: Param = _p("N·m")


@dataclass
class Drivetrain:
    layout: Param = _p("enum")  # FWD | RWD | AWD
    # G2.5c: ruedas motrices (identificadores de rueda del origen, ordenados)
    driven_wheels: Param = _p("text")
    final_ratio: Param = _p("1")
    front_torque_share: Param = _p("1")
    gearbox: Gearbox = field(default_factory=Gearbox)
    clutch: Clutch = field(default_factory=Clutch)
    torque_converter: TorqueConverter = field(default_factory=TorqueConverter)
    differentials: dict[str, Differential] = field(default_factory=dict)
    # G2.7 (G2.6b): eje motriz único (front | rear) y SU diferencial. Con un solo eje
    # motriz y sin central, ``driven_axle_differential`` ES el objeto
    # ``differentials[driven_axle]`` (mismos Param: no pueden divergir); en AWD con
    # central, «no aplica»; con tracción desconocida o AWD sin central, ausente.
    driven_axle: Param = _p("enum")
    driven_axle_differential: Differential = field(default_factory=Differential)


@dataclass
class AxleSuspension:
    label: Param = _p("text")
    spring_rate_beam: Param = _p("N/m")
    motion_ratio_hyp: Param = _p("1")
    torsionbar_rates: list[Param] = field(default_factory=list)
    # G2.14: masa no suspendida de UNA rueda de un eje independiente (cierre rígido de
    # nodos + masa generada de la rueda); ausente con motivo en un eje acoplado
    unsprung_mass_per_wheel: Param = _p("kg")
    # G2.16: estructura de enlaces MEDIDA (src/extract/linkage.py: dwb_5link,
    # dwb_equivalent, overconstrained, underconstrained, coupled, unsupported), sus 8
    # anclajes de doble triángulo (m, relativos al centro de rueda en reposo; ejes
    # lateral hacia dentro / arriba / adelante, rueda derecha) y lo que de ellos se
    # deriva: palanca cinemática del muelle, rigidez y amortiguación lenta en rueda,
    # precarga como recorrido de rueda y caída/convergencia en reposo
    linkage: Param = _p("enum")
    anchors: dict[str, Param] = field(default_factory=dict)
    kinematic_motion_ratio: Param = _p("1")
    # G2.19: con puntal en raíl (linkage = strut_rail): palanca del eje del puntal de
    # AC, |d(longitud apoyo superior–nodo deslizante)/dh| en reposo
    strut_motion_ratio: Param = _p("1")
    wheel_rate: Param = _p("N/m")
    spring_preload_travel: Param = _p("m")
    damper_rate_slow: Param = _p("N·s/m")
    rest_camber: Param = _p("deg")
    rest_toe: Param = _p("deg")
    # G2.17: con el DWB medido: sensibilidad CON SIGNO de la convergencia al
    # punto del chasis de la barra (m hacia dentro por grado de toe-in);
    # amortiguación por sentido (compresión = el amortiguador se acorta: banco de
    # G2.17) y rápida con su umbral (en rueda); recorrido vertical hasta los topes,
    # su rigidez y progresividad en rueda (ley medida en la zona de transición) y su
    # amortiguación; rigidez vertical equivalente de la barra estabilizadora
    # (k_t·(dθ/dh)², N por m de diferencia de altura entre las ruedas del eje)
    rod_inward_shift_per_toe_in: Param = _p("m/deg")
    damper_rate_slow_bump: Param = _p("N·s/m")
    damper_rate_slow_rebound: Param = _p("N·s/m")
    damper_rate_fast_bump: Param = _p("N·s/m")
    damper_rate_fast_rebound: Param = _p("N·s/m")
    damper_fast_threshold_bump: Param = _p("m/s")
    damper_fast_threshold_rebound: Param = _p("m/s")
    bumpstop_travel_up: Param = _p("m")
    bumpstop_travel_down: Param = _p("m")
    bumpstop_rate: Param = _p("N/m")
    bumpstop_progressive: Param = _p("N/m²")
    bumpstop_damping: Param = _p("N·s/m")
    antiroll_rate: Param = _p("N/m")
    # G2.44 (linkage = rigid_axle): posición lateral del muelle como fracción de la
    # semivía (ATTACH_REL_POS de AC) y número de enlaces de barra del eje; los puntos
    # J<n>_CAR/J<n>_AXLE van en ``anchors`` (relativos al centro del eje, ejes AC)
    attach_rel_pos: Param = _p("1")
    link_count: Param = _p("1")


@dataclass
class Suspension:
    axles: dict[str, AxleSuspension] = field(default_factory=dict)


@dataclass
class Steering:
    """Dirección (G2.15). ``wheel_lock`` es el ángulo máximo de la RUEDA y
    ``steering_wheel_lock`` el del VOLANTE: no se confunden."""

    wheel_lock: Param = _p("deg")  # rueda, a tope (motivo: importador o G2.8)
    steering_ratio: Param = _p("1")  # volante/rueda
    # G2.15: mecanismo (rack = cremallera en raíl movida por hidros), grados de VOLANTE
    # a tope (entrada 1) y recorrido de la cremallera a tope en cada sentido
    mechanism: Param = _p("enum")
    steering_wheel_lock: Param = _p("deg")
    rack_travel: Param = _p("m")
    # G2.16: con un doble triángulo delantero medido y la barra unida al extremo de la
    # cremallera: posición de la barra respecto al eje (ahead | behind) y desplazamiento
    # lateral de la barra por grado de rueda en el centro
    tie_rod_position: Param = _p("enum")
    rack_displacement_per_wheel_degree: Param = _p("m/deg")


@dataclass
class TyreAxle:
    radius: Param = _p("m")
    hub_radius: Param = _p("m")
    width: Param = _p("m")
    pressure: Param = _p("psi")
    # G2.20: medidas del banco de neumáticos de BeamNG (media de las dos ruedas del eje;
    # src/extract/tyre_bench.py): carga estática de la rueda en el banco, radio de
    # rodadura libre, rigidez y amortiguación verticales rodando, inercia del conjunto
    # rotatorio, resistencia a la rodadura (−Fx/Fz frente a v) y las muestras
    # fuerza–deslizamiento (dataset). La longitud de relajación no se mide (motivo).
    reference_load: Param = _p("N")
    free_rolling_radius: Param = _p("m")
    vertical_stiffness: Param = _p("N/m")
    vertical_damping: Param = _p("N·s/m")
    rotational_inertia: Param = _p("kg·m²")
    rolling_resistance_vs_speed: Param = _p("table:m/s->1")
    force_slip_samples: Param = _p("dataset")
    relaxation_length: Param = _p("m")


@dataclass
class Tyres:
    axles: dict[str, TyreAxle] = field(default_factory=dict)


@dataclass
class BrakeAxle:
    max_torque_per_wheel: Param = _p("N·m")
    parking_torque_per_wheel: Param = _p("N·m")
    # G2.15: codo del pedal (par = T·(min(e, s) + c·max(e − s, 0))), modelo térmico,
    # disco y ABS de la rueda (wheels.lua); sin hecho bng.* heredado
    input_split: Param = _p("1")
    split_coef: Param = _p("1")
    thermal_model: Param = _p("enum")
    pad_material: Param = _p("text")
    disc_type: Param = _p("enum")
    disc_diameter: Param = _p("m")
    disc_mass: Param = _p("kg")
    abs: Param = _p("enum")
    abs_slip_target: Param = _p("1")
    abs_frequency: Param = _p("Hz")  # absHz (por defecto 100: wheels.lua l. 1048)


@dataclass
class Brakes:
    axles: dict[str, BrakeAxle] = field(default_factory=dict)
    front_share_full_pedal: Param = _p("1")
    front_share_below_knee: Param = _p("1")
    # G2.50: resumen del ABS del vehículo para AC (un solo límite y una sola frecuencia)
    abs_present: Param = _p("1")
    abs_slip_limit: Param = _p("1")
    abs_rate_hz: Param = _p("Hz")


@dataclass
class Aero:
    # G2.15: cefaero (Lua) | triangles (C++ del juego) | none
    model: Param = _p("enum")
    cda: Param = _p("m²")
    target_front: Param = _p("N")
    target_rear: Param = _p("N")
    cla_front: Param = _p("m²")
    cla_rear: Param = _p("m²")
    # G2.19: aerodinámica por triángulos MEDIDA en el juego (banco de G2.15, registro de
    # ``measurements/`` asociado por la huella del origen;
    # ``src/extract/aero_bench.py``):
    # arrastre y carga en los centros de los ejes (+ = hacia abajo) frente al ángulo de
    # ataque (grados, = aoa de AC), como C·A (m²). Sin hecho bng.*
    cda_vs_aoa: Param = _p("table:deg->m²")
    cla_front_axle_vs_aoa: Param = _p("table:deg->m²")
    cla_rear_axle_vs_aoa: Param = _p("table:deg->m²")


@dataclass
class Resources:
    """Recursos no físicos (sin cargar los archivos): qué hay y de dónde sale."""

    files_by_role: dict[str, list[str]] = field(default_factory=dict)
    materials_file: Param = _p("text")
    paint_1: Param = _p("text")
    lights: Param = _p("text", "sin importador todavía")
    animations: Param = _p("text", "sin importador todavía")
    audio_present: Param = _p("enum")


@dataclass
class UnknownItem:
    """Dato del origen conservado sin interpretar."""

    source: str
    locator: str
    raw: Any
    reason: str


@dataclass
class VehicleIR:
    identity: Identity = field(default_factory=Identity)
    formats: Formats = field(default_factory=Formats)
    declared: Declared = field(default_factory=Declared)
    dimensions: Dimensions = field(default_factory=Dimensions)
    mass: Mass = field(default_factory=Mass)
    engine: Engine = field(default_factory=Engine)
    drivetrain: Drivetrain = field(default_factory=Drivetrain)
    suspension: Suspension = field(default_factory=Suspension)
    steering: Steering = field(default_factory=Steering)
    tyres: Tyres = field(default_factory=Tyres)
    brakes: Brakes = field(default_factory=Brakes)
    aero: Aero = field(default_factory=Aero)
    resources: Resources = field(default_factory=Resources)
    unknown: list[UnknownItem] = field(default_factory=list)
    architecture: dict[str, Any] = field(default_factory=dict)  # ver architecture.py

    # --- acceso por ruta -------------------------------------------------------------
    def _resolve(self, path: str, create: bool = False) -> tuple[Any, str]:
        parts = path.split(".")
        obj: Any = self
        for name in parts[:-1]:
            obj = _child(obj, name, create)
        return obj, parts[-1]

    def get(self, path: str) -> Param:
        obj, last = self._resolve(path)
        val = _child(obj, last, False)
        if not isinstance(val, Param):
            raise IRError(f"{path} no es un parámetro")
        return val

    def set(self, path: str, param: Param) -> None:
        """Asigna un parámetro existente en el esquema (los errores de nombre fallan).
        En listas, el índice es la posición desde 1 y debe ser la siguiente libre."""
        if not isinstance(param, Param):
            raise IRError(f"{path}: se esperaba Param")
        obj, last = self._resolve(path, create=True)
        if isinstance(obj, list):
            idx = int(last) - 1
            if idx == len(obj):
                obj.append(param)
            elif 0 <= idx < len(obj):
                obj[idx] = param
            else:
                raise IRError(f"{path}: índice fuera de orden")
        elif isinstance(obj, dict):
            obj[last] = param
        else:
            current = getattr(obj, last, None) if hasattr(obj, last) else None
            if not isinstance(current, Param):
                raise IRError(f"{path}: campo inexistente en el IR")
            if current.unit != param.unit:
                raise IRError(
                    f"{path}: unidad {param.unit!r} distinta de la del esquema "
                    f"{current.unit!r}"
                )
            setattr(obj, last, param)

    def walk(self) -> Iterator[tuple[str, Param]]:
        yield from _walk(self, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "params": {p: v.to_dict() for p, v in self.walk()},
            "resources": {"files_by_role": self.resources.files_by_role},
            "unknown": [u.__dict__ for u in self.unknown],
            "architecture": self.architecture,
        }


_ALLOWED_KEYS = {
    AxleSuspension: AXLES,
    TyreAxle: AXLES,
    BrakeAxle: AXLES,
    Differential: DIFF_ROLES,
}


def _child(obj: Any, name: str, create: bool) -> Any:
    if isinstance(obj, list):
        idx = int(name) - 1
        if not 0 <= idx < len(obj):
            raise IRError(f"índice {name} fuera de rango")
        return obj[idx]
    if isinstance(obj, dict):
        if name not in obj:
            raise IRError(f"clave {name!r} inexistente")
        return obj[name]
    if not hasattr(obj, name):
        raise IRError(f"{type(obj).__name__} no tiene {name!r}")
    value = getattr(obj, name)
    return value


def _walk(obj: Any, prefix: str) -> Iterator[tuple[str, Param]]:
    if isinstance(obj, Param):
        yield prefix, obj
    elif isinstance(obj, list):
        for i, v in enumerate(obj, 1):
            yield from _walk(v, f"{prefix}.{i}")
    elif isinstance(obj, dict):
        for k in sorted(obj):
            yield from _walk(obj[k], f"{prefix}.{k}" if prefix else k)
    elif is_dataclass(obj) and not isinstance(obj, UnknownItem):
        for f in fields(obj):
            if f.name in ("unknown", "architecture", "files_by_role"):
                continue
            yield from _walk(
                getattr(obj, f.name), f"{prefix}.{f.name}" if prefix else f.name
            )


def ensure_entry(container: dict, key: str, kind: type) -> Any:
    """Crea (si falta) la entrada de un eje o rol con su tipo; valida la clave."""
    allowed = _ALLOWED_KEYS[kind]
    if key not in allowed:
        raise IRError(f"{kind.__name__}: clave {key!r} no está en {allowed}")
    return container.setdefault(key, kind())
