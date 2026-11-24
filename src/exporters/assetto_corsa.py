"""Exportador Assetto Corsa: resultados del motor de conversión → modelo de destino.

No lee JBeam ni estructuras de Automation. Entradas:

* los resultados del motor de reglas (``Resolution.to_dict()``), que ya llevan regla,
  decisión, confianza y procedencia;
* el IR, solo para la identidad y las capacidades del destino
  (``src.exporters.capabilities``).

Los INI se generan con ``src.project.resolved_ini.build`` (el mismo generador de la
etapa ``resolved_ini``: nada sin resolver se escribe ni se convierte en 0) y se validan
con ``src.ac.car_validate`` leyendo como AC. El modelo es INCOMPLETO mientras haya un
bloqueo o una validación fallida; nunca se declara instalable (falta el KN5, la
interfaz y la prueba en el juego).
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.ir.vehicle import VehicleIR

from . import capabilities as caps


@dataclass
class ACTargetModel:
    vehicle: str
    files: dict[str, str]
    traces: list[dict[str, Any]]
    blockers: list[dict[str, str]]
    not_applicable: list[str]
    validation: list[dict[str, Any]]
    capabilities: dict[str, Any]
    complete: bool
    why_incomplete: list[str] = field(default_factory=list)
    installable: bool = False  # nunca desde aquí: falta KN5, interfaz y prueba

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_files(files: dict[str, str]) -> list[dict[str, Any]]:
    """Validación de los INI tal como los leería AC (sin escribir fuera de la carpeta
    temporal del sistema)."""
    from src.ac.car_validate import (
        awd_problem,
        critical_key_problems,
        load_requirements,
        read_ini,
        shift_window_problem,
    )

    with tempfile.TemporaryDirectory(prefix="beam2ac_target_") as tmp:
        data = Path(tmp)
        for name, text in files.items():
            (data / name).write_text(text, encoding="utf-8")
        inis = {p.name: read_ini(p) for p in sorted(data.glob("*.ini"))}
        out = [
            {"check": n, "ok": ok, "detail": d} for n, ok, d in load_requirements(inis)
        ]
        if "drivetrain.ini" in inis:
            problem = shift_window_problem(inis["drivetrain.ini"])
            out.append(
                {
                    "check": "drivetrain.ini [GEARBOX] VALID_SHIFT_RPM_WINDOW > 0",
                    "ok": problem is None,
                    "detail": problem or "ok",
                }
            )
        out += [
            {"check": n, "ok": ok, "detail": d}
            for n, ok, d in critical_key_problems(data)
        ]
        awd = awd_problem(data)
        if awd is not None:
            out.append({"check": awd[0], "ok": awd[1], "detail": awd[2]})
    return out


def build_target(
    ir: VehicleIR, results: list[dict[str, Any]], catalog: Any
) -> ACTargetModel:
    from src.attrs import schema as S
    from src.project import resolved_ini as R

    attrs = {k: S._to_plain(a) for k, a in catalog.attributes.items()}
    res = R.build(results, attrs, R.evidence_index(catalog))
    validation = validate_files(res.files)
    findings = caps.assess(ir)
    blockers = [asdict(b) for b in res.blockers]
    why = []
    if blockers:
        why.append(
            f"{len(blockers)} bloqueos (atributos sin resolver o no escribibles)"
        )
    failed = [c for c in validation if not c["ok"]]
    if failed:
        why.append(f"{len(failed)} validaciones de carga fallidas")
    pending = [f.feature for f in findings if f.level == "pending_research"]
    if pending:
        why.append(f"rasgos sin investigar en el destino: {pending}")
    vid = ir.identity.vehicle_id
    return ACTargetModel(
        vehicle=f"{vid.value if vid.present else '?'}",
        files=res.files,
        traces=[asdict(t) for t in res.traces],
        blockers=blockers,
        not_applicable=res.not_applicable,
        validation=validation,
        capabilities=caps.summary(findings),
        complete=not (blockers or failed),
        why_incomplete=why,
    )
