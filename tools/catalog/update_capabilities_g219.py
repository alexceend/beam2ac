"""Capacidades de AC tras G2.19 (2026-11-06).

    python tools/catalog/update_capabilities_g219.py [--dry-run]

Solo describe qué representa AC (no cambia valores ni reglas):

* ``aero.triangles`` → ``approximation`` cuando hay medida del banco asociada al origen
  (``aero.cda_vs_aoa`` presente): 3 alas de AC reproducen resultante y cabeceo medidos;
  se pierden guiñada, efecto suelo y el arrastre de las ruedas girando. Nuevo
  ``aero.triangles_unmeasured`` (``pending_research``) sin medida.
* ``suspension.strut_rail`` → ``approximation`` con ``linkage = strut_rail`` (misma
  cinemática; tope de rigidez fija y palanca en reposo). Nuevo
  ``suspension.underconstrained_linkage`` (``pending_research``) para el resto.
* ``tyres.model``: sigue ``pending_research``; se añade el motivo de G2.19.

El archivo está formateado a mano (objetos en línea, saltos CRLF): se sustituye solo el
bloque de cada entrada, en el mismo estilo, y se comprueba que el resultado se lee y es
exactamente el JSON esperado. Se niega si ya está actualizado.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAP = ROOT / "catalog/capabilities/assettocorsa.json"
DOC = "docs/research/g219_desbloqueo_integral_200409.md"

AERO = {
    "id": "aero.triangles",
    "level": "approximation",
    "when": [
        {"path": "aero.model", "op": "eq", "value": "triangles"},
        {"path": "aero.cda_vs_aoa", "op": "present"},
    ],
    "reason": "G2.19: aerodinámica por triángulos MEDIDA en el juego (banco de G2.15, "
    "asociada por la huella del origen) → 3 alas de AC (arrastre en el eje trasero, carga "
    "en cada eje) con LUT por ángulo de ataque: resultante y cabeceo exactos a 50 m/s; se "
    "pierden la guiñada (CdA ≤ 2,4 %, ClA ≤ 5,1 % a ±10°), el efecto suelo y el arrastre "
    "de las ruedas girando",
    "evidence": [f"{DOC} §4", "output/g219_2026-11-06/aero/validacion_aero.md"],
}
AERO_UNMEASURED = {
    "id": "aero.triangles_unmeasured",
    "level": "pending_research",
    "when": [
        {"path": "aero.model", "op": "eq", "value": "triangles"},
        {"path": "aero.cda_vs_aoa", "op": "absent"},
    ],
    "reason": "G2.15/G2.19: aerodinámica por triángulos sin medida del banco para este "
    "origen: el C++ del juego no tiene fórmula reconstruida",
    "evidence": [f"{DOC} §4", "docs/research/g215_direction_brakes_aero_2026-11-03.md §5"],
}
STRUT = {
    "id": "suspension.strut_rail",
    "level": "approximation",
    "when": [{"path": "suspension.axles.*.linkage", "op": "eq", "value": "strut_rail"}],
    "reason": "G2.19: puntal en raíl (nodo deslizante con fixToRail) = TYPE=STRUT de AC "
    "(suspensión de puntal de AC): misma cinemática (≤ 3,5·10⁻⁴° en ±60 mm); muelle y "
    "amortiguador llevados al eje del puntal con su palanca EN REPOSO; tope de AC de "
    "rigidez fija 500 000 N/m (solo el punto de contacto es equivalente)",
    "evidence": [f"{DOC} §8", "output/g219_2026-11-06/puntal/validacion_puntal.md"],
}
UNDER = {
    "id": "suspension.underconstrained_linkage",
    "level": "pending_research",
    "when": [
        {"path": "suspension.axles.*.linkage", "op": "eq", "value": "underconstrained"}
    ],
    "reason": "G2.16/G2.19: las barras rígidas no guían la mangueta y no hay un raíl que "
    "la complete como puntal: sin equivalente",
    "evidence": ["docs/research/g216_suspension_steering_2026-11-04.md", f"{DOC} §8"],
}
TYRE_EXTRA = (
    "; G2.19: el rozamiento por nodo lo calcula el C++ (stage2.lua → obj:setNode); solo "
    "dimensiones y presión tienen regla (200409)"
)


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
    if any(f["id"] == "aero.triangles_unmeasured" for f in data["features"]):
        raise SystemExit("ya actualizado")
    # JSON esperado
    want = copy.deepcopy(data)
    feats = want["features"]
    by = {f["id"]: i for i, f in enumerate(feats)}
    tyre = dict(feats[by["tyres.model"]])
    tyre["reason"] += TYRE_EXTRA
    tyre["evidence"] = tyre["evidence"] + [f"{DOC} §3"]
    feats[by["tyres.model"]] = tyre
    feats[by["aero.triangles"]] = AERO
    feats[by["suspension.strut_rail"]] = STRUT
    feats.insert(by["aero.triangles"] + 1, AERO_UNMEASURED)
    feats.insert([f["id"] for f in feats].index("suspension.strut_rail") + 1, UNDER)
    # texto: de abajo arriba para no mover los índices
    edits = [
        ("suspension.strut_rail", block(STRUT, eol) + "," + eol + block(UNDER, eol)),
        ("aero.triangles", block(AERO, eol) + "," + eol + block(AERO_UNMEASURED, eol)),
        ("tyres.model", block(tyre, eol)),
    ]
    text = raw
    for fid, new in sorted(edits, key=lambda e: -span(raw, e[0])[0]):
        s, e = span(text, fid)
        text = text[:s] + new + text[e:]
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
