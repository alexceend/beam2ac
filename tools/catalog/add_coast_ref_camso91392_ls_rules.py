"""``[COAST_REF]`` de ``camsoEngine`` 91392d70… por mínimos cuadrados (G2.31, 2026-11-12).

    python tools/catalog/add_coast_ref_camso91392_ls_rules.py [--dry-run]

Crea ``eng.coast_{rpm,torque,nonlinearity}_camso91392@2`` (``supersedes`` = la @1, que no se toca):
misma ley de AC (lineal desde MINIMUM, ``NON_LINEARITY = 0``) pero con la pendiente por MÍNIMOS CUADRADOS sobre
MINIMUM < rpm ≤ techo (``law = acs_least_squares``) en lugar de anclada en el último punto ≤ techo.

Por qué (G2.31, AC): con el ancla en el techo el freno motor de AC es 2,3–2,7 veces más lento que el de BeamNG en
los cinco 40340 (tiempo de techo a MINIMUM + 300 rpm, con la curva del IR y la inercia: quail 2,9 s en BeamNG frente a
7,9 s en AC) y quail (12 000 rpm, inercia 0,151) no baja de régimen dentro del límite pre-registrado de 8 s
(``rpm_bajan_al_soltar``, medido en dos sesiones: 7,98 s / sin llegar). El objetivo (mínimos cuadrados sobre todo el
rango) se fijó antes de calcular si pasa el criterio; el error máximo no sube (lo domina el desfase en MINIMUM, que
ninguna pendiente elimina) y el error cuadrático medio baja un 4–13 %; a cambio sobra freno arriba (+9…+18 N·m).
Solo añade; se niega a sobrescribir.
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
ASSUMPTION = (
    "pendiente del freno motor por MÍNIMOS CUADRADOS sobre MINIMUM < rpm ≤ techo (ley de AC, lineal desde "
    "MINIMUM, NON_LINEARITY = 0; NON_LINEARITY ≠ 0 añade un término convexo y no sirve para el desfase de "
    "BeamNG en el ralentí). Sustituye al ancla en el techo (la @1) porque esta deja el freno de AC 2,3–2,7 veces "
    "más lento que el de BeamNG; el error en MINIMUM no se elimina y arriba sobra freno (+9…+18 N·m)"
)


def build(cat: Catalog) -> list[S.Rule]:
    out = []
    for key in ("rpm", "torque", "nonlinearity"):
        r = cat.rules[f"eng.coast_{key}_camso91392@1"]
        t = copy.deepcopy(r.transform)
        t.params = {**t.params, "law": "acs_least_squares"}
        t.formula = (
            "ac_coast_ref (src/ac/coast_ref.py coast_ref_least_squares): pendiente s = Σ t·x / Σ x² con "
            "x = rpm − MINIMUM sobre MINIMUM < rpm ≤ techo; RPM = último punto ≤ techo, TORQUE = s·(RPM − "
            "MINIMUM); NON_LINEARITY = 0; AC aplica freno(rpm) = s·(rpm − MINIMUM) (motor de AC, "
            "motor de AC)"
        )
        out.append(
            S.Rule(
                **{
                    **{f: getattr(r, f) for f in S.Rule.__dataclass_fields__},
                    "version": 2,
                    "transform": t,
                    "supersedes": r.key,
                    "assumptions": [ASSUMPTION] + list(r.assumptions[1:]),
                    "evidence": list(r.evidence)
                    + [S.EvidenceRef(type="report", path=DOC, locator="§4")],
                    "history": [
                        S.HistoryEntry(
                            date=DATE,
                            author=AUTHOR,
                            action="new_version",
                            reason="quail no baja de régimen en 8 s con el ancla en el techo (AC, G2.31)",
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
        print("[dry-run]" if a.dry_run else write_rule(REPO_CATALOG, r), r.key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
