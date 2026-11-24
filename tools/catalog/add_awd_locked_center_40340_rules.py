"""Tracción total con central ``locked`` y ``camsoEngine`` del exportador 40340 (2026-11-16).

    python tools/catalog/add_awd_locked_center_40340_rules.py [--dry-run]

NO volver a ejecutar tras escribir las reglas (reglas inmutables, solo se añade).

Muestra: ``bisexual_aspiration`` (``<autor>_bisexual_aspiration.zip``, exportador 40340,
``camsoEngine``, caja ``manualGearbox`` + ``rangeBox`` + diferencial de transferencia
``locked`` + diferenciales de eje ``torqueVectoring``). Ninguna regla cubría esa
combinación: las ``*_lockc`` (D-G212-LOCK, G2.44) eran de 200409 + ``combustionEngine`` y las
AWD del 40340 (G2.30) exigen central ``open``/``lsd``.

Derivación = la de ``docs/research/g244_central_bloqueado_2026-11-14.md`` (mecánica de
``differential.lua`` de BeamNG, igual en todos los vehículos; la mitad de AC es el cálculo 4WD de AC):

* ``dt.traction_awd``, ``dt.awd_front_share`` (0,5 fijo de ``lockedUpdateTorque``) y las tres
  claves del central: clones de ``*_lockc`` con ``40340`` + ``camsoEngine``;
* ejes: clones de ``*_openmode`` ampliados a ``torqueVectoring``: ``differential.lua``
  l. 126-134 usa para ``open`` y ``torqueVectoring`` la MISMA actualización de diferencial
  abierto (el vector de par es un controlador externo, ``activeDiffBias``) ⇒ en AC,
  POWER = COAST = PRELOAD = 0 como un diferencial abierto. El control activo no se
  representa (pérdida de fidelidad declarada).

Todas ``provisional`` (una sola muestra, sin probar en AC).
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

DATE = "2026-11-16"
AUTHOR = "claude (conversión de bisexual_aspiration, encargada por el usuario)"
DOC = "docs/research/bisexual_aspiration_2026-11-16.md"
RULES_DIR = ROOT / "catalog" / "rules"
VERSION = "ir.formats.exporter_version"
ENGINE = "ir.engine.device_type"
ROLE_MODE = "ir.drivetrain.differentials.{}.initial_mode"
CENTER = [
    "dt.traction_awd_lockc@1",
    "dt.awd_front_share_lockc@1",
] + [f"dt.awd_center_diff_{k}_lockc@1" for k in ("power", "coast", "preload")]
AXLES = [
    f"dt.awd_{r}_diff_{k}_openmode@1"
    for r in ("front", "rear")
    for k in ("power", "coast", "preload")
]
ASSUMPTION = (
    "clon de la regla del 200409 para el exportador 40340 + camsoEngine: una sola muestra "
    "(bisexual_aspiration), sin probar en AC; la mecánica es la de differential.lua de BeamNG"
)
TV_ASSUMPTION = (
    "torqueVectoring = diferencial abierto + controlador externo (differential.lua l. 126-134); "
    "el reparto activo de par no se representa en AC"
)


def clone(key: str) -> S.Rule:
    d = json.loads((RULES_DIR / f"{key}.json").read_text("utf-8"))
    seen = set()
    for c in d["conditions"]:
        if c["fact"] == VERSION:
            assert c["value"] == ["200409"], (key, c)
            c["value"] = ["40340"]
            seen.add("v")
        elif c["fact"] == ENGINE:
            assert c["value"] == "combustionEngine", (key, c)
            c["value"] = "camsoEngine"
            seen.add("e")
        elif key in AXLES and c["fact"] in (
            ROLE_MODE.format("front"),
            ROLE_MODE.format("rear"),
        ):
            assert c["op"] == "eq" and c["value"] == "open", (key, c)
            c["op"], c["value"] = "in", ["open", "torqueVectoring"]
            seen.add("m")
    assert seen == ({"v", "e", "m"} if key in AXLES else {"v", "e"}), (key, seen)
    base = d["id"]
    d["supersedes"] = ""
    d.update(
        id=base + "_40340",
        version=1,
        status="provisional",
        assumptions=list(d.get("assumptions", []))
        + [ASSUMPTION]
        + ([TV_ASSUMPTION] if key in AXLES else []),
        history=[
            {
                "date": DATE,
                "author": AUTHOR,
                "action": "create",
                "reason": f"clon de {key} para 40340 + camsoEngine (bisexual_aspiration)",
                "status_from": "",
                "status_to": "provisional",
            }
        ],
        notes="bisexual_aspiration 2026-11-16",
    )
    d["evidence"] = list(d.get("evidence", [])) + [
        {"type": "report", "path": DOC, "locator": "§AWD", "sha256": "", "note": ""}
    ]
    return S.from_dict(d)


def rules() -> list[S.Rule]:
    return [clone(k) for k in CENTER + AXLES]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = rules()
    have = {p.stem for p in RULES_DIR.glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        write_rule(REPO_CATALOG, r)
    Catalog.load(REPO_CATALOG)
    print(f"escritas {len(rs)} reglas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
