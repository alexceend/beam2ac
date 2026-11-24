"""Reglas de neumático del 40340 con κ referido al giro libre medido (G2.31, 2026-11-12).

    python tools/catalog/add_tyre_free_ref_40340_rules.py [--dry-run]

Para cada una de las 26 reglas ``tyre.<eje>.<clave>_40340@1`` que usan ``ac_sctm_fit`` crea la
``@2`` (``supersedes`` = la @1; la @1 NO se toca): misma transformación y misma tolerancia
(``max_rms`` 0,06, sin cambios) con ``params.kappa_reference = free_rolling_at_alpha``.

Por qué: en hyacinth y quail el barrido combinado ``b5`` tenía un error de forma de 0,070–0,077 en μ
(comb_x) concentrado en κ ≈ 0…0,1. Causa medida: con la rueda inclinada el giro libre a deriva α no está en
κ = 0 sino en κ₀(α, rueda) = 0,001–0,018 a 5° (las filas ``lat`` del banco, sin orden de κ, lo miden por
separado y coinciden con el cruce por cero de Fx del barrido combinado). Referir κ a ese giro libre cambia la
REFERENCIA de κ, no el agarre (``docs/research/g231_*``). No relaja ninguna tolerancia.
Solo añade: nunca sobrescribe una regla.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-12"
AUTHOR = "claude (G2.31, encargada por el usuario)"
DOC = "docs/research/g231_hyacinth_quail_2026-11-12.md"
NEW_ASSUMPTION = (
    "κ referido al giro libre MEDIDO de la misma rueda a la misma deriva (filas «lat» del banco; G2.31): "
    "con caída el giro libre a 5° está en κ = 0,001–0,018, no en 0. La medida del giro libre es independiente "
    "del error del modelo; no se ajusta nada para bajar el error. AC mide κ respecto a su radio fijo: el "
    "desfase de 0,4–1,8 % en la velocidad de giro libre en curva no se representa"
)


def build(cat: Catalog) -> list[S.Rule]:
    out = []
    for key in sorted(cat.rules):
        r = cat.rules[key]
        if not (
            r.id.startswith("tyre.")
            and r.id.endswith("_40340")
            and r.version == 1
            and r.transform
            and r.transform.id == "ac_sctm_fit"
        ):
            continue
        t = copy.deepcopy(r.transform)
        t.params = {**t.params, "kappa_reference": "free_rolling_at_alpha"}
        t.formula = (
            "mínimos cuadrados de (F_SCTM − F_medida)/Fz con κ' = κ − κ₀(α, rueda), κ₀ = giro libre medido a la "
            "misma deriva (src.ac.tyre_fit.free_rolling_slip)"
        )
        out.append(
            S.Rule(
                **{
                    **{f: getattr(r, f) for f in S.Rule.__dataclass_fields__},
                    "version": 2,
                    "transform": t,
                    "supersedes": r.key,
                    "assumptions": list(r.assumptions) + [NEW_ASSUMPTION],
                    "evidence": list(r.evidence)
                    + [S.EvidenceRef(type="report", path=DOC, locator="§2")],
                    "history": [
                        S.HistoryEntry(
                            date=DATE,
                            author=AUTHOR,
                            action="new_version",
                            reason="κ referido al giro libre medido (hyacinth/quail: comb_x 0,077 → 0,035)",
                            status_from=r.status,
                            status_to="provisional",
                        )
                    ],
                    "notes": "G2.31",
                }
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cat = Catalog.load(REPO_CATALOG)
    rules = build(cat)
    for r in rules:
        if a.dry_run:
            print("[dry-run]", r.key, "supersedes", r.supersedes)
        else:
            print(write_rule(REPO_CATALOG, r))
    print(len(rules), "reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
