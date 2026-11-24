"""Acepta, como decisiones del PROYECTO, solo las propuestas de bajo riesgo con evidencia.

    python tools/analysis/accept_low_risk_proposals.py --project output/projects/<p>
        --proposals output/<…>/proposals.json [--dry-run]

Entrada: la clasificación de ``tools/analysis/classify_proposals.py`` (riesgo, validación,
supuestos y comprobaciones estáticas). Política (fase C2, explícita y conservadora):

* riesgo ``bajo`` (sin efecto físico, o geometría/lectura directa con semántica de AC
  documentada), validación ``estática``; o
* riesgo ``medio`` con validación ``estática``, **sin supuestos declarados**, sin
  cuestiones abiertas y con al menos una comprobación estática hecha y sin fallos
  (p. ej. marchas y ``FINAL`` frente a la velocidad punta declarada).

Todo lo demás (riesgo alto, pruebas específicas, decisiones, supuestos) queda pendiente.
Cada decisión se registra con ``ReviewService`` (acción ``accept`` del valor de la regla),
ámbito vehículo, estado ``provisional``, con la clasificación como evidencia. Nunca escribe
en el catálogo de producción: solo en ``<proyecto>/decisions/``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def eligible(p: dict[str, Any]) -> tuple[bool, str]:
    if p.get("approved"):
        return False, "ya aprobada"
    if p["validation"] != "estática":
        return False, f"validación {p['validation']}"
    if p["risk"] == "bajo":
        return True, "riesgo bajo, validación estática"
    if p["risk"] != "medio":
        return False, f"riesgo {p['risk']}"
    if p.get("assumptions") or p.get("open_issues"):
        return False, "riesgo medio con supuestos o cuestiones abiertas"
    done = p.get("static_checks_done") or {}
    if not done:
        return False, "riesgo medio sin comprobación estática hecha"
    if any(v is False for v in done.values()):
        return False, "una comprobación estática falla"
    return True, "riesgo medio: lectura directa sin supuestos, comprobación estática hecha"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--proposals", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from src.app.service import ReviewService
    from src.pipeline.physics_preview import load_vehicle_dir, load_vehicle_zip
    from src.pipeline.physics_preview import preview_physics
    from src.project.manifest import load_project

    project = load_project(a.project)
    data = json.loads(a.proposals.read_text(encoding="utf-8"))
    rows = []
    for p in data["proposals"]:
        ok, why = eligible(p)
        rows.append((p, ok, why))
        print(f"{'ACEPTAR ' if ok else 'pendiente'} {p['target']}  ({why})")
    if a.dry_run:
        return 0
    s = project.source
    src = (
        load_vehicle_dir(Path(s.path), s.pc)
        if s.kind == "dir"
        else load_vehicle_zip(
            Path(s.path), Path(s.common_zip) if s.common_zip else None, s.pc
        )
    )
    svc = ReviewService(preview_physics(src, project.driver_mass_kg), project.root)
    for p, ok, why in rows:
        if not ok:
            continue
        checks = json.dumps(p.get("static_checks_done") or {}, ensure_ascii=False)
        out = svc.decide(
            {
                "action": "accept",
                "source": "result",
                "target": p["target"],
                "scope": "vehicle",
                "status": "provisional",
                "author": "automático (accept_low_risk_proposals.py, fase C2)",
                "justification": (
                    f"Propuesta {p['rule']} aceptada por la política de bajo riesgo: "
                    f"{why}. Motivo de la regla: {p['why']}. Comprobaciones: "
                    f"{checks[:400]}"
                ),
                "evidence": [
                    {
                        "type": "report",
                        "path": str(a.proposals),
                        "locator": p["target"],
                        "note": f"riesgo {p['risk']}, validación {p['validation']}",
                    }
                ],
            }
        )
        r = out["result"]
        print("  ->", r["target"], r["status"], r["value"] if not isinstance(r["value"], list) else "tabla")
    return 0


if __name__ == "__main__":
    sys.exit(main())
