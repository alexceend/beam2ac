"""Dirección de la configuración activa (G2.15): cremallera movida por hidros.

Semántica del juego (BeamNG 0.38.6; sha256 de cada archivo en el informe G2.15 §3.1):

* ``lua/common/jbeam/sections/nodeBeam.lua`` ``processHydros``: ``inputSource`` por
  defecto ``"steering"``; con ``factor`` f numérico ⇒ ``inLimit = 1 − |f|``,
  ``outLimit = 1 + |f|``, ``inputFactor = sign(f)``; ``extentFactor`` e se
  convierte antes en ``f = e / L0`` (L0 = distancia entre los dos nodos del hidro en
  la pose del JBeam).
* ``lua/vehicle/hydros.lua`` ``init``/``update``: en régimen la longitud de
  referencia del hidro es ``L0·(1 + f·s)``, con s ∈ [−1, 1] la entrada de dirección.
  Con los dos hidros
  cruzados de las exportaciones de Automation (``st2l–st1r`` +f, ``st2r–st1l`` −f),
  alineados con el raíl de la cremallera, la cremallera se desplaza ``Δ = f·L0·s`` ⇒
  recorrido a tope
  ``|f|·L0`` [derivado; el banco del juego lo contrasta, informe G2.15 §3.3].
* ``hydros.lua`` l. 500: ángulo del volante = ``s · v.data.input.steeringWheelLock``.
* ``lua/vehicle/input.lua`` ``initSecondStage`` (l. 42, 149-182):
  ``steeringWheelLock`` =
  la sección ``input`` del vehículo si lo declara; si no, el ``|steeringWheelLock|`` del
  primer hidro de dirección que lo tenga; si no, 450 (valor por defecto del Lua).

Lo que NO se deriva aquí (depende de la cinemática de la mangueta y de los topes
``SUPPORT``, que no son datos declarados): el ángulo máximo de la rueda, la relación de
dirección y el Ackermann. Se conservan como pendientes en el IR.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from src.coords import beamng_to_ac
from src.jbeam import ResolvedConfig, evaluate, parse_table

from .components import collect_beams
from .nodes import collect_nodes

DEFAULT_STEERING_WHEEL_LOCK = 450.0  # lua/vehicle/input.lua:42
ALIGN_COS = 0.9999  # hidro alineado con el raíl (|cos| mínimo)


class SteeringDataError(ValueError):
    """Dirección no reconocida o incompleta."""


@dataclass(frozen=True)
class SteeringHydro:
    part: str
    node1: str
    node2: str
    factor: float  # f efectivo (de factor o de extentFactor / L0)
    length: float  # L0 (m)
    steering_wheel_lock: float | None  # leído en el hidro (sin signo), si lo hay
    raw: dict[str, Any]


@dataclass
class SteeringSystem:
    hydros: list[SteeringHydro]
    mechanism: str  # "rack" (cremallera en raíl movida por hidros) u "other"
    rack_travel: float  # m, a tope en cada sentido (|f|·L0)
    steering_wheel_lock: float  # grados de volante a tope (s = 1)
    steering_wheel_lock_source: str  # "input" | "hydro" | "default"
    ffb_coef: Any = None  # sección input.FFBcoef (sin equivalencia demostrada)
    issues: list[str] = field(default_factory=list)
    # G2.16: nodos de los extremos de la cremallera (raíles de los hidros) y dirección
    # unitaria de los raíles en ejes AC (solo con mecanismo "rack")
    rack_nodes: tuple[str, ...] = ()
    rack_axis_ac: tuple[float, float, float] | None = None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _on_rail(a: str, rail: list[str], nodes: dict[str, Any]) -> bool:
    """¿El nodo ``a`` es un extremo del raíl o está sobre su segmento?

    El hidro de la cremallera nace en un extremo del raíl (Automation 38022/200409) o en
    un nodo intermedio de la línea (eje rígido delantero de bisexual_aspiration: el hidro
    parte del centro del eje ``fax3`` y el raíl va de ``fax3l`` a ``fax3r``). En los dos
    casos el nodo deslizante recorre ``|factor|·L0`` a lo largo del raíl."""
    if a in rail:
        return True
    if a not in nodes:
        return False
    p, q, r = (nodes[n] for n in (rail[0], rail[1], a))
    pq = [y - x for x, y in zip(p, q)]
    pr = [y - x for x, y in zip(p, r)]
    n2 = sum(x * x for x in pq)
    if n2 == 0:
        return False
    t = sum(x * y for x, y in zip(pq, pr)) / n2
    off = math.dist(r, [x + t * d for x, d in zip(p, pq)])
    return 0.0 <= t <= 1.0 and off < 1e-4


def build_steering(resolved: ResolvedConfig) -> SteeringSystem:
    variables = resolved.variable_values()
    nodes = {n.id: n.pos for n in collect_nodes(resolved)}
    hydros: list[SteeringHydro] = []
    for b in collect_beams(resolved, "hydros"):
        source = b.value("inputSource", variables, "steering")
        if source != "steering":
            continue
        n1, n2 = b.nodes[0], b.nodes[1]
        if n1 not in nodes or n2 not in nodes:
            raise SteeringDataError(f"hidro con nodo inexistente: {n1}–{n2}")
        length = math.dist(nodes[n1], nodes[n2])
        f = _num(b.value("factor", variables))
        e = _num(b.value("extentFactor", variables))
        if e is not None:
            f = e / length  # nodeBeam.lua: extentFactor tiene prioridad sobre factor
        if f is None:
            raise SteeringDataError(
                f"hidro de dirección {n1}–{n2} sin factor/extentFactor numérico "
                "(límites por inLimit/outLimit/inExtent no soportados)"
            )
        lock = _num(b.value("steeringWheelLock", variables))
        hydros.append(
            SteeringHydro(
                b.part,
                n1,
                n2,
                f,
                length,
                abs(lock) if lock is not None else None,
                {
                    k: b.value(k, variables)
                    for k in ("inRate", "outRate")
                    if k in b.props
                },
            )
        )
    if not hydros:
        raise SteeringDataError("sin hidros de dirección (inputSource 'steering')")
    travels = sorted({round(abs(h.factor) * h.length, 9) for h in hydros})
    if len(travels) != 1:
        raise SteeringDataError(f"recorridos distintos entre hidros: {travels}")
    issues: list[str] = []
    # cremallera: cada hidro une un nodo de un raíl (links) con un slidenode de ese raíl
    rails: dict[str, list[str]] = {}
    for _, sec in resolved.section_contributions("rails"):
        if isinstance(sec, dict):
            for name, r in sec.items():
                if isinstance(r, dict) and isinstance(r.get("links:"), list):
                    rails[name] = list(r["links:"])
    slides: dict[str, str] = {}
    for _, sec in resolved.section_contributions("slidenodes"):
        for row in parse_table(sec).rows:
            slides[str(row.get("id:"))] = str(row.get("railName"))
    mechanism = "rack"
    rack_nodes: list[str] = []
    axes: list[tuple[float, float, float]] = []
    for h in hydros:
        a, b = (h.node1, h.node2) if h.node2 in slides else (h.node2, h.node1)
        rail = rails.get(slides.get(b, ""), [])
        if b not in slides or len(rail) != 2 or not _on_rail(a, rail, nodes):
            mechanism = "other"
            break
        ra, rb = (nodes[n] for n in rail)
        rv = [x - y for x, y in zip(rb, ra)]
        hv = [x - y for x, y in zip(nodes[a], nodes[b])]
        cos = sum(x * y for x, y in zip(rv, hv)) / (
            math.sqrt(sum(x * x for x in rv)) * math.sqrt(sum(x * x for x in hv))
        )
        if abs(cos) < ALIGN_COS:
            mechanism = "other"
            issues.append(
                f"hidro {h.node1}–{h.node2} no alineado con el raíl (cos {cos:.6f})"
            )
            break
        # extremos del raíl y nodo deslizante (la barra se une a uno de ellos)
        rack_nodes.extend(n for n in (*rail, b) if n not in rack_nodes)
        rn = math.sqrt(sum(x * x for x in rv))
        ax = beamng_to_ac(tuple(x / rn for x in rv))  # type: ignore[arg-type]
        axes.append(ax if ax[0] >= 0 else (-ax[0], -ax[1], -ax[2]))
    if mechanism != "rack":
        issues.append(
            "no es una cremallera en raíl con hidros alineados: el recorrido |f|·L0 "
            "no es "
            "el desplazamiento de la cremallera"
        )
    sec_lock = None
    ffb = None
    for _, sec in resolved.section_contributions("input"):
        if isinstance(sec, dict):
            if "steeringWheelLock" in sec:
                sec_lock = _num(evaluate(sec["steeringWheelLock"], variables))
            if "FFBcoef" in sec:
                ffb = evaluate(sec["FFBcoef"], variables)
    hydro_locks = {
        h.steering_wheel_lock for h in hydros if h.steering_wheel_lock is not None
    }
    if sec_lock is not None:
        lock, src = sec_lock, "input"
    elif hydro_locks:
        if len(hydro_locks) != 1:
            raise SteeringDataError(
                "steeringWheelLock distinto entre hidros "
                f"{sorted(hydro_locks)}: input.lua "
                "toma el primero en orden de tabla (no determinable aquí)"
            )
        lock, src = hydro_locks.pop(), "hydro"
    else:
        lock, src = DEFAULT_STEERING_WHEEL_LOCK, "default"
    return SteeringSystem(
        hydros=hydros,
        mechanism=mechanism,
        rack_travel=travels[0],
        steering_wheel_lock=lock,
        steering_wheel_lock_source=src,
        ffb_coef=ffb,
        issues=issues,
        rack_nodes=tuple(rack_nodes) if mechanism == "rack" else (),
        rack_axis_ac=axes[0] if mechanism == "rack" and axes else None,
    )
