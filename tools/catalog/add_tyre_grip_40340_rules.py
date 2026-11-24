"""Modelo de agarre del neumático del exportador 40340 desde SUS medidas de banco (G2.29, 2026-11-11).

    python tools/catalog/add_tyre_grip_40340_rules.py [--dry-run]

Las 49 reglas del neumático del 200409 que aún no tenían clon (``tyre.*_200409@1`` y
``app.tyres.*_200409@1``: SCTM, VERSION 10, RATE, FZ0, inercia, resistencia a la rodadura, y los valores de
referencia DAMP y RELAXATION_LENGTH de G2.25) se clonan para el 40340 con ``clone()`` de
``clone_200409_rules_to_40340.py`` (solo cambia la versión del exportador, el estado ``provisional`` y la
procedencia). Sus condiciones exigen las medidas en el IR (``force_slip_samples``, ``reference_load``): solo
se aplican a un vehículo con registro ``beamng_tyre_bench`` asociado por la huella del origen
(``measurements/beamng_bench/tyre/``; los cinco 40340 se midieron en G2.29, banco de G2.20 sin cambios).

Solo añade; se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import clone_200409_rules_to_40340 as C  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

C.AUTHOR = "claude (G2.29, encargada por el usuario)"
C.DOC = "docs/research/g229_consolidacion_2026-11-11.md"
C.ASSUMPTION = (
    "clon de la regla del 200409 para el exportador 40340: el neumático se midió en el banco de BeamNG "
    "(el de G2.20, sin cambios) con ESTE vehículo del 40340 (G2.29, dos sesiones); el ajuste y sus "
    "tolerancias son los mismos; ningún 40340 se ha probado en AC"
)


def keys() -> list[str]:
    out = []
    for p in sorted(C.RULES_DIR.glob("*_200409@1.json")):
        if p.name.startswith(("tyre.", "app.tyres.")):
            if not (C.RULES_DIR / p.name.replace("_200409@", "_40340@")).exists():
                out.append(p.stem)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ks = keys()
    rs = []
    for k in ks:
        r = C.clone(k)
        d = S.to_dict(r)
        if k.startswith("app."):  # la aplicabilidad del 200409 está validada [C]; no depende del coche
            d["status"] = "validated"
            d["history"][0]["status_to"] = "validated"
        d["notes"] = "G2.29"
        rs.append(S.from_dict(d))
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    if not rs:
        raise SystemExit("nada que clonar (ya existen)")
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
