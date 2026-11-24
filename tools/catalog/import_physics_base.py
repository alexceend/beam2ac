"""Importa la base física aprobada del Ibiza como decisiones manuales provisionales.

    python tools/catalog/import_physics_base.py [--dry-run]

Lee (solo lectura) ``output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25`` y su
manifiesto. Para cada clave de sus INI:

* si una regla del catálogo reproduce el valor con la precisión con la que está escrito
  (±½ de la última cifra decimal), no se crea decisión;
* si no, se crea una ``Decision`` ``manual_value`` de ámbito vehículo, estado ``provisional``
  (la base es funcional en el juego pero NO calibrada), con el comentario de la clave y la
  cabecera del archivo como justificación y el sha256 del manifiesto como evidencia.

Las reglas con dependencias (p. ej. ``TOTALMASS`` a partir de ``HUB_MASS``) se comprueban con
las demás decisiones ya cargadas. Escribe ``catalog/decisions/seat_ibiza_acfr.jsonl`` (se niega
si existe) y un informe ``output/catalog_import_2026-10-26/import_report.json``.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.compare import parse_value as _parse_value, same_as_written  # noqa: E402
from src.attrs.engine import ResolutionEngine  # noqa: E402
from src.attrs.report import context_from_preview  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, append_decision  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_dir, parse_ini_text, preview_physics  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

BASE = ROOT / "output/seat_ibiza_acfr/physics_bases/ibiza_v18bu_2026-10-25"
VEHICLE, VARIANT = "seat_ibiza_acfr", "35b84"
DATE = "2026-10-26"


parse_value = _parse_value
same = same_as_written


# Estados con los que el resultado de una regla se escribe en un INI (resolved_ini).
USABLE = ("known", "inferred", "format_requirement")


def rule_reproduces(res: Any, written: str) -> bool:
    """¿La regla reproduce el valor de la base Y su resultado es utilizable?

    Corrección 2026-10-26 (fase B3): antes bastaba con que el valor coincidiera, así que
    FRONT_SHARE (regla con cuestión abierta, estado ``ambiguous``) no se importó como
    decisión y el valor aprobado 0,8570 dejó de llegar a ningún INI.
    """
    return (res.source == "rule" and res.value is not None and res.status in USABLE
            and not res.requires_approval and same(written, res.value))


def file_header(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        if raw.startswith(";"):
            lines.append(raw.lstrip("; ").strip())
        elif raw.strip():
            break
    return " ".join(lines)[:300]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    target_file = REPO_CATALOG / "decisions" / f"{VEHICLE}.jsonl"
    if target_file.exists() and not args.dry_run:
        raise SystemExit(f"ya existe {target_file}; no se reimporta")

    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    pkg = Path(manifest["package_dir"])
    catalog = Catalog.load(REPO_CATALOG)
    pv = preview_physics(load_vehicle_dir(ROOT / "sample_beamng/vehicles/seat_ibiza_acfr", f"{VARIANT}.pc"))
    if not pv.ok:
        raise SystemExit(f"la vista previa del Ibiza falla: {pv.failed_stages}")

    def engine_for(cat: Catalog) -> ResolutionEngine:
        return ResolutionEngine(cat, context_from_preview(pv, cat, 70.0))

    base_engine = engine_for(catalog)
    entries: list[tuple[str, str, str, str, str]] = []  # (target, file, written, comment, header)
    for rel in sorted(manifest["files"]):
        if not rel.endswith(".ini"):
            continue
        text = (pkg / rel).read_text(encoding="utf-8")
        header = file_header(text)
        for (sec, key), (written, comment) in parse_ini_text(text).items():
            entries.append((f"ac:{Path(rel).name}/{sec}/{key}", rel, written, comment, header))

    report: dict[str, Any] = {"reproduced_by_rule": [], "decisions": [], "reproduced_with_dependencies": []}
    pending: list[S.Decision] = []
    for target, rel, written, comment, header in entries:
        if target not in catalog.attributes:
            raise SystemExit(f"atributo no definido en el catálogo: {target}")
        res = base_engine.resolve(target)
        if rule_reproduces(res, written):
            report["reproduced_by_rule"].append({"target": target, "rule": res.rule, "written": written,
                                                 "value": res.value})
            continue
        attr = catalog.attributes[target]
        tags = [t for t in ("DIAG", "EXPERIMENTAL") if t in (comment + header).upper()]
        just = (f"Valor de la base física aprobada ibiza_v18bu_2026-10-25 (funcional en el juego, NO calibrada"
                f"{'; ' + '/'.join(tags) if tags else ''}). Comentario: '{comment or '—'}'. "
                f"Cabecera del archivo: '{header or '—'}'.")
        pending.append(S.Decision(
            id=f"ibz.base.{Path(rel).stem}.{target.split('/', 1)[1].replace('/', '.')}",
            target=target, vehicle=VEHICLE, variant=VARIANT, action="manual_value", justification=just,
            author="importación de la base física (tools/catalog/import_physics_base.py)", date=DATE,
            scope=S.Scope(level="vehicle", vehicle=VEHICLE), status="provisional", value=parse_value(written),
            unit=attr.unit, evidence=[S.EvidenceRef(type="file", path=str((pkg / rel).relative_to(ROOT)),
                                                    locator=f"[{target.split('/')[1]}] {target.split('/')[2]}",
                                                    sha256=manifest["files"][rel])],
            history=[S.HistoryEntry(date=DATE, author="importación", action="create", status_to="provisional",
                                    reason="migración de la base física congelada al catálogo de decisiones")],
        ))
    # segundo paso: reglas con dependencias entre atributos
    keep: list[S.Decision] = []
    for d in pending:
        if not catalog.rules_for(d.target):
            keep.append(d)
            continue
        trial = copy.copy(catalog)
        trial.decisions = [x for x in pending if x.id != d.id]
        res = engine_for(trial).resolve(d.target)
        written = next(w for t, _r, w, _c, _h in entries if t == d.target)
        if rule_reproduces(res, written):
            report["reproduced_with_dependencies"].append({"target": d.target, "rule": res.rule, "written": written,
                                                           "value": res.value,
                                                           "inputs": [i.get("attribute") or i.get("fact") for i in res.inputs]})
        else:
            keep.append(d)
    report["decisions"] = [d.target for d in keep]
    print(f"reproducidas por regla: {len(report['reproduced_by_rule'])}; con dependencias: "
          f"{len(report['reproduced_with_dependencies'])}; decisiones: {len(keep)}")
    if args.dry_run:
        return 0
    for d in keep:
        append_decision(REPO_CATALOG, d, existing=catalog)
    out = new_output_dir(ROOT / "output" / "catalog_import_2026-10-26")
    ensure_safe_output_path(out / "import_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    Catalog.load(REPO_CATALOG)  # el catálogo resultante debe ser coherente
    print(f"escrito {target_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
