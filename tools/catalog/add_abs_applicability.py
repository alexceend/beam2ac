"""Aplicabilidad de ``electronics.ini [ABS]`` (G2.50): «no aplica» al Ibiza (su paquete no cambia). No volver a ejecutar."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

TARGETS = ROOT / "catalog" / "targets" / "ac_physics.json"
KEYS = [("PRESENT", "1"), ("ACTIVE", "1"), ("SLIP_RATIO_LIMIT", "1"), ("RATE_HZ", "Hz")]
src = json.loads((ROOT / "catalog/rules/app.gearbox.inertia@3.json").read_text("utf-8"))
rules = []
for k, unit in KEYS:
    d = json.loads(json.dumps(src))
    d.update(id=f"app.abs.{k.lower()}", version=1, target=f"ac:electronics.ini/ABS/{k}", supersedes="", notes="G2.50")
    d["applies_when"] = [{"fact": "ir.identity.vehicle_id", "op": "ne", "value": "seat_ibiza_acfr"}]
    if k in ("SLIP_RATIO_LIMIT", "RATE_HZ"):  # sin ABS basta PRESENT = 0
        d["applies_when"].append({"fact": "ir.brakes.abs_present", "op": "eq", "value": 1})
    d["transform"]["output_unit"] = unit
    d["assumptions"] = ["el paquete del Ibiza (referencia validada) no cambia: no se le añade electronics.ini"]
    d["history"] = [{"date": "2026-11-15", "author": "claude (G2.50)", "action": "create", "reason": "el Ibiza queda fuera del ABS", "status_from": "", "status_to": d["status"]}]
    rules.append(S.from_dict(d))
text = TARGETS.read_text("utf-8")
add = "".join(f'  "ac:electronics.ini/ABS/{k}",\r\n' for k, _ in KEYS)
assert "electronics.ini/ABS" not in text
TARGETS.write_bytes(text.replace('"targets": [\r\n', '"targets": [\r\n' + add, 1).encode("utf-8") if "\r\n" in text else text.replace('"targets": [\n', '"targets": [\n' + add.replace("\r", ""), 1).encode("utf-8"))
for r in rules:
    write_rule(REPO_CATALOG, r)
Catalog.load(REPO_CATALOG)
print("ok", [r.id for r in rules])
