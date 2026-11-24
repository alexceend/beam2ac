"""Fase 6: añade la evidencia del catálogo de referencias de AC a la regla de VALID_SHIFT_RPM_WINDOW.

    python tools/catalog/add_phase6_reference_evidence.py --index output/ac_reference_catalog_2026-10-26/run1/index.json

No cambia el valor (800). Crea ``fmt.valid_shift_window@2`` que sustituye a ``@1`` (las reglas son
inmutables) con la distribución de valores por contenido INDEPENDIENTE de ``drivetrain.ini`` y una
entrada de historial. Se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402
from src.catalog.ac_refs import reference_values  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index", type=Path, required=True)
    args = ap.parse_args(argv)
    index = json.loads(args.index.read_text(encoding="utf-8"))
    groups = reference_values(index, "ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW")
    dist = collections.Counter(g["value"] for g in groups)
    kunos = sorted((g["value"], ",".join(g["cars"])) for g in groups if "kunos_probable" in g["origins"])
    cat = Catalog.load(REPO_CATALOG)
    old = cat.rules["fmt.valid_shift_window@1"]
    new = copy.deepcopy(old)
    new.version, new.supersedes = 2, old.key
    note = (f"{len(groups)} contenidos independientes de drivetrain.ini con la clave; valores: "
            f"{dict(dist.most_common())}; Kunos probables: {kunos}")
    new.evidence = list(old.evidence) + [S.EvidenceRef(type="reference_vehicle", path=str(args.index),
                                                       locator="ac:drivetrain.ini/GEARBOX/VALID_SHIFT_RPM_WINDOW",
                                                       note=note)]
    new.assumptions = [f"valor por referencia: 800 es el más frecuente entre contenidos independientes ({note}); "
                       "no deriva de BeamNG"]
    new.history = list(old.history) + [S.HistoryEntry(
        date="2026-10-26", author="claude (fase 6)", action="new_version", status_from=old.status,
        status_to=new.status, reason="evidencia del catálogo de referencias de AC; el valor no cambia")]
    print("escrita", write_rule(REPO_CATALOG, new))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
