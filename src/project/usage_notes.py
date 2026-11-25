"""Notas de uso del coche convertido (del colaborador, 2026-11-24) y avisos automáticos desde los hechos.

Las notas son texto fijo (se muestran en el informe de exportación y en la pestaña «Notas de uso» de la
aplicación); los avisos se derivan de los hechos del modelo de atributos cuando la fuente los contiene.
"""

from __future__ import annotations

from typing import Any

NOTES: tuple[str, ...] = (
    "No uses eje rígido delantero en Automation: no está soportado.",
    "No uses diferencial bloqueable (D-G212-LOCK sin resolver).",
    "No uses ruedas de competición.",
    "Calidad para un agarre óptimo en Automation: de +6 a +8.",
    "Las revoluciones de cambio de marcha (cuando la caja es automática) pueden fallar: tendrás que cambiar de forma manual.",
    "Algunas animaciones se pueden generar con scripts de Automation (se indicará cuáles); otras hay que hacerlas en Assetto Corsa.",
    "El depósito de combustible hay que colocarlo de forma manual donde se desee (editor de Vista 3D: la posición inicial es provisional).",
    "El collider hay que hacerlo de forma manual (el export escribe un collider.kn5 por defecto; sin editor todavía).",
    "La caja de inercia (inertial box) hay que hacerla de forma manual (el export ya escribe [EXPLICIT_INERTIA] medido en los nodos; confirmar si aún hace falta).",
)


def _v(facts: dict[str, Any], key: str) -> Any:
    return (facts.get(key) or {}).get("value")


def auto_warnings(facts: dict[str, Any]) -> list[str]:
    """Avisos que la fuente permite comprobar (eje rígido delantero, diferencial bloqueable)."""
    out = []
    if _v(facts, "ir.suspension.axles.front.linkage") == "rigid_axle":
        out.append(NOTES[0])
    for axle in ("front", "rear", "center"):
        kind = str(_v(facts, f"ir.drivetrain.differentials.{axle}.kind") or "")
        if "lock" in kind:
            out.append(f"{NOTES[1]} (diferencial {axle}: «{kind}»)")
    return out


def report_lines(facts: dict[str, Any]) -> list[str]:
    md = ["", "## Notas de uso", ""]
    warn = auto_warnings(facts)
    if warn:
        md += (
            ["**Avisos detectados en este coche:**", ""]
            + [f"- ⚠ {w}" for w in warn]
            + [""]
        )
    return md + [f"- {n}" for n in NOTES]
