"""Instantánea del resultado del lector JBeam sobre todos los JBeam disponibles (solo lectura).

    python tools/analysis/jbeam_parse_snapshot.py --out <archivo.json> [--zip-root DIR] [--mods DIR]

Para cada archivo guarda: ``ok`` + sha256 del valor leído (JSON canónico) + lista de
tolerancias (tipo, línea, columna, detalle), o ``error`` con el mensaje. Comparar dos
instantáneas (``--compare A B``) muestra qué archivos cambian de resultado.
Lee los zip en memoria (``src.jbeam.sources``); no extrae ni escribe nada fuera de ``--out``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.steam_paths import find_game  # noqa: E402
from src.jbeam.lax_json import JBeamSyntaxError, loads  # noqa: E402
from src.jbeam.sources import ZipSource  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402

BNG = (find_game("BeamNG.drive") or Path("BeamNG.drive")) / "content" / "vehicles"
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"
SAMPLE = ROOT / "sample_beamng"


def _entry(text: str, source: str) -> dict:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        doc = loads(text, source=source)
    except JBeamSyntaxError as exc:
        return {"error": f"{exc.line}:{exc.column}: {exc.message}"}
    canon = json.dumps(doc.value, sort_keys=True, ensure_ascii=False, default=str)
    return {
        "value_sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        "leniencies": [[n.kind, n.line, n.column, n.detail] for n in doc.leniencies],
    }


def snapshot(zip_root: Path, mods: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    zips = sorted(zip_root.glob("*.zip")) + sorted(mods.rglob("*.zip"))
    for z in zips:
        for member, data in ZipSource(z).read_all(".jbeam"):
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                out[str(member)] = {"error": f"utf-8: {exc.reason}"}
                continue
            out[str(member)] = _entry(text, str(member))
    for path in sorted(SAMPLE.rglob("*.jbeam")):
        key = str(path.relative_to(ROOT))
        out[key] = _entry(path.read_text(encoding="utf-8"), key)
    return out


def compare(a: dict, b: dict) -> dict:
    changed_ok = [k for k in a if "error" not in a[k] and b.get(k) != a[k]]
    fixed = [k for k in a if "error" in a[k] and k in b and "error" not in b[k]]
    still = [k for k in a if "error" in a[k] and "error" in b.get(k, {})]
    new_errors = [k for k in a if "error" not in a[k] and "error" in b.get(k, {})]
    return {
        "files_a": len(a),
        "files_b": len(b),
        "errors_a": sum("error" in v for v in a.values()),
        "errors_b": sum("error" in v for v in b.values()),
        "previously_ok_changed": changed_ok,
        "fixed": fixed,
        "still_failing": {k: b[k]["error"] for k in still},
        "new_errors": new_errors,
        "missing_in_b": [k for k in a if k not in b],
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--zip-root", type=Path, default=BNG)
    ap.add_argument("--mods", type=Path, default=MODS)
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"))
    args = ap.parse_args(argv)
    if args.compare:
        a, b = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        result = compare(a, b)
        print(json.dumps(result, indent=1, ensure_ascii=False))
        return (
            0 if not result["previously_ok_changed"] and not result["new_errors"] else 1
        )
    if not args.out:
        ap.error("--out o --compare")
    data = snapshot(args.zip_root, args.mods)
    target = ensure_safe_output_path(args.out)
    if target.exists():
        raise SystemExit(f"ya existe {target}; no se sobrescribe")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=0, ensure_ascii=False), encoding="utf-8")
    print(
        f"{len(data)} archivos, {sum('error' in v for v in data.values())} con error -> {target}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
