"""Matriz de cobertura del conversor: estado de cada clave de AC en cada exportación del corpus local.

    python tools/analysis/coverage_matrix.py --out output/<nueva> [--driver-mass 0]

Resuelve (solo lectura, catálogo de producción) las exportaciones de ``sample_beamng/`` y ``samples_beamng/`` y
agrupa cada objetivo ``ac:<archivo>/<SECCIÓN>/<CLAVE>`` por subsistema y por clase de cobertura:

* ``medida``      regla o decisión con valor leído o medido en el origen (identidad, fórmula, banco);
* ``referencia``  valor de referencia que NO sale del origen (``kind = reference``: medianas del corpus de AC);
* ``formato``     requisito de formato de AC sin contenido físico;
* ``no_aplica``;  ``propuesta`` (regla de otro ámbito, sin aplicar);  ``sin_regla`` (nada la resuelve).

«Exporta sin errores» no es «físicamente correcto»: la clase dice de dónde sale el valor, no si está validado.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs.coverage import CLASSES, klass, subsystem  # noqa: E402
from src.attrs.report import resolve_vehicle  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_dir, preview_physics  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402


def corpus() -> list[tuple[str, Path, str]]:
    out = [
        ("seat_ibiza_acfr", ROOT / "sample_beamng/vehicles/seat_ibiza_acfr", "35b84.pc")
    ]
    for d in sorted((ROOT / "samples_beamng").glob("*/vehicles/*")):
        pcs = sorted(d.glob("*.pc"))
        if pcs:
            out.append((d.name, d, pcs[0].name))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--driver-mass", type=float, default=0.0)
    a = ap.parse_args(argv)
    out = ensure_safe_output_path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cat = Catalog.load(REPO_CATALOG)
    cells: dict[str, dict[str, str]] = defaultdict(dict)
    versions: dict[str, str] = {}
    for name, vd, pc in corpus():
        try:
            ctx, results = resolve_vehicle(
                preview_physics(load_vehicle_dir(vd, pc), a.driver_mass),
                cat,
                "ac_physics",
                a.driver_mass,
            )
        except Exception as e:  # una exportación ilegible no tumba la matriz
            versions[name] = f"error: {e!r}"[:80]
            continue
        f = ctx.facts.get("ir.formats.exporter_version")
        versions[name] = str(f.value) if f else "?"
        for r in results:
            cells[r.target][name] = klass(r, cat)
    cars = [n for n in versions if not versions[n].startswith("error")]
    (out / "cobertura.json").write_text(
        json.dumps({"versiones": versions, "celdas": cells}, indent=1), "utf-8"
    )
    by_sub: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for target, row in cells.items():
        for car in cars:
            by_sub[subsystem(target)][car][row.get(car, "sin_regla")] += 1
    order = CLASSES
    lines = [
        "# Cobertura por subsistema (claves de AC: " + " / ".join(order) + ")",
        "",
        "| Subsistema | "
        + " | ".join(f"{c[:18]} ({versions[c]})" for c in cars)
        + " |",
        "|---|" + "---|" * len(cars),
    ]
    for sub in sorted(by_sub):
        lines.append(
            f"| {sub} | "
            + " | ".join("/".join(str(by_sub[sub][c][k]) for k in order) for c in cars)
            + " |"
        )
    never = sorted(
        t
        for t, row in cells.items()
        if all(row.get(c) in ("sin_regla", None) for c in cars)
    )
    lines += ["", f"## Claves sin regla en ningún vehículo ({len(never)})", ""] + [
        f"- `{t}`" for t in never
    ]
    (out / "cobertura.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines[: 4 + len(by_sub)]))
    print(
        f"claves sin regla en ningún vehículo: {len(never)}; errores: "
        f"{ {k: v for k, v in versions.items() if v.startswith('error')} }"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
