"""ABS de BeamNG -> ``electronics.ini [ABS]`` de AC (G2.50, D-G250-ABS, ``provisional``).

    python tools/catalog/add_abs_rules.py [--dry-run]     (no volver a ejecutar: las reglas ya existen)

Origen (``wheels.lua``, BeamNG 0.38): PID por rueda, objetivo ``absSlipRatioTarget + 2/v``, a ``absHz`` (por defecto
100), activo por encima de 5 m/s. Destino (ABS de AC / ABS de AC [C]): límite ``SLIP_RATIO_LIMIT`` todo/nada por
rueda, ``RATE_HZ``, activo por encima de 20 km/h. No es una igualdad de comportamiento: es la correspondencia más
cercana. ``PRESENT = ACTIVE`` = 1 (ABS en ambos ejes) o 0 (en ninguno; así AC no depende de su valor por defecto).
Excluye al Ibiza (su paquete no cambia). Sin prueba en AC.
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

RULES = ROOT / "catalog" / "rules"
ATTR_FILE = ROOT / "catalog" / "attributes" / "assettocorsa_electronics.json"
DOC = {"type": "code", "path": "output/g227_2026-11-10/acs/c", "locator": "ABS de AC, ABS de AC", "sha256": "", "note": ""}
REPORT = {"type": "report", "path": "docs/research/g250_abs_termica_2026-11-15.md", "locator": "§ABS", "sha256": "", "note": ""}
BASE = [
    {"fact": "ir.formats.exporter_version", "op": "in", "value": ["38022", "40340", "200409"]},
    {"fact": "ir.identity.vehicle_id", "op": "ne", "value": "seat_ibiza_acfr"},
]
KEYS = [  # clave, atributo, unidad AC, dtype, entrada IR, condición extra, texto
    ("PRESENT", "1", "integer", "ir.brakes.abs_present", None, "1 = ABS en ambos ejes; 0 = en ninguno"),
    ("ACTIVE", "1", "integer", "ir.brakes.abs_present", None, "igual que PRESENT (ABS activo desde el inicio)"),
    ("SLIP_RATIO_LIMIT", "1", "number", "ir.brakes.abs_slip_limit", {"fact": "ir.brakes.abs_present", "op": "eq", "value": 1}, "absSlipRatioTarget ponderado por el reparto de freno"),
    ("RATE_HZ", "Hz", "number", "ir.brakes.abs_rate_hz", {"fact": "ir.brakes.abs_present", "op": "eq", "value": 1}, "absHz (el menor de los dos ejes)"),
]
ASSUMPTIONS = [
    "BeamNG regula con un PID hacia absSlipRatioTarget + 2/v (wheels.lua updateABSCoef, > 5 m/s); AC es todo/nada por rueda "
    "(ABS de AC: freno × 0 si slip < −límite, > 20 km/h): el límite de AC hace de disparador, no de objetivo",
    "AC tiene un solo límite y una sola frecuencia: se pondera por el reparto de freno delantero y se toma el menor absHz",
    "el número de canales de [ABS] no está definido en AC (CHANNELS solo existe en [ABS_V2]); los coches oficiales de Kunos usan [ABS] sin CHANNELS",
    "ningún coche con ABS se ha probado en AC",
    ]
PEAK = (
    "el pico de frenada del neumático SCTM exportado está en deslizamiento 0,10–0,13 (Quail: 0,104 delante, 0,13 detrás, "
    "medido con src/ac/tyre_sctm); un límite de ~0,2 corta el freno DESPUÉS del pico; los coches de Kunos usan 0,11–0,13 y "
    "170–250 Hz. No se ajusta: se registra"
)


def attributes() -> list[dict]:
    return [
        {"schema": "beam2ac.attribute/1", "id": f"ac:electronics.ini/ABS/{k}", "game": "assettocorsa", "unit": u, "dtype": dt,
         "description": f"[ABS] {k} de electronics.ini", "doc": [DOC], "required_for_load": False}
        for k, u, dt, *_ in KEYS
    ]


def rule(k, unit, dtype, src, extra, why) -> S.Rule:
    d = {
        "schema": "beam2ac.rule/2", "id": f"abs.{k.lower()}", "version": 1, "target": f"ac:electronics.ini/ABS/{k}",
        "kind": "computed",
        "transform": {"id": "identity", "params": {}, "input_units": {"x": unit}, "output_unit": unit, "formula": ""},
        "scope": {"level": "family", "vehicle": "", "variant": "", "family": "automation_export", "conditions": []},
        "status": "provisional", "inputs": {"x": src}, "conditions": BASE + ([extra] if extra else []),
        "applies_when": [], "exceptions": [], "incompatible_with": [],
        "assumptions": ASSUMPTIONS + [why, "D-G250-ABS: equivalencia de comportamiento sin medir en AC"] + ([PEAK] if k == "SLIP_RATIO_LIMIT" else []), "open_issues": [],
        "source_doc": [{"type": "code", "path": "src/importers/beamng.py", "locator": "_import_abs_summary", "sha256": "", "note": ""}],
        "target_doc": [DOC], "evidence": [REPORT], "validations": [], "supersedes": "", "notes": "G2.50",
        "history": [{"date": "2026-11-15", "author": "claude (G2.50, encargada por el usuario)", "action": "create",
                     "reason": "ABS de BeamNG a electronics.ini [ABS]", "status_from": "", "status_to": "provisional"}],
    }
    return S.from_dict(d)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = [rule(k[0], k[1], k[2], k[3], k[4], k[5]) for k in KEYS]
    if ATTR_FILE.exists() or any((RULES / f"{r.key}.json").exists() for r in rs):
        raise SystemExit("ya existen")
    if a.dry_run:
        print("ok", [r.id for r in rs])
        return 0
    ATTR_FILE.write_text(json.dumps(attributes(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas y {len(KEYS)} atributos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
