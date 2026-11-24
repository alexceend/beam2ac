"""Capacidades de AC tras G2.20 (2026-11-07).

    python tools/catalog/update_capabilities_g220.py [--dry-run]

Solo describe qué representa AC (no cambia valores ni reglas):

* ``tyres.model`` → ``approximation`` cuando hay medida del banco de neumáticos asociada al
  origen (``tyres.axles.*.force_slip_samples`` presente): el modelo SCTM de AC (VERSION 10)
  ajustado a las curvas Fx/Fy medidas; se cuantifica el error del ajuste.
* nuevo ``tyres.model_unmeasured`` (``pending_research``) sin medida.
* nuevo ``tyres.relaxation`` (``pending_research``): la longitud de relajación no se midió.

Mismo procedimiento que ``update_capabilities_g219.py`` (archivo formateado a mano, CRLF):
se sustituye solo el bloque de cada entrada y se comprueba el JSON. Se niega si ya está
actualizado.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAP = ROOT / "catalog/capabilities/assettocorsa.json"
DOC = "docs/research/g220_tyre_grip_model_2026-11-07.md"

TYRE = {
    "id": "tyres.model",
    "level": "approximation",
    "when": [{"path": "tyres.axles.*.force_slip_samples", "op": "present"}],
    "reason": "G2.20: curvas Fx/Fy del neumático MEDIDAS en el juego (banco virtual, coche sin "
    "modificar, asociadas por la huella del origen) → modelo SCTM de AC (VERSION 10) "
    "ajustado por mínimos cuadrados; se pierden la relajación, la dinámica de la estructura "
    "y la caída fuera de CAMBER_GAIN; error del ajuste en los supuestos de cada clave",
    "evidence": [f"{DOC}", "output/g220_2026-11-07/validacion_neumatico.md"],
}
UNMEASURED = {
    "id": "tyres.model_unmeasured",
    "level": "pending_research",
    "when": [{"path": "tyres.axles.*.force_slip_samples", "op": "absent"}],
    "reason": "neumático de BeamNG por nodos y vigas (rozamiento por nodo en el C++: "
    "stage2.lua → obj:setNode) sin medida del banco para este origen: sin equivalencia; no "
    "se resuelve con curvas neutras ni estimaciones",
    "evidence": ["docs/research/porsche_fisica_completa_2026-10-26.md §3",
                 "docs/research/g219_desbloqueo_integral_200409.md §3", f"{DOC}"],
}
RELAX = {
    "id": "tyres.relaxation",
    "level": "pending_research",
    "when": [{"path": "tyres.axles.*.force_slip_samples", "op": "present"}],
    "reason": "G2.20: la longitud de relajación del neumático de BeamNG no se midió (barridos "
    "cuasiestáticos a 50 Hz); sin RELAXATION_LENGTH AC usa el deslizamiento instantáneo",
    "evidence": [f"{DOC}"],
}


def block(entry: dict, eol: str) -> str:
    """Una entrada en el estilo del archivo: una clave por línea, listas en línea."""
    lines = ["  {"]
    keys = list(entry)
    for i, k in enumerate(keys):
        val = json.dumps(entry[k], ensure_ascii=False, separators=(", ", ": "))
        lines.append(f'   "{k}": {val}' + ("," if i < len(keys) - 1 else ""))
    lines.append("  }")
    return eol.join(lines)


def span(raw: str, fid: str) -> tuple[int, int]:
    """Inicio de la línea «  {» de la entrada y fin de su «  }» (sin coma)."""
    i = raw.index(f'"id": "{fid}"')
    start = raw.rindex("  {", 0, i)
    end = raw.index("\n  }", i) + len("\n  }")
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    raw = CAP.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    data = json.loads(raw)
    if any(f["id"] == "tyres.model_unmeasured" for f in data["features"]):
        raise SystemExit("ya actualizado")
    want = copy.deepcopy(data)
    feats = want["features"]
    by = {f["id"]: i for i, f in enumerate(feats)}
    feats[by["tyres.model"]] = TYRE
    feats.insert(by["tyres.model"] + 1, UNMEASURED)
    feats.insert(by["tyres.model"] + 2, RELAX)
    s, e = span(raw, "tyres.model")
    text = raw[:s] + eol.join([block(TYRE, eol) + ",", block(UNMEASURED, eol) + ",",
                               block(RELAX, eol)]) + raw[e:]
    if json.loads(text) != want:
        raise SystemExit("el texto resultante no es el JSON esperado; no se escribe")
    if a.dry_run:
        print("ok (sin escribir)")
        return 0
    CAP.write_bytes(text.encode("utf-8"))
    print("escrito", CAP)
    return 0


if __name__ == "__main__":
    sys.exit(main())
