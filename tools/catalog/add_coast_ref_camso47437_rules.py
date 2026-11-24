"""[COAST_REF] de los camsoEngine 47437d5c… (38022, mods) por regla (G2.44). NO volver a ejecutar.

Clona ``eng.coast_{rpm,torque,nonlinearity}_camso91392@2`` (pendiente por mínimos cuadrados, D-G231-COAST) con la versión
del Lua ``47437d5c…`` y excluye el Ibiza (su COAST_REF es una decisión de proyecto: D-G29c, anclaje en el techo).
Evidencia: mismo ``camsoEngine`` que el Ibiza; la ley de AC (lineal desde MINIMUM) es la de G2.10; sin prueba en AC."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

OLD = "91392d703ac3d54a9c45fa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb"
NEW = "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed"
rules = []
for k in ("rpm", "torque", "nonlinearity"):
    d = json.loads((ROOT / f"catalog/rules/eng.coast_{k}_camso91392@2.json").read_text("utf-8"))
    for c in d["conditions"] + d.get("scope", {}).get("conditions", []):
        if isinstance(c["value"], str) and c["value"].startswith("91392d70"):
            c["value"] = NEW
    d["conditions"].append({"fact": "ir.identity.vehicle_id", "op": "ne", "value": "seat_ibiza_acfr"})
    d.update(id=f"eng.coast_{k}_camso47437", version=1, supersedes="", status="provisional", notes="G2.44")
    d["assumptions"] = list(d.get("assumptions", [])) + [
        "mismo camsoEngine que el Ibiza (38022); sin prueba en AC para estos coches; el Ibiza conserva su decisión D-G29c"]
    d["history"] = [{"date": "2026-11-14", "author": "claude (G2.44: mods 38022)", "action": "create",
                     "reason": f"clon de eng.coast_{k}_camso91392@2 para camsoEngine 47437d5c", "status_from": "",
                     "status_to": "provisional"}]
    rules.append(S.from_dict(d))
if "--dry-run" in sys.argv:
    print("ok", [r.key for r in rules])
else:
    for r in rules:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print("escritas", len(rules))
