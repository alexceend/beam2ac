"""``[COAST_REF]`` para ``camsoEngine.lua`` 91392d70… (exportador 40340) (G2.27, 2026-11-11).

    python tools/catalog/add_coast_ref_camso91392_rules.py [--dry-run]

El freno motor de estos coches ya está en el IR (``engine.coast_curve``, ``computed``): fila de gas
cerrado 0 de la tabla de par menos la fricción, según ``CAMSO_ENGINE_VERSIONS`` (``camsoEngine.lua:725``,
leído; ``src/extract/engine_camso.py``). Faltaba la regla: las del Ibiza (``eng.coast_*@2``) están
limitadas a la otra versión del Lua, y sin ``[COAST_REF]`` AC da freno motor 0 (G2.24).

Clona ``eng.coast_{rpm,torque,nonlinearity}_combustion@2`` (misma transformación ``ac_coast_ref`` con
``law = acs_from_minimum``: anclado en el techo de rpm, lineal desde MINIMUM como hace AC)
cambiando solo las condiciones: ``camsoEngine`` + versión 91392d70…. ``provisional``: ningún 40340 se ha
probado en AC. Solo añade; se niega a sobrescribir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402
from src.extract.engine_camso import CAMSO_ENGINE_VERSIONS  # noqa: E402

DATE = "2026-11-11"
AUTHOR = "claude (G2.27, trabajo paralelo: bloqueos del 40340, encargada por el usuario)"
SHA = "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
RULES_DIR = ROOT / "catalog" / "rules"
CONDITIONS = [
    {"fact": "ir.engine.device_type", "op": "eq", "value": "camsoEngine"},
    {"fact": "ir.formats.code_versions.camsoEngine", "op": "eq", "value": SHA},
]


def rules() -> list[S.Rule]:
    assert CAMSO_ENGINE_VERSIONS[SHA]["closed_throttle"] == 0
    out = []
    for key in ("rpm", "torque", "nonlinearity"):
        d = json.loads((RULES_DIR / f"eng.coast_{key}_combustion@2.json").read_text("utf-8"))
        d["supersedes"] = ""  # un clon no sustituye a ninguna regla
        d.update(
            id=f"eng.coast_{key}_camso91392", version=1, status="provisional", conditions=CONDITIONS,
            assumptions=[d["assumptions"][0],
                         "freno de BeamNG = fila de gas cerrado 0 de la tabla de par − fricción "
                         f"({CAMSO_ENGINE_VERSIONS[SHA]['evidence']}); en el ralentí BeamNG ya frena y AC da 0: "
                         "el error decrece linealmente hasta 0 en el techo (misma limitación que en G2.24)",
                         "ningún coche con esta versión del Lua se ha probado en AC"],
            history=[{"date": DATE, "author": AUTHOR, "action": "create", "status_from": "",
                      "status_to": "provisional",
                      "reason": "sin [COAST_REF] AC da freno motor 0; el IR ya tiene la curva de esta versión"}],
            notes="G2.27 (paralelo)")
        out.append(S.from_dict(d))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = rules()
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
