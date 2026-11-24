"""Transmisión de la configuración activa (fase 4).

Fuentes y reglas (ver docs/research/transmision.md):

* Grafo: filas ``["type", "name", "inputName", "inputIndex", {opciones}]`` de las
  tablas ``powertrain`` de las piezas activas. [leído]
* Ruedas motrices: dispositivos con la opción ``connectedWheel`` (nombre de la
  rueda de ``pressureWheels``) alcanzables desde el motor. [leído]
* Parámetros de un dispositivo = opciones de su fila + sección con su mismo nombre
  (p. ej. ``frontDiff.gearRatio`` en la pieza de grupo final). Se combinan con
  ``merge_section`` (fase 3): claves únicas o iguales; cualquier conflicto entre
  fila y sección o entre piezas se RECHAZA (precedencia no verificada).
* ``gearRatios``: ``[R, 0 (punto muerto), 1.ª, 2.ª, …]``. [evidencia en la muestra:
  la sección ``engine`` comentada de la transmisión dice "-1, 0 (neutral), 1, etc"
  y las variables se titulan "Gear R", "Gear 1"…]
* Relación total rueda/motor = marcha · ``gearRatio`` del diferencial, con
  embrague y ejes a 1:1. [derivado; un eje con ``gearRatio`` propio, cajas
  reductoras, convertidores, etc. se rechazan: semántica no verificada]

* Cajas admitidas: ``manualGearbox`` y ``sequentialGearbox``. [C: en el Lua del juego
  ambas leen ``gearRatios`` igual (``manualGearbox.lua`` l. 549-575 =
  ``sequentialGearbox.lua`` l. 359-385: separan por signo, 0 = punto muerto) y
  transmiten igual el par (l. 55-57 de ambas). Lo que cambia es la forma de cambiar
  de marcha (secuencial, sin saltos), que no afecta a las relaciones; su equivalente
  en AC queda pendiente y se anota en ``issues``.]

* Tracción total con diferencial central [C, ``differential.lua``]: un
  ``differential`` común a los caminos de las cuatro ruedas, con otro diferencial por
  eje. La salida 1 recibe ``diffTorqueSplit`` (por defecto 0,5, l. 315-317) y la 2 el
  resto (l. 37-39, 64-65, 82-83); su ``gearRatio`` por defecto es 1 (l. 249). El
  grupo final de cada eje es el ``gearRatio`` de su diferencial; solo se acepta un
  ``final_drive`` si los dos coinciden (AC tiene un único ``FINAL``: loadINI). Ver
  :class:`AwdLayout`.

* G2.11 (exportador 200409 de Automation): ``dctGearbox`` (alimentada directamente por
  el motor, sin ``frictionClutch``: la caja lleva sus dos embragues) y
  ``automaticGearbox`` alimentada por un ``torqueConverter`` que cuelga del motor;
  opcionalmente un ``rangeBox`` justo detrás de la caja, solo si su relación inicial es
  1 (gama alta; rangeBox.lua l. 190). Las relaciones se leen igual que en
  ``manualGearbox`` (dctGearbox.lua l. 417-445, automaticGearbox.lua l. 420-449). La
  estructura se comprueba en :func:`check_transmission_structure`; los parámetros de la
  caja, del convertidor y del control los lee :mod:`src.extract.automatic_transmission`.
* 2026-11-16 (exportador 40340, bisexual_aspiration): el ``rangeBox`` también puede ir
  tras ``manualGearbox``/``sequentialGearbox`` (reductora del 4x4 de Automation), con la
  misma condición (relación inicial 1: AC no tiene gama baja; las demás se avisan).

Nada de esto ejecuta código de BeamNG: ``manualGearbox``, ``frictionClutch`` y
``differential`` son Lua del juego que NO está en la muestra.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.jbeam import ResolvedConfig, evaluate, parse_table
from src.jbeam.merge import merge_section

GEARBOX_TYPES = ("manualGearbox", "sequentialGearbox")
# G2.11: cajas de las exportaciones 200409 (src/extract/automatic_transmission.py)
AUTOMATIC_GEARBOX_TYPES = ("dctGearbox", "automaticGearbox")
# 2026-11-19 (exportador 40340, agus_1_nrt_1): caja de relación continua tras un
# convertidor de par (``GEARBOX_CVT`` de ProcessGearbox.lua); sin ``gearRatios``: lee
# ``minGearRatio``/``maxGearRatio`` (powertrain/cvtGearbox.lua l. 206-207, 120-124)
CVT_GEARBOX_TYPES = ("cvtGearbox",)
CONVERTER_TYPES = ("torqueConverter",)
RANGE_BOX_TYPES = ("rangeBox",)
SUPPORTED_ON_PATH = {
    "frictionClutch",
    "shaft",
    # torsionReactor.lua (0.38): eje sin fricción con relación fija (par x gearRatio,
    # velocidad / gearRatio, 1 por defecto); los nodos de reacción solo mueven el
    # chasis. Su gearRatio entra en el grupo final (``_reactor_ratio``).
    "torsionReactor",
    "differential",
    *GEARBOX_TYPES,
    *AUTOMATIC_GEARBOX_TYPES,
    *CVT_GEARBOX_TYPES,
    *CONVERTER_TYPES,
    *RANGE_BOX_TYPES,
}
RPM_TO_RAD_S = 2 * math.pi / 60


class DrivetrainDataError(ValueError):
    """Datos de transmisión no soportados, incompletos o ambiguos."""


@dataclass(frozen=True)
class Device:
    type: str
    name: str
    input_name: str | None
    input_index: Any
    part: str
    params: dict[str, Any]  # opciones de fila + sección, expresiones evaluadas


@dataclass(frozen=True)
class GearSpeed:
    gear: str  # "R", "1", "2"…
    ratio: float  # marcha (con signo leído)
    total_ratio: float  # |marcha| · grupo final
    speed_at_ceiling: float  # m/s al techo de rpm del motor
    speed_at_peak_power: float | None  # m/s al rpm de potencia máxima


@dataclass(frozen=True)
class AwdAxle:
    output: int  # salida del diferencial central (1 o 2)
    differential: Device  # diferencial del eje
    wheels: tuple[str, ...]  # ruedas (connectedWheel) de ese eje
    final: float  # gearRatio del diferencial del eje
    torque_share: float  # fracción del par del central (diffTorqueSplit / 1 − …)


@dataclass(frozen=True)
class AwdLayout:
    """Tracción total con diferencial central (ver el docstring del módulo)."""

    center: Device
    split_a: float  # diffTorqueSplit leído o 0,5 por defecto (differential.lua:315)
    split_source: str
    axles: tuple[AwdAxle, AwdAxle]  # ordenados por salida (1, 2)


@dataclass
class Drivetrain:
    devices: dict[str, Device]
    engine: str
    gearbox: Device
    differential: Device | None  # None en tracción total: ver ``awd``
    clutch: Device | None
    path: list[str]  # motor -> … -> rueda motriz (primera)
    driven_wheels: list[str]
    undriven_axles: list[str]  # dispositivos con connectedWheel sin camino al motor
    gear_ratios_raw: list[Any]
    reverse: float
    forward: list[float]
    final_drive: float
    status: dict[str, str] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    awd: AwdLayout | None = None
    # G2.11: convertidor de par delante de ``automaticGearbox`` y caja reductora detrás
    # de la caja (solo exportaciones 200409); ``None`` en el resto
    converter: Device | None = None
    range_box: Device | None = None
    # CVT: (relación mínima, relación máxima) de la caja de relación continua. Con CVT,
    # ``forward`` = [máxima, mínima] (extremos del rango, no marchas discretas),
    # ``reverse`` = −máxima y ``gear_ratios_raw`` = [].
    cvt_range: tuple[float, float] | None = None


def read_devices(resolved: ResolvedConfig) -> dict[str, Device]:
    variables = resolved.variable_values()
    devices: dict[str, Device] = {}
    for active in resolved.active:
        for row in parse_table(active.part.data.get("powertrain", [])).rows:
            name = row.get("name")
            dtype = row.get("type")
            if not isinstance(name, str) or not isinstance(dtype, str):
                raise DrivetrainDataError(
                    f"'{active.name}': fila de powertrain inválida"
                )
            if name in devices:
                raise DrivetrainDataError(
                    f"dispositivo '{name}' definido en '{devices[name].part}' y "
                    f"'{active.name}' (precedencia no verificada)"
                )
            params = dict(row.inline)
            if dtype.lower().endswith("engine"):
                # La sección del motor la trata la fase 3 (engine_camso); aquí
                # solo hace falta su posición en el grafo.
                devices[name] = Device(
                    dtype,
                    name,
                    row.get("inputName"),
                    row.get("inputIndex"),
                    active.name,
                    {k: evaluate(v, variables) for k, v in params.items()},
                )
                continue
            section = merge_section(resolved, name)
            if section.conflicts or section.operators or section.non_dict:
                raise DrivetrainDataError(
                    f"sección '{name}' con conflictos u operadores entre piezas"
                )
            for key, value in section.values.items():
                if key in params and params[key] != value:
                    raise DrivetrainDataError(
                        f"'{name}.{key}': fila ({params[key]!r}) y sección "
                        f"({value!r}) difieren (precedencia no verificada)"
                    )
                params[key] = value
            params = {k: _evaluate_deep(v, variables) for k, v in params.items()}
            devices[name] = Device(
                dtype,
                name,
                row.get("inputName"),
                row.get("inputIndex"),
                active.name,
                params,
            )
    return devices


def _evaluate_deep(value: Any, variables) -> Any:
    if isinstance(value, list):
        return [_evaluate_deep(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _evaluate_deep(v, variables) for k, v in value.items()}
    return evaluate(value, variables)


def _path_to_engine(devices: dict[str, Device], name: str) -> list[str] | None:
    """Camino rueda -> … -> motor siguiendo inputName; None si se corta."""
    path, seen = [name], {name}
    current = devices[name]
    while current.input_name in devices:
        if current.input_name in seen:
            raise DrivetrainDataError(f"ciclo en el grafo de powertrain en '{name}'")
        seen.add(current.input_name)
        path.append(current.input_name)
        current = devices[current.input_name]
    if not current.type.lower().endswith("engine"):
        return None
    return path


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DrivetrainDataError(f"{what}: se esperaba un número, no {value!r}")
    if not math.isfinite(value):
        raise DrivetrainDataError(f"{what}: valor no finito")
    return float(value)


def _split_gears(raw: Any) -> tuple[float, list[float]]:
    if not isinstance(raw, list) or len(raw) < 3:
        raise DrivetrainDataError(f"gearRatios inesperado: {raw!r}")
    ratios = [_number(v, "gearRatios") for v in raw]
    zeros = [i for i, v in enumerate(ratios) if v == 0]
    if zeros != [1]:
        raise DrivetrainDataError(
            f"gearRatios {ratios}: se esperaba [R, 0, 1.ª, …] con el punto muerto en "
            "la posición 1"
        )
    reverse, forward = ratios[0], ratios[2:]
    if reverse >= 0:
        raise DrivetrainDataError(f"marcha atrás no negativa: {reverse}")
    if not forward or any(v <= 0 for v in forward):
        raise DrivetrainDataError(f"marchas hacia delante no positivas: {forward}")
    return reverse, forward


def _reactor_ratio(devices: dict[str, Device], path: list[str]) -> float:
    """Producto de los ``gearRatio`` (1 por defecto) de los ``torsionReactor`` del
    camino: relación fija entre el motor y el diferencial que se suma al grupo final
    (``torsionReactor.lua``: inputAV = outputAV1·gearRatio)."""
    ratio = 1.0
    for name in path:
        dev = devices[name]
        if dev.type == "torsionReactor":
            ratio *= _number(dev.params.get("gearRatio", 1), f"{name}.gearRatio")
    return ratio


def build_drivetrain(resolved: ResolvedConfig) -> Drivetrain:
    devices = read_devices(resolved)
    issues: list[str] = []
    engines = [d for d in devices.values() if d.type.lower().endswith("engine")]
    if len(engines) != 1:
        raise DrivetrainDataError(
            f"se esperaba un único motor, hay {[e.name for e in engines]}"
        )
    engine = engines[0]

    wheel_axles = [d for d in devices.values() if "connectedWheel" in d.params]
    driven: dict[str, list[str]] = {}
    undriven: list[str] = []
    for axle in wheel_axles:
        path = _path_to_engine(devices, axle.name)
        if path is None:
            undriven.append(axle.name)
            missing = devices[axle.name].input_name
            issues.append(
                f"'{axle.name}' ({axle.params['connectedWheel']}) no llega al motor "
                f"(su entrada '{missing}' no existe en la config): rueda no motriz"
            )
        else:
            driven[axle.params["connectedWheel"]] = list(reversed(path))
    if not driven:
        raise DrivetrainDataError("ninguna rueda está conectada al motor")

    paths = list(driven.values())
    for path in paths:
        for name in path[1:]:
            dev = devices[name]
            if dev.type not in SUPPORTED_ON_PATH:
                raise DrivetrainDataError(
                    f"dispositivo '{name}' de tipo '{dev.type}' en el camino motriz: "
                    "no soportado"
                )
            if dev.type in ("shaft", "frictionClutch") and "gearRatio" in dev.params:
                raise DrivetrainDataError(
                    f"'{name}' ({dev.type}) tiene gearRatio propio: "
                    "semántica no verificada"
                )

    def on_paths(dtype: str) -> list[Device]:
        names = {n for p in paths for n in p}
        return [devices[n] for n in sorted(names) if devices[n].type == dtype]

    all_gearbox_types = GEARBOX_TYPES + AUTOMATIC_GEARBOX_TYPES + CVT_GEARBOX_TYPES
    gearboxes = [g for t in all_gearbox_types for g in on_paths(t)]
    diffs = on_paths("differential")
    clutches = on_paths("frictionClutch")
    if len(gearboxes) != 1:
        raise DrivetrainDataError(
            "se esperaba una caja "
            f"({'/'.join(all_gearbox_types)}), hay "
            f"{[g.name for g in gearboxes]}"
        )
    gearbox = gearboxes[0]
    converter, range_box, g211_issues = check_transmission_structure(
        engine,
        gearbox,
        clutches,
        [d for t in CONVERTER_TYPES for d in on_paths(t)],
        [d for t in RANGE_BOX_TYPES for d in on_paths(t)],
    )
    issues += g211_issues
    awd: AwdLayout | None = None
    if len(diffs) == 3:
        awd = _awd_layout(devices, driven, diffs)
        diff = None
    elif len(diffs) != 1:
        raise DrivetrainDataError(
            "se esperaba un diferencial en el camino motriz (o tres: central y uno "
            f"por eje), hay {[d.name for d in diffs]}"
        )
    else:
        diff = diffs[0]
    if gearbox.type in GEARBOX_TYPES and gearbox.type != "manualGearbox":
        issues.append(
            f"caja '{gearbox.type}': relaciones y par como manualGearbox (Lua [C]); "
            "modo y tiempos de cambio sin equivalencia verificada en AC"
        )
    cvt_range: tuple[float, float] | None = None
    if gearbox.type in CVT_GEARBOX_TYPES:
        cvt_range = _cvt_range(gearbox)
        reverse, forward = -cvt_range[1], [cvt_range[1], cvt_range[0]]
        raw_ratios: list[Any] = []
    else:
        if "gearRatios" not in gearbox.params:
            raise DrivetrainDataError(f"'{gearbox.name}' sin gearRatios")
        reverse, forward = _split_gears(gearbox.params["gearRatios"])
        if any(a <= b for a, b in zip(forward, forward[1:])):
            issues.append(f"marchas no estrictamente decrecientes: {forward}")
        raw_ratios = list(gearbox.params["gearRatios"])
    if awd is not None:
        finals = {a.final for a in awd.axles}
        if len(finals) != 1:
            raise DrivetrainDataError(
                "tracción total con grupos finales distintos por eje "
                f"{[(a.differential.name, a.final) for a in awd.axles]}: AC solo tiene "
                "un FINAL (transmisión de AC); no representable sin una decisión"
            )
        final = finals.pop()
    else:
        assert diff is not None
        if "gearRatio" not in diff.params:
            raise DrivetrainDataError(f"'{diff.name}' sin gearRatio (grupo final)")
        final = _number(diff.params["gearRatio"], f"{diff.name}.gearRatio")
        reactors = {_reactor_ratio(devices, p) for p in paths if diff.name in p}
        if len(reactors) != 1:
            raise DrivetrainDataError(
                f"torsionReactor con relaciones distintas hacia '{diff.name}': "
                f"{sorted(reactors)}"
            )
        final *= reactors.pop()
    if final <= 0:
        raise DrivetrainDataError(f"grupo final no positivo: {final}")

    status = {
        "graph_and_driven_wheels": "leído (powertrain + connectedWheel)",
        "gear_layout": "confirmado en la muestra ([R, 0, 1.ª…]: comentario y títulos)",
        "gearbox_type": f"{gearbox.type} (relaciones: Lua del juego, ver docstring)",
        "final_drive": (
            "leído (gearRatio igual en los dos diferenciales de eje; central 1:1)"
            if awd
            else "leído (gearRatio del diferencial × el de los torsionReactor del camino)"
        ),
        "total_ratio": "derivado (marcha · final; embrague y ejes 1:1)",
        "rolling_radius": "hipótesis (radio de nodos de pressureWheels, sin carga)",
        "device_params_merge": "fila + sección sin conflictos (fase 3, conservador)",
    }
    return Drivetrain(
        devices=devices,
        engine=engine.name,
        gearbox=gearbox,
        differential=diff,
        clutch=clutches[0] if len(clutches) == 1 else None,
        path=paths[0],
        driven_wheels=sorted(driven),
        undriven_axles=sorted(undriven),
        gear_ratios_raw=raw_ratios,
        reverse=reverse,
        forward=forward,
        final_drive=final,
        status=status,
        issues=issues,
        awd=awd,
        converter=converter,
        range_box=range_box,
        cvt_range=cvt_range,
    )


def _cvt_range(gearbox: Device) -> tuple[float, float]:
    """``(minGearRatio, maxGearRatio)`` de una ``cvtGearbox`` (ya evaluadas las
    variables ``$gear_min``/``$gear_max``). Sin valor por defecto: el del juego (0,5 y
    2,5; cvtGearbox.lua l. 206-207) no describe una exportación de Automation, que
    siempre los declara; su ausencia se rechaza en vez de suponerla."""
    lo_raw = gearbox.params.get("minGearRatio")
    hi_raw = gearbox.params.get("maxGearRatio")
    if lo_raw is None or hi_raw is None:
        raise DrivetrainDataError(
            f"cvtGearbox '{gearbox.name}' sin minGearRatio/maxGearRatio"
        )
    lo = _number(lo_raw, f"{gearbox.name}.minGearRatio")
    hi = _number(hi_raw, f"{gearbox.name}.maxGearRatio")
    if not 0 < lo <= hi:
        raise DrivetrainDataError(
            f"cvtGearbox '{gearbox.name}': se esperaba 0 < minGearRatio ≤ "
            f"maxGearRatio, hay {lo} y {hi}"
        )
    return lo, hi


def check_transmission_structure(
    engine: Device,
    gearbox: Device,
    clutches: list[Device],
    converters: list[Device],
    range_boxes: list[Device],
) -> tuple[Device | None, Device | None, list[str]]:
    """Estructura de la caja en el camino motriz (G2.11).

    Devuelve ``(convertidor, caja reductora, avisos)``. Con ``manualGearbox`` o
    ``sequentialGearbox`` no cambia nada respecto a antes (sin avisos), salvo que un
    convertidor en el camino se rechaza (combinación no vista); una caja reductora se
    comprueba como con las demás cajas (2026-11-16).
    Errores (``DrivetrainDataError``), nunca supuestos:

    * ``dctGearbox``: su entrada debe ser el motor (dctGearbox.lua l. 173-178) y no
      puede haber ``frictionClutch`` ni convertidor en el camino;
    * ``automaticGearbox``: exactamente un ``torqueConverter`` con el motor como entrada
      (torqueConverter.lua l. 81-86) y la caja colgando de él; sin ``frictionClutch``;
    * ``rangeBox``: como mucho uno, colgando de la caja, con relaciones ≥ 0 y la
      inicial (índice 0, rangeBox.lua l. 155-190) igual a 1: otra relación cambiaría
      la relación total y no se compone con ``FINAL`` sin una decisión.
    """
    plain = gearbox.type in GEARBOX_TYPES
    if plain:
        if converters:
            raise DrivetrainDataError(
                f"caja '{gearbox.type}' con "
                f"{[d.type for d in converters]} en el camino motriz: "
                "combinación no vista en Automation"
            )
        if not range_boxes:
            return None, None, []
        # manual/secuencial + rangeBox (reductora del 4x4 de Automation, muestra
        # bisexual_aspiration): misma comprobación de la gama que con las demás cajas.
    issues: list[str] = []
    if len(converters) > 1 or len(range_boxes) > 1:
        raise DrivetrainDataError(
            "varios convertidores o cajas reductoras en el camino motriz "
            f"{[d.name for d in converters + range_boxes]}: estructura ambigua"
        )
    if clutches and not plain:
        raise DrivetrainDataError(
            f"'{gearbox.type}' con frictionClutch {[c.name for c in clutches]} en el "
            "camino: la caja ya tiene sus embragues o su convertidor"
        )
    converter: Device | None = None
    if plain:
        pass
    elif gearbox.type == "dctGearbox":
        if converters:
            raise DrivetrainDataError(
                f"dctGearbox con torqueConverter '{converters[0].name}': no visto"
            )
        if gearbox.input_name != engine.name:
            raise DrivetrainDataError(
                f"dctGearbox '{gearbox.name}' alimentada por '{gearbox.input_name}', "
                f"no por el motor '{engine.name}' (dctGearbox.lua l. 173-178)"
            )
        issues.append(
            f"caja dctGearbox '{gearbox.name}': relaciones como manualGearbox "
            "(dctGearbox.lua l. 417-445); par rígido con un embrague cerrado "
            "(l. 74-79). Traspaso entre embragues, modos automáticos y capacidad de "
            "los embragues sin equivalente verificado en AC"
        )
    else:  # automaticGearbox o cvtGearbox
        if len(converters) != 1:
            raise DrivetrainDataError(
                f"{gearbox.type} '{gearbox.name}' sin torqueConverter en el camino: "
                "no visto en Automation"
            )
        converter = converters[0]
        if converter.input_name != engine.name or gearbox.input_name != converter.name:
            raise DrivetrainDataError(
                f"orden no reconocido: se esperaba motor -> '{converter.name}' -> "
                f"'{gearbox.name}', hay '{converter.input_name}' -> '{converter.name}' "
                f"y '{gearbox.input_name}' -> '{gearbox.name}' "
                "(torqueConverter.lua l. 81-86)"
            )
        if gearbox.type == "cvtGearbox":
            issues.append(
                f"convertidor de par '{converter.name}' delante de cvtGearbox: sin "
                "equivalente en AC (AC no tiene convertidor; G2.11). La relación "
                "de la caja es continua entre minGearRatio y maxGearRatio "
                "(cvtGearbox.lua l. 120-124): AC no tiene CVT, solo una marcha fija "
                "(la variación exige un script de CSP; ver cvt_generalizacion_2026-11-19)"
            )
        else:
            issues.append(
                f"convertidor de par '{converter.name}' delante de automaticGearbox: "
                "sin equivalente en AC (AC no tiene convertidor; G2.11). "
                "Relaciones de la caja como manualGearbox (automaticGearbox.lua "
                "l. 420-449)"
            )
    range_box: Device | None = None
    if range_boxes:
        range_box = range_boxes[0]
        if range_box.input_name != gearbox.name:
            raise DrivetrainDataError(
                f"rangeBox '{range_box.name}' alimentada por '{range_box.input_name}', "
                f"no por la caja '{gearbox.name}': estructura no vista"
            )
        raw = range_box.params.get("gearRatios")
        if not isinstance(raw, list) or not raw:
            raise DrivetrainDataError(f"rangeBox '{range_box.name}' sin gearRatios")
        ratios = [_number(v, f"{range_box.name}.gearRatios") for v in raw]
        if any(v < 0 for v in ratios):
            raise DrivetrainDataError(
                f"rangeBox '{range_box.name}' con relaciones negativas {ratios}"
            )
        if ratios[0] != 1:
            raise DrivetrainDataError(
                f"rangeBox '{range_box.name}': relación inicial {ratios[0]} "
                "distinta de 1 "
                "(rangeBox.lua l. 190); la relación total no se compone con FINAL sin "
                "una decisión"
            )
        issues.append(
            f"caja reductora '{range_box.name}': gama inicial 1:1 (rangeBox.lua "
            f"l. 190); las otras gamas {ratios[1:]} no tienen equivalente en AC"
        )
    return converter, range_box, issues


def _awd_layout(
    devices: dict[str, Device], driven: dict[str, list[str]], diffs: list[Device]
) -> AwdLayout:
    """Central = el diferencial presente en los caminos de todas las ruedas motrices;
    los otros dos deben repartirse las ruedas por ejes disjuntos."""
    names = {d.name for d in diffs}
    common = [n for n in names if all(n in path for path in driven.values())]
    if len(common) != 1:
        raise DrivetrainDataError(
            f"tres diferenciales {sorted(names)} sin un único central común a todas "
            f"las ruedas motrices ({common})"
        )
    center = devices[common[0]]
    if "gearRatio" in center.params and center.params["gearRatio"] != 1:
        raise DrivetrainDataError(
            f"diferencial central '{center.name}' con gearRatio "
            f"{center.params['gearRatio']}: semántica no verificada"
        )
    raw_split = center.params.get("diffTorqueSplit")
    split_a = 0.5 if raw_split is None else _number(raw_split, "diffTorqueSplit")
    if not 0.0 <= split_a <= 1.0:
        raise DrivetrainDataError(f"diffTorqueSplit fuera de [0, 1]: {split_a}")
    axles: dict[int, AwdAxle] = {}
    for d in (devices[n] for n in sorted(names - {center.name})):
        wheels = tuple(sorted(w for w, p in driven.items() if d.name in p))
        paths = [driven[w] for w in wheels]
        # dispositivo inmediatamente posterior al central en el camino del eje
        nxt = {p[p.index(center.name) + 1] for p in paths}
        if len(wheels) != 2 or len(nxt) != 1:
            raise DrivetrainDataError(
                f"diferencial de eje '{d.name}': ruedas {wheels}, tras el central "
                f"{sorted(nxt)}; estructura AWD no reconocida"
            )
        output = devices[nxt.pop()].input_index
        if output not in (1, 2) or output in axles:
            raise DrivetrainDataError(
                f"salida {output!r} del central para '{d.name}': no es 1 ni 2 o está "
                "repetida"
            )
        if "gearRatio" not in d.params:
            raise DrivetrainDataError(f"'{d.name}' sin gearRatio (grupo final)")
        reactors = {_reactor_ratio(devices, p) for p in paths}
        if len(reactors) != 1:
            raise DrivetrainDataError(
                f"torsionReactor con relaciones distintas hacia '{d.name}': "
                f"{sorted(reactors)}"
            )
        axles[output] = AwdAxle(
            output,
            d,
            wheels,
            _number(d.params["gearRatio"], f"{d.name}.gearRatio") * reactors.pop(),
            split_a if output == 1 else 1.0 - split_a,
        )
    if set(axles) != {1, 2}:
        raise DrivetrainDataError(f"salidas del central sin eje: {sorted(axles)}")
    return AwdLayout(
        center,
        split_a,
        "diffTorqueSplit" if raw_split is not None else "por defecto 0,5 (Lua l. 315)",
        (axles[1], axles[2]),
    )


def traction_type(driven: list[str], front: set[str], rear: set[str]) -> str:
    """FWD / RWD / AWD según qué ejes (fase 2) tienen ruedas motrices."""
    f = any(w in front for w in driven)
    r = any(w in rear for w in driven)
    unknown = [w for w in driven if w not in front and w not in rear]
    if unknown:
        raise DrivetrainDataError(f"ruedas motrices sin eje conocido: {unknown}")
    return "AWD" if f and r else "FWD" if f else "RWD"


def gear_speeds(
    dt: Drivetrain, radius: float, rpm_ceiling: float, peak_power_rpm: float | None
) -> list[GearSpeed]:
    """v = ω_motor / (|marcha| · final) · r   (sin deslizamiento ni deformación)."""
    out = []
    labelled = [("R", dt.reverse)] + [(str(i + 1), g) for i, g in enumerate(dt.forward)]
    for label, ratio in labelled:
        total = abs(ratio) * dt.final_drive
        at_ceiling = rpm_ceiling * RPM_TO_RAD_S / total * radius
        at_peak = (
            None
            if peak_power_rpm is None
            else peak_power_rpm * RPM_TO_RAD_S / total * radius
        )
        out.append(GearSpeed(label, ratio, total, at_ceiling, at_peak))
    return out
