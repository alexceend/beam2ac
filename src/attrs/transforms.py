"""Transformaciones explícitas origen → destino, con comprobación de unidades.

Cada transformación recibe ``{nombre: (valor, unidad)}``, los ``params`` de la regla y
la unidad de salida. Lanza :class:`TransformError` si las unidades no son compatibles o
falta algo. Ninguna transformación «adivina» una unidad: ``assume_unit`` existe
precisamente para declarar una reinterpretación como SUPUESTO (el motor la añade a los
supuestos de la regla).

Fórmulas (``Transform.id``):

* ``identity``      y = x                         (x convertible a la unidad de salida)
* ``constant``      y = params.value              (sin entradas)
* ``assume_unit``   y = x, leyendo x como ``params.as_unit`` aunque se declare
``params.declared``
* ``linear_sum``    y = Σ cᵢ·xᵢ                   (``params.coefficients``; misma
dimensión)
* ``scale``         y = params.factor · x
* ``table``         tabla [(x, y)] pasada tal cual (unidades de ambos ejes compatibles)
* ``ac_coast_ref``  ``[COAST_REF]`` de AC desde la curva de freno motor de origen
  (``curve``, ``table:rpm->N·m``) y el techo de rpm (``ceiling``):
  ``src.ac.coast_ref.coast_ref_linear``; ``params.output`` ∈ {``rpm``, ``torque``,
  ``non_linearity``}, ``params.round`` opcional (decimales). Sin puntos ≤ techo:
  error. Devuelve como supuesto dependiente de los valores el error máximo del ajuste.
* ``text_contains_map``  texto → valor por subcadenas: ``params.pairs`` =
  ``[[aguja, valor], …]`` en orden; gana el PRIMER par cuya aguja está en el texto
  (sin distinguir mayúsculas: ``str.lower`` en los dos lados). Sin coincidencia, texto
  vacío o no textual: error. Entrada ``text``; la tabla vive en la regla (catálogo).
* ``ac_body_cg``    ``CG_LOCATION``/``BASEY`` del cuerpo de AC sin piloto (G2.18;
  fórmula en :func:`t_ac_body_cg`).
* ``ac_axle_point`` centro de un eje (0, BASEY, ±z) en el marco del cuerpo de AC (G2.19;
  :func:`t_ac_axle_point`).
* ``ac_wing_table`` tabla C·A (m²) → LUT de coeficiente de un ala de área CHORD·SPAN
  (G2.19; :func:`t_ac_wing_table`).
* ``ac_strut_axis`` magnitud en rueda → a lo largo del eje del puntal de AC
  (rigidez ÷ s², recorrido o velocidad × s; G2.19; :func:`t_ac_strut_axis`).

Supuestos dependientes de los valores (G2.7, decisión D-G26a): una transformación
puede devolver :class:`Detailed` con ``assumptions`` calculadas a partir de sus
entradas (p. ej. el error de un ajuste). :func:`apply_detailed` las devuelve; el motor
las añade a los supuestos de la regla, después de los declarados y de los de
:func:`implied_assumptions`.
:func:`apply` sigue devolviendo solo el valor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import units


class TransformError(ValueError):
    """Entradas o unidades incompatibles con la transformación."""


@dataclass(frozen=True)
class Detailed:
    """Valor de una transformación con supuestos que dependen de los valores."""

    value: Any
    assumptions: tuple[str, ...] = ()


Inputs = dict[str, tuple[Any, str]]


def _one(inputs: Inputs, name: str = "x") -> tuple[Any, str]:
    if name not in inputs:
        raise TransformError(f"falta la entrada '{name}'")
    return inputs[name]


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TransformError(f"{what}: se esperaba un número, no {value!r}")
    return value


def _convert(value: Any, src: str, dst: str, what: str) -> Any:
    if src in ("text", "enum") or dst in ("text", "enum"):
        if src != dst:
            raise TransformError(f"{what}: unidades incompatibles {src!r} -> {dst!r}")
        return value
    if not units.compatible(src, dst):
        raise TransformError(f"{what}: unidades incompatibles {src!r} -> {dst!r}")
    return units.convert(_num(value, what), src, dst)


def t_identity(inputs: Inputs, params: dict, out_unit: str) -> Any:
    value, unit = _one(inputs)
    if units.is_table(unit) or units.is_table(out_unit):
        return t_table(inputs, params, out_unit)
    return _convert(value, unit, out_unit, "identity")


def t_constant(inputs: Inputs, params: dict, out_unit: str) -> Any:
    if inputs:
        raise TransformError("constant no admite entradas")
    if "value" not in params:
        raise TransformError("constant necesita params.value")
    return params["value"]


def t_assume_unit(inputs: Inputs, params: dict, out_unit: str) -> Any:
    value, unit = _one(inputs)
    declared, as_unit = params.get("declared"), params.get("as_unit")
    if unit != declared:
        raise TransformError(
            f"assume_unit: la entrada está en {unit!r}, no en {declared!r}"
        )
    if not as_unit:
        raise TransformError("assume_unit necesita params.as_unit")
    return _convert(value, as_unit, out_unit, "assume_unit")


def t_linear_sum(inputs: Inputs, params: dict, out_unit: str) -> Any:
    coeffs = params.get("coefficients")
    if not isinstance(coeffs, dict) or not coeffs:
        raise TransformError("linear_sum necesita params.coefficients")
    if set(coeffs) != set(inputs):
        raise TransformError(
            f"linear_sum: coeficientes {sorted(coeffs)} ≠ entradas {sorted(inputs)}"
        )
    total = 0.0
    for name, c in coeffs.items():
        value, unit = inputs[name]
        total += _num(c, f"coeficiente {name}") * _convert(value, unit, out_unit, name)
    return total


def t_scale(inputs: Inputs, params: dict, out_unit: str) -> Any:
    value, unit = _one(inputs)
    factor = _num(params.get("factor"), "params.factor")
    return factor * _convert(value, unit, out_unit, "scale")


def t_scale_product(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """``y = factor · base · Π(otras)``: la entrada ``params.base`` lleva la unidad (se
    convierte a la de salida) y las demás deben ser adimensionales (``1``). G2.21:
    precarga
    del central de AC en el lado de la rueda = 2·lsdPreload·grupo final."""
    factor = _num(params.get("factor"), "params.factor")
    base = params.get("base")
    if base not in inputs:
        raise TransformError(f"scale_product: params.base {base!r} no es una entrada")
    value, unit = inputs[base]
    total = factor * _convert(value, unit, out_unit, "scale_product")
    for name, (v, u) in inputs.items():
        if name == base:
            continue
        if u != "1":
            raise TransformError(
                f"scale_product: {name} debe ser adimensional (es {u!r})"
            )
        total *= _num(v, name)
    return total


AC_PHYSICS_DT = 1.0 / 333.0  # s; paso de la física de AC [I] (medido: phys_ms, G2.22)


def t_ac_center_lock_cap(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Tope de ``CENTRE_DIFF_PRELOAD`` que imita un central bloqueado (G2.44).

    ``transmisión 4WD de AC`` [C]: cada paso corrige Δω en ``dt·lim·sat(Δω)/ΣI`` con
    ``sat(x) = x/(|x|+0,1)``, así que con un límite grande el central oscila dentro de la
    ventana con amplitud pico-pico ≈ ``dt·lim/ΣI`` (rad/s). Se fija la amplitud admitida
    ``params.chatter`` (rad/s en rueda) ⇒ ``lim = chatter·ΣI/dt``, con ``ΣI = 2·(I_del +
    I_tras)`` (``loadINI`` iguala la inercia de las 4 ruedas a la media)."""
    chatter = _num(params.get("chatter"), "params.chatter")
    dt = _num(params.get("dt", AC_PHYSICS_DT), "params.dt")
    if chatter <= 0 or dt <= 0:
        raise TransformError("ac_center_lock_cap: chatter y dt deben ser > 0")
    total = 0.0
    for name in ("i_front", "i_rear"):
        if name not in inputs:
            raise TransformError(f"ac_center_lock_cap: falta la entrada {name!r}")
        value, unit = inputs[name]
        total += 2.0 * _convert(value, unit, "kg·m²", name)
    return chatter * total / dt


def t_table(inputs: Inputs, params: dict, out_unit: str) -> Any:
    value, unit = _one(inputs)
    if not (units.is_table(unit) and units.is_table(out_unit)):
        raise TransformError(f"table: se esperaban tablas ({unit!r} -> {out_unit!r})")
    (sx, sy), (dx, dy) = units.table_axes(unit), units.table_axes(out_unit)
    if not (units.compatible(sx, dx) and units.compatible(sy, dy)):
        raise TransformError(f"table: ejes incompatibles {unit!r} -> {out_unit!r}")
    return [
        [units.convert(_num(x, "x"), sx, dx), units.convert(_num(y, "y"), sy, dy)]
        for x, y in value
    ]


_COAST_OUTPUTS = {"rpm": "rpm", "torque": "N·m", "non_linearity": "1"}


def _fmt_num(x: float) -> str:
    return f"{int(x)}" if float(x).is_integer() else f"{x:g}"


def t_ac_coast_ref(inputs: Inputs, params: dict, out_unit: str) -> Detailed:
    """``[COAST_REF]`` lineal desde 0 anclado en el techo (concepto de AC).

    La fórmula está en ``src.ac.coast_ref`` (compartida con el borrador de
    ``engine.ini``); aquí solo se comprueban unidades y se elige la salida.
    """
    from src.ac.coast_ref import coast_ref_linear

    curve, cu = _one(inputs, "curve")
    ceiling, ceu = _one(inputs, "ceiling")
    output = params.get("output")
    if output not in _COAST_OUTPUTS:
        raise TransformError(f"params.output debe ser uno de {sorted(_COAST_OUTPUTS)}")
    if not units.is_table(cu):
        raise TransformError(f"'curve' debe ser una tabla, no {cu!r}")
    sx, sy = units.table_axes(cu)
    if not (units.compatible(sx, "rpm") and units.compatible(sy, "N·m")):
        raise TransformError(f"ejes de 'curve' incompatibles: {cu!r}")
    if not isinstance(curve, (list, tuple)) or any(
        not isinstance(p, (list, tuple)) or len(p) != 2 for p in curve
    ):
        raise TransformError("'curve' debe ser una lista de pares")
    points = [
        (
            units.convert(_num(r, "curve rpm"), sx, "rpm"),
            units.convert(_num(t, "curve N·m"), sy, "N·m"),
        )
        for r, t in curve
    ]
    ceil_rpm = _convert(ceiling, ceu, "rpm", "techo")
    law = params.get("law", "linear_from_zero")
    if law == "acs_from_minimum":
        # G2.24: error con la ley real de AC (lineal desde MINIMUM, 0 por debajo);
        # MINIMUM se lee con getInt (std::stoi): se trunca como en AC.
        from src.ac.coast_ref import coast_ref_from_minimum

        minimum, mu = _one(inputs, "minimum")
        min_rpm = float(int(_convert(minimum, mu, "rpm", "minimum")))
        fit = coast_ref_from_minimum(points, ceil_rpm, min_rpm)
        law_txt = f"lineal desde MINIMUM = {_fmt_num(min_rpm)} rpm (AC)"
    elif law == "acs_least_squares":
        # G2.31: pendiente por mínimos cuadrados (ley de AC: lineal desde MINIMUM)
        from src.ac.coast_ref import coast_ref_least_squares

        minimum, mu = _one(inputs, "minimum")
        min_rpm = float(int(_convert(minimum, mu, "rpm", "minimum")))
        fit = coast_ref_least_squares(points, ceil_rpm, min_rpm)
        law_txt = (
            f"pendiente por mínimos cuadrados, lineal desde MINIMUM = "
            f"{_fmt_num(min_rpm)} rpm (AC)"
        )
    elif law == "linear_from_zero":
        fit = coast_ref_linear(points, ceil_rpm)
        law_txt = "lineal desde 0"
    else:
        raise TransformError(f"params.law desconocida: {law!r}")
    if fit is None:
        raise TransformError(
            f"ningún punto de la curva por debajo del techo "
            f"({_fmt_num(ceil_rpm)} rpm)"
            + (
                " por encima de MINIMUM"
                if law in ("acs_from_minimum", "acs_least_squares")
                else ""
            )
        )
    value = {"rpm": fit.rpm, "torque": fit.torque, "non_linearity": fit.non_linearity}[
        output
    ]
    digits = params.get("round")
    if digits is not None:
        if isinstance(digits, bool) or not isinstance(digits, int):
            raise TransformError("params.round debe ser un entero")
        value = round(value, digits)
    n = sum(1 for r, _t in points if r <= ceil_rpm)
    note = (
        f"ajuste COAST_REF {law_txt}: error máx. {fit.max_abs_error:.1f} N·m a "
        f"{_fmt_num(fit.worst_rpm)} rpm frente a la curva de origen ({n} puntos ≤ "
        f"{_fmt_num(ceil_rpm)} rpm)"
        + (f"; error cuadrático medio {fit.rms_error:.1f} N·m" if fit.rms_error else "")
    )
    return Detailed(
        _convert(value, _COAST_OUTPUTS[output], out_unit, "salida"), (note,)
    )


def t_text_contains_map(inputs: Inputs, params: dict, out_unit: str) -> Any:
    value, unit = _one(inputs, "text")
    if unit != "text":
        raise TransformError(f"la entrada debe ser 'text', no {unit!r}")
    if out_unit not in ("enum", "text"):
        raise TransformError(f"salida 'enum' o 'text', no {out_unit!r}")
    pairs = params.get("pairs")
    if (
        not isinstance(pairs, list)
        or not pairs
        or any(
            not isinstance(p, (list, tuple))
            or len(p) != 2
            or not isinstance(p[0], str)
            or not p[0]
            for p in pairs
        )
    ):
        raise TransformError(
            "text_contains_map necesita params.pairs = [[aguja, valor], …] con agujas "
            "de texto no vacías"
        )
    if not isinstance(value, str) or not value:
        raise TransformError(f"texto vacío o no textual {value!r}")
    low = value.lower()
    for needle, result in pairs:
        if needle.lower() in low:
            return result
    raise TransformError(f"sin correspondencia para {value!r}")


def t_vector_identity(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Vector de números (p. ej. un punto en m) sin cambio de significado (G2.16).

    ``params.length``: número de componentes exigido. Cada componente se convierte de la
    unidad de la entrada a la de salida como en ``identity``."""
    value, unit = _one(inputs)
    n = params.get("length")
    if not isinstance(value, (list, tuple)):
        raise TransformError(f"vector_identity: se esperaba un vector, no {value!r}")
    if not isinstance(n, int) or isinstance(n, bool) or len(value) != n:
        raise TransformError(
            f"vector_identity: se esperaban {n!r} componentes, hay {len(value)}"
        )
    return [_convert(v, unit, out_unit, "vector_identity") for v in value]


def t_as_undocumented(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Número hacia un atributo de unidad ``undocumented`` (G2.16).

    La unidad ``undocumented`` nunca es compatible con otra (obliga a decidir). Esta
    transformación es esa decisión explícita: exige que la entrada esté en
    ``params.unit`` y que ``params.semantics`` cite la semántica verificada del
    destino; el valor no cambia."""
    value, unit = _one(inputs)
    if out_unit != "undocumented":
        raise TransformError("as_undocumented: solo hacia la unidad 'undocumented'")
    if params.get("unit") != unit:
        raise TransformError(
            f"as_undocumented: la entrada está en {unit!r}, "
            f"no en {params.get('unit')!r}"
        )
    if not str(params.get("semantics") or "").strip():
        raise TransformError("as_undocumented necesita params.semantics")
    return _num(value, "as_undocumented")


def t_ac_toe_out(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """``TOE_OUT`` de AC (m) desde la convergencia en reposo y la sensibilidad
    de la barra (G2.17).

    AC [C] (suspensión de AC/el desplazamiento de la barra de dirección, AC):
    ``TOE_OUT`` se guarda tal cual (``toeOUT_Linear``) y desplaza el punto del chasis
    de la barra (``WBCAR_STEER``) en X ``signo(x de la rueda)·TOE_OUT``, es decir,
    HACIA FUERA con valores positivos; el buje se monta alineado con la carrocería
    (suspensión de AC): sin ``TOE_OUT`` la convergencia de AC es 0. Entradas:
    ``toe`` (grados, + = toe-in) y ``shift`` (m por grado de toe-in, + = el punto del
    chasis va hacia DENTRO). Primer orden: hacia dentro = toe·shift ⇒
    ``TOE_OUT`` = −toe·shift."""
    if set(inputs) != {"toe", "shift"}:
        raise TransformError("ac_toe_out necesita las entradas 'toe' y 'shift'")
    toe = _convert(inputs["toe"][0], inputs["toe"][1], "deg", "toe")
    shift = _convert(inputs["shift"][0], inputs["shift"][1], "m/deg", "shift")
    return _convert(-toe * shift, "m", out_unit, "ac_toe_out")


def _driver_point(inputs: Inputs) -> tuple[float, tuple[float, float, float]]:
    """(masa, posición) del piloto de ``driver_mass``/``driver_position`` (G2.45)."""
    md = _convert(*inputs["driver_mass"], "kg", "driver_mass")
    vec, vu = inputs["driver_position"]
    if not isinstance(vec, (list, tuple)) or len(vec) != 3:
        raise TransformError(f"driver_position debe ser un vector de 3: {vec!r}")
    pos = tuple(_convert(v, vu, "m", "driver_position") for v in vec)
    if md < 0:
        raise TransformError(f"masa de piloto negativa: {md}")
    return md, pos  # type: ignore[return-value]


def t_ac_body_cg(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """``CG_LOCATION`` o ``BASEY`` de AC desde el CdG del vehículo entero, quitando los
    ``HUB_MASS`` en los centros de rueda, SIN piloto (G2.18, decisión D-G217-CG = A).

    AC [C] (suspensión de AC, ``docs/research/ac_suspension_coordinates.md``
    §2.7): base de la rueda delantera en z = (1 − CG_LOCATION)·WHEELBASE, trasera en
    −CG_LOCATION·WHEELBASE, y = BASEY respecto al CdG del CUERPO; el cuerpo lleva
    ``TOTALMASS`` y los bujes van aparte (§2.8). Con M masa total, f fracción
    delantera del vehículo entero, h_d/h_t ``HUB_MASS`` por rueda (2 ruedas por eje),
    d_d/d_t altura del centro de rueda del eje respecto al CdG entero:

        M_c = M − 2·h_d − 2·h_t
        CG_LOCATION = (M·f − 2·h_d) / M_c
        BASEY_eje = d_eje + (2·h_d·d_d + 2·h_t·d_t) / M_c

    (``output/g214_suspension_2026-11-03/cdg.py``, valor «sin piloto»).
    ``params.output`` ∈ {``cg_location``, ``basey_front``, ``basey_rear``}.

    Con ``params.driver`` (G2.45) el piloto entra como masa puntual ``m_d`` en ``r_d``
    (relativa al CdG del vehículo entero, ejes AC; entradas ``driver_mass``,
    ``driver_position``, y ``wheelbase`` para ``cg_location``): ``M_t = M + m_d``,
    ``M_c = M_t − 2·h_d − 2·h_t`` y, con la batalla WB,

        CG_LOCATION = (M·f + m_d·f + m_d·r_dz/WB − 2·h_d) / M_c
        BASEY_eje   = d_eje + (2·h_d·d_d + 2·h_t·d_t − m_d·r_dy) / M_c

    Con ``m_d = 0`` coincide exactamente con la forma sin piloto."""
    out = params.get("output")
    need = {"total", "front_fraction", "hub_front", "hub_rear"}
    driver = bool(params.get("driver"))
    if driver:
        need |= {"driver_mass", "driver_position"}
        if out == "cg_location":
            need.add("wheelbase")
    if out in ("basey_front", "basey_rear"):
        need |= {"height_front", "height_rear"}
    elif out != "cg_location":
        raise TransformError(f"ac_body_cg: params.output desconocido {out!r}")
    if set(inputs) != need:
        raise TransformError(
            f"ac_body_cg: entradas {sorted(inputs)} ≠ {sorted(need)} para {out}"
        )
    m = _convert(*inputs["total"], "kg", "total")
    hd = _convert(*inputs["hub_front"], "kg", "hub_front")
    ht = _convert(*inputs["hub_rear"], "kg", "hub_rear")
    if params.get("rear_hub_is_axle"):  # G2.44: AXLE, HUB_MASS = eje entero
        ht /= 2.0
    md, rd = _driver_point(inputs) if driver else (0.0, (0.0, 0.0, 0.0))
    mt = m + md
    mc = mt - 2 * hd - 2 * ht
    if not (m > 0 and hd > 0 and ht > 0 and mc > 0):
        raise TransformError(f"ac_body_cg: masas no válidas M={m} h={hd}/{ht}")
    if out == "cg_location":
        f = _convert(*inputs["front_fraction"], "1", "front_fraction")
        wb_term = 0.0
        if driver:
            wb = _convert(*inputs["wheelbase"], "m", "wheelbase")
            if not wb > 0:
                raise TransformError(f"ac_body_cg: batalla no válida {wb}")
            wb_term = md * rd[2] / wb
        return _convert((mt * f - 2 * hd + wb_term) / mc, "1", out_unit, "ac_body_cg")
    dd = _convert(*inputs["height_front"], "m", "height_front")
    dt = _convert(*inputs["height_rear"], "m", "height_rear")
    d = dd if out == "basey_front" else dt
    return _convert(
        d + (2 * hd * dd + 2 * ht * dt - md * rd[1]) / mc, "m", out_unit, "ac_body_cg"
    )


def t_ac_axle_point(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Centro de un eje en el marco del cuerpo de AC (G2.19).

    AC [C] (suspensión de AC, ``docs/research/ac_suspension_coordinates.md``
    §2.7): la base de las ruedas delanteras está en z = (1 − CG_LOCATION)·WHEELBASE y la
    de las traseras en z = −CG_LOCATION·WHEELBASE, y = BASEY del eje, respecto al CdG
    del cuerpo (x = ±TRACK/2). El centro del eje es el punto medio: (0, BASEY, z).
    Entradas ``wheelbase`` (m), ``cg_location`` (1), ``basey`` (m);
    ``params.axle`` ∈ {``front``, ``rear``}. Salida: vector (x, y, z) en ``out_unit``.
    """
    axle = params.get("axle")
    if axle not in ("front", "rear"):
        raise TransformError(f"ac_axle_point: params.axle desconocido {axle!r}")
    if set(inputs) != {"wheelbase", "cg_location", "basey"}:
        raise TransformError(
            f"ac_axle_point: entradas {sorted(inputs)} ≠ basey, cg_location, wheelbase"
        )
    wb = _convert(*inputs["wheelbase"], "m", "wheelbase")
    cg = _convert(*inputs["cg_location"], "1", "cg_location")
    y = _convert(*inputs["basey"], "m", "basey")
    if not (wb > 0 and 0 < cg < 1):
        raise TransformError(
            f"ac_axle_point: WHEELBASE={wb} o CG_LOCATION={cg} inválido"
        )
    z = (1 - cg) * wb if axle == "front" else -cg * wb
    return [_convert(c, "m", out_unit, "ac_axle_point") for c in (0.0, y, z)]


def t_ac_wing_table(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Tabla C·A (m²) → coeficiente de una ``WING`` de AC de área CHORD·SPAN (G2.19).

    AC [C] (ala de AC/la sustentación): fuerza = ½·ρ·v²·
    ``LUT``(ángulo + aoa)·GAIN·área con área = CHORD·SPAN. Para que el ala reproduzca
    una medida C·A con GAIN 1: ``LUT`` = C·A / (CHORD·SPAN). Entradas ``table``
    (``table:deg->m²``), ``chord`` y ``span`` (m); los ángulos no cambian."""
    if set(inputs) != {"table", "chord", "span"}:
        raise TransformError(f"ac_wing_table: entradas {sorted(inputs)}")
    value, unit = inputs["table"]
    if not (units.is_table(unit) and units.is_table(out_unit)):
        raise TransformError(
            f"ac_wing_table: se esperaban tablas ({unit} -> {out_unit})"
        )
    (sx, sy), (dx, dy) = units.table_axes(unit), units.table_axes(out_unit)
    if not (units.compatible(sx, dx) and units.compatible(sy, "m²") and dy == "1"):
        raise TransformError(f"ac_wing_table: ejes incompatibles {unit} -> {out_unit}")
    area = _convert(*inputs["chord"], "m", "chord") * _convert(
        *inputs["span"], "m", "span"
    )
    if not area > 0:
        raise TransformError(f"ac_wing_table: área CHORD·SPAN no positiva ({area})")
    return [
        [
            units.convert(_num(x, "x"), sx, dx),
            units.convert(_num(y, "y"), sy, "m²") / area,
        ]
        for x, y in value
    ]


def t_ac_strut_axis(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """Magnitud equivalente en rueda → a lo largo del eje del puntal de AC (G2.19).

    AC [C] (suspensión de puntal de AC): muelle y amortiguador actúan a lo largo de
    ``STRUT_CAR → STRUT_TYRE`` con x = longitud base + ``ROD_LENGTH`` − longitud y
    velocidad = proyección sobre ese eje. Con s = |dL_puntal/dh| (entrada
    ``strut_mr``), por trabajo virtual: rigidez o amortiguación en rueda
    k_r = k_p·s² ⇒ k_p = k_r / s² (``params.quantity`` = ``rate``); recorrido o
    velocidad en rueda d_r ⇒ a lo largo del puntal d_p = d_r·s (``length`` o ``speed``).
    Entradas ``x`` (en rueda) y ``strut_mr`` (1)."""
    q = params.get("quantity")
    if q not in ("rate", "length", "speed"):
        raise TransformError(f"ac_strut_axis: params.quantity desconocido {q!r}")
    if set(inputs) != {"x", "strut_mr"}:
        raise TransformError(f"ac_strut_axis: entradas {sorted(inputs)} ≠ strut_mr, x")
    s = _convert(*inputs["strut_mr"], "1", "strut_mr")
    if not s > 0:
        raise TransformError(f"ac_strut_axis: palanca del puntal no positiva ({s})")
    value, unit = inputs["x"]
    v = _num(value, "x")
    out = v / (s * s) if q == "rate" else v * s
    return _convert(out, unit, out_unit, "ac_strut_axis")


def t_ac_sctm_fit(inputs: Inputs, params: dict, out_unit: str) -> Detailed:
    """Una clave del modelo SCTM de AC ajustada a las muestras medidas del neumático
    (G2.20).

    ``samples``: ``tyres.axles.<eje>.force_slip_samples`` (dataset del banco de BeamNG);
    ``fz0``: carga de referencia (N). ``params.output``: clave de ``tyres.ini``
    (``src.ac.tyre_fit.INI_FIELDS``); ``params.max_rms``: error RMS máximo admitido (en
    μ)
    de CADA tipo de barrido; si se supera, la clave no se resuelve (error de la regla).
    El ajuste y el modelo están en ``src.ac.tyre_fit`` y ``src.ac.tyre_sctm``."""
    from src.ac.tyre_fit import INI_FIELDS, fit_dataset

    data, du = _one(inputs, "samples")
    fz0, fu = _one(inputs, "fz0")
    if du != "dataset" or not isinstance(data, dict):
        raise TransformError(f"'samples' debe ser un dataset, no {du!r}")
    output = params.get("output")
    if output not in INI_FIELDS:
        raise TransformError(f"params.output debe ser uno de {sorted(INI_FIELDS)}")
    fz0_n = _convert(fz0, fu, "N", "fz0")
    est = data.get("estimated_sctm")
    if est is not None:
        # neumático ESTIMADO sin banco (src/ac/tyre_estimate.py): no hay muestras que
        # ajustar ni error en μ que comprobar; la nota lo deja claro
        value = est.get(INI_FIELDS[output])
        if value is None:
            raise TransformError(f"el estimador no da {output}")
        digits = params.get("round", 4)
        return Detailed(
            float(f"{value:.{digits}g}"),
            (
                "ESTIMADO sin banco de BeamNG (regresión sobre medidas); no es un ajuste a muestras",
            ),
        )
    ref = params.get("kappa_reference")
    if ref not in (None, "free_rolling_at_alpha"):
        raise TransformError(f"params.kappa_reference desconocido: {ref!r}")
    free_ref = ref == "free_rolling_at_alpha"
    try:
        res = fit_dataset(data, fz0_n, free_reference=free_ref)
    except (ValueError, ZeroDivisionError) as exc:
        raise TransformError(f"ajuste SCTM imposible: {exc}") from exc
    max_rms = params.get("max_rms")
    errs = ", ".join(
        f"{k} {v:.3f} (máx. {res.max_abs[k]:.3f}, n {res.n[k]})"
        for k, v in res.rms.items()
    )
    if max_rms is not None:
        bad = {k: v for k, v in res.rms.items() if v > float(max_rms)}
        if bad:
            raise TransformError(
                f"ajuste SCTM fuera de tolerancia (RMS > {max_rms} en μ): {errs}"
            )
    value = getattr(res.params, INI_FIELDS[output])
    digits = params.get("round", 4)
    value = float(f"{value:.{digits}g}")
    note = (
        f"ajuste SCTM por mínimos cuadrados a {sum(res.n.values())} muestras medidas "
        f"(FZ0 = {fz0_n:.0f} N); error en μ por barrido: {errs}"
        + (
            "; κ referido al giro libre medido a la misma deriva (G2.31)"
            if free_ref
            else ""
        )
    )
    expected = "deg" if output == "FRICTION_LIMIT_ANGLE" else "1"
    if out_unit != expected:
        raise TransformError(
            f"{output}: la salida debe ir en {expected!r}, no {out_unit!r}"
        )
    return Detailed(value, (note,))


def t_ac_body_inertia(inputs: Inputs, params: dict, out_unit: str) -> Any:
    """``[EXPLICIT_INERTIA] INERTIA`` del cuerpo de AC (G2.26).

    AC [C] (coche de AC): con la sección ``[EXPLICIT_INERTIA]`` el cuerpo usa
    ``setMassExplicitInertia`` con ese vector (kg·m², ejes del coche [X cabeceo,
    Y guiñada, Z balanceo]); sin ella usa el cálculo de inercia por caja de masa con ``[BASIC] INERTIA``, y si
    esa clave falta la caja es (0, 0, 0): ``log.txt`` imprime ``LOCAL INERTIA: 0 0 0``
    (los cuatro 200409 de G2.25). El cuerpo de AC es el vehículo entero menos los
    ``HUB_MASS`` en los centros de rueda (mismo modelo que ``ac_body_cg``). Con I el
    tensor del vehículo entero respecto a su CdG G, M la masa total y h_i los bujes en
    r_i = centro de rueda − G = (±TRACK_eje/2, d_eje, (1 − f)·WB | −f·WB):

        I_c(G)   = I − Σ h_i·(|r_i|²·1 − r_i r_iᵀ)          (solo la diagonal)
        M_c      = M − Σ h_i;   e = −Σ h_i r_i / M_c          (CdG del cuerpo)
        I_c(G_c) = I_c(G) − M_c·(|e|²·1 − e eᵀ)              (ejes paralelos)

    Entradas: ``inertia`` (vector de 3, kg·m²), ``total``, ``front_fraction``,
    ``wheelbase``, ``track_front``, ``track_rear``, ``height_front``, ``height_rear``,
    ``hub_front``, ``hub_rear``. Devuelve el vector [I_x, I_y, I_z].

    Con ``params.driver`` (G2.45) se añaden ``driver_mass`` y ``driver_position``: el
    piloto es una masa puntual m_d en r_d (relativa a G; su inercia propia se ignora).
    ``M_t = M + m_d``, ``I(G) += m_d·(|r_d|²·1 − r_d r_dᵀ)`` (ejes paralelos) y el CdG
    del cuerpo pasa a ``e = (m_d·r_d − Σ h_i·r_i) / M_c`` con ``M_c = M_t − Σ h_i``.
    Con ``m_d = 0`` el resultado es idéntico al de sin piloto."""
    need = {
        "inertia",
        "total",
        "front_fraction",
        "wheelbase",
        "track_front",
        "track_rear",
        "height_front",
        "height_rear",
        "hub_front",
        "hub_rear",
    }
    driver = bool(params.get("driver"))
    if driver:
        need |= {"driver_mass", "driver_position"}
    if set(inputs) != need:
        raise TransformError(
            f"ac_body_inertia: entradas {sorted(inputs)} ≠ {sorted(need)}"
        )
    vec, vu = inputs["inertia"]
    if not isinstance(vec, (list, tuple)) or len(vec) != 3:
        raise TransformError(
            f"ac_body_inertia: 'inertia' debe ser un vector de 3: {vec!r}"
        )
    inertia = [_convert(v, vu, "kg·m²", "inertia") for v in vec]
    m = _convert(*inputs["total"], "kg", "total")
    f = _convert(*inputs["front_fraction"], "1", "front_fraction")
    wb = _convert(*inputs["wheelbase"], "m", "wheelbase")
    hd = _convert(*inputs["hub_front"], "kg", "hub_front")
    ht = _convert(*inputs["hub_rear"], "kg", "hub_rear")
    if params.get("rear_hub_is_axle"):  # G2.44: AXLE, HUB_MASS = eje entero
        ht /= 2.0
    axles = (
        (
            hd,
            _convert(*inputs["track_front"], "m", "track_front"),
            _convert(*inputs["height_front"], "m", "height_front"),
            (1.0 - f) * wb,
        ),
        (
            ht,
            _convert(*inputs["track_rear"], "m", "track_rear"),
            _convert(*inputs["height_rear"], "m", "height_rear"),
            -f * wb,
        ),
    )
    md, rd = _driver_point(inputs) if driver else (0.0, (0.0, 0.0, 0.0))
    mc = m + md - 2 * hd - 2 * ht
    if not (m > 0 and hd > 0 and ht > 0 and mc > 0 and wb > 0 and 0 < f < 1):
        raise TransformError(
            f"ac_body_inertia: datos no válidos M={m} h={hd}/{ht} f={f}"
        )
    if min(inertia) <= 0:
        raise TransformError(f"ac_body_inertia: inercia no positiva {inertia}")
    pts = [(h, (s * tr / 2, y, z)) for h, tr, y, z in axles for s in (1.0, -1.0)]
    out = list(inertia)
    for i in range(3):
        out[i] += md * (sum(c * c for c in rd) - rd[i] * rd[i])
    for h, r in pts:
        for i in range(3):
            out[i] -= h * (sum(c * c for c in r) - r[i] * r[i])
    e = [(md * rd[i] - sum(h * r[i] for h, r in pts)) / mc for i in range(3)]
    for i in range(3):
        out[i] -= mc * (sum(c * c for c in e) - e[i] * e[i])
    if min(out) <= 0:
        raise TransformError(f"ac_body_inertia: inercia del cuerpo no positiva {out}")
    return [_convert(round(v, 1), "kg·m²", out_unit, "ac_body_inertia") for v in out]


TRANSFORMS: dict[str, Callable[[Inputs, dict, str], Any]] = {
    "identity": t_identity,
    "constant": t_constant,
    "assume_unit": t_assume_unit,
    "linear_sum": t_linear_sum,
    "scale": t_scale,
    "scale_product": t_scale_product,
    "ac_center_lock_cap": t_ac_center_lock_cap,
    "table": t_table,
    "ac_coast_ref": t_ac_coast_ref,
    "text_contains_map": t_text_contains_map,
    "vector_identity": t_vector_identity,
    "as_undocumented": t_as_undocumented,
    "ac_toe_out": t_ac_toe_out,
    "ac_body_cg": t_ac_body_cg,
    "ac_body_inertia": t_ac_body_inertia,
    "ac_axle_point": t_ac_axle_point,
    "ac_wing_table": t_ac_wing_table,
    "ac_strut_axis": t_ac_strut_axis,
    "ac_sctm_fit": t_ac_sctm_fit,
    "ac_rolling_resistance": None,  # se rellena tras definirla (abajo)
}


def t_ac_rolling_resistance(inputs: Inputs, params: dict, out_unit: str) -> Detailed:
    """``ROLLING_RESISTANCE_0`` = 1000·media de (−Fx/Fz) en rodadura libre (G2.20).

    AC: par de rodadura = −(Fz·0,001)·(RR0 + RR1·v²)·r_ef (``neumático de AC``
    [C]) ⇒ fuerza = Fz·0,001·RR0 con RR1 = 0."""
    table, tu = _one(inputs, "table")
    if not units.is_table(tu):
        raise TransformError(f"'table' debe ser una tabla, no {tu!r}")
    sx, sy = units.table_axes(tu)
    if not (units.compatible(sx, "m/s") and units.compatible(sy, "1")):
        raise TransformError(f"ejes incompatibles: {tu!r}")
    if not isinstance(table, (list, tuple)) or not table:
        raise TransformError("tabla vacía")
    ys = [units.convert(_num(y, "−Fx/Fz"), sy, "1") for _x, y in table]
    mean = sum(ys) / len(ys)
    if mean <= 0:
        raise TransformError(f"resistencia a la rodadura medida no positiva ({mean:g})")
    value = float(f"{1000.0 * mean:.4g}")
    pts = ", ".join(
        f"{units.convert(_num(x, 'v'), sx, 'm/s'):g} m/s: {1000 * y:.2f}"
        for (x, _), y in zip(table, ys)
    )
    return Detailed(value, (f"1000·(−Fx/Fz) medido en rodadura libre: {pts}",))


TRANSFORMS["ac_rolling_resistance"] = t_ac_rolling_resistance


def apply_detailed(
    transform_id: str, inputs: Inputs, params: dict, out_unit: str
) -> tuple[Any, list[str]]:
    """Valor y supuestos dependientes de los valores (lista vacía si no hay)."""
    fn = TRANSFORMS.get(transform_id)
    if fn is None:
        raise TransformError(f"transformación desconocida {transform_id!r}")
    out = fn(inputs, params, out_unit)
    if isinstance(out, Detailed):
        return out.value, list(out.assumptions)
    return out, []


def apply(transform_id: str, inputs: Inputs, params: dict, out_unit: str) -> Any:
    return apply_detailed(transform_id, inputs, params, out_unit)[0]


def implied_assumptions(transform_id: str, params: dict) -> list[str]:
    """Supuestos que introduce la propia transformación (se suman a los de la regla)."""
    if transform_id == "assume_unit":
        return [
            f"la unidad declarada {params.get('declared')!r} se interpreta como "
            f"{params.get('as_unit')!r}"
        ]
    if transform_id == "ac_toe_out":
        return [
            "TOE_OUT de primer orden: desplazamiento de la barra = −convergencia × "
            "sensibilidad en el reposo (la no linealidad con las convergencias del "
            "corpus es ≤ 6·10⁻⁴ relativo; "
            "output/g217_suspension_steering_2026-11-04/cinematica_g217.md)"
        ]
    if transform_id in ("ac_body_cg", "ac_body_inertia") and params.get("driver"):
        return [
            "el piloto es una masa puntual en una posición ESTIMADA (no medida; ojos "
            "de la cámara de cabina o por defecto + hipótesis antropométrica, "
            "± 0,15 m por eje); "
            "su inercia propia se ignora; la masa del piloto entra una sola vez, en "
            "TOTALMASS (car.totalmass_*)"
        ]
    if transform_id == "ac_body_cg":
        return [
            "CdG del cuerpo de AC = CdG del vehículo entero de BeamNG sin los HUB_MASS "
            "en los centros de rueda y SIN piloto (decisión D-G217-CG = A, G2.18): "
            "solo "
            "es coherente con TOTALMASS sin piloto (ir.mass.driver = 0)"
        ]
    if transform_id == "ac_body_inertia":
        return [
            "cuerpo de AC = vehículo entero de BeamNG sin los HUB_MASS, como masas "
            "puntuales en los centros de rueda (mismo modelo que ac_body_cg); solo la "
            "diagonal (AC no admite productos de inercia)"
        ]
    if transform_id == "ac_axle_point":
        return [
            "centro del eje = punto medio de las bases de rueda de AC "
            "(±TRACK/2, BASEY, (1 − CG_LOCATION)·WB | −CG_LOCATION·WB) [C]"
        ]
    if transform_id == "ac_strut_axis":
        return [
            "equivalencia por trabajo virtual con la palanca del eje del puntal EN "
            "REPOSO (la palanca varía en ±40 mm: ver la nota del hecho "
            "strut_motion_ratio)"
        ]
    if transform_id == "ac_wing_table":
        return [
            "LUT = C·A / (CHORD·SPAN) con CD_GAIN = CL_GAIN = 1 (ala de AC/la sustentación "
            "[C]); fuera de la tabla AC mantiene el primer o el último valor "
            "(curva de AC [C])"
        ]
    if transform_id == "ac_sctm_fit":
        return [
            "modelo SCTM de AC (tyres.ini VERSION 10; neumático de AC [C]) ajustado a las "
            "curvas "
            "Fx/Fy medidas en BeamNG con el coche sin modificar (banco G2.20); válido "
            "en el "
            "rango medido (|α| ≤ 20°, |κ| ≤ 0,5, cargas ×0,65–×1,6, 10–30 m/s); fuera "
            "de él "
            "es extrapolación del modelo"
        ]
    if transform_id == "ac_rolling_resistance":
        return [
            "ROLLING_RESISTANCE_0 = 1000·(−Fx/Fz) medio en rodadura libre (AC: fuerza "
            "= "
            "Fz·0,001·(RR0 + RR1·v²) [C]); la variación medida con la velocidad no se "
            "representa (RR1 no se escribe)"
        ]
    if transform_id == "as_undocumented":
        return [
            "unidad 'undocumented' del destino interpretada como "
            f"{params.get('unit')!r}: "
            f"{params.get('semantics')}"
        ]
    return []
