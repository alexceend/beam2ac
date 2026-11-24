"""G2.33: variante EXPERIMENTAL de un paquete exportado con ``[COAST_SETTINGS]`` (no toca el original).

    python tools/analysis/coast_settings_variant.py --package <.../exports/NNN/<car_id>> --ir <ir.json> \
        --out output/<carpeta_nueva> [--max-offset 0.10]

Copia el paquete a ``--out/<car_id>/`` (carpeta NUEVA; ``safe_paths``), cambia SOLO ``engine.ini``
(``[COAST_REF] TORQUE`` y la sección ``[COAST_SETTINGS]``) y añade ``data/coast_settings.lut``. ``variant.json``
guarda los hashes del original, el ajuste y las predicciones del modelo (``src/ac/coast_settings.py``) para
compararlas con una sesión de AC. NO es una exportación del flujo (sin sello ni validación del exportador) y no
está probada en AC. Sin variante (``o = 0`` óptimo) no escribe nada.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac import coast_settings as CS  # noqa: E402
from src.ac.coast_ref import coast_ref_least_squares  # noqa: E402
from src.project.resolved_ini import parse_lut  # noqa: E402
from src.safe_paths import new_output_dir  # noqa: E402


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _ini_value(text: str, section: str, key: str) -> str:
    sec = re.search(rf"^\[{section}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    m = re.search(rf"^{key}\s*=\s*([^\s;]+)", sec.group(1), re.M) if sec else None
    if not m:
        raise SystemExit(f"engine.ini sin [{section}] {key}")
    return m.group(1)


def build(package: Path, ir_path: Path, max_offset: float) -> dict | None:
    data = package / "data"
    eng = (data / "engine.ini").read_text("utf-8")
    if "[COAST_SETTINGS]" in eng:
        raise SystemExit("el paquete ya tiene [COAST_SETTINGS]")
    minimum = float(_ini_value(eng, "ENGINE_DATA", "MINIMUM"))
    inertia = float(_ini_value(eng, "ENGINE_DATA", "INERTIA"))
    power = parse_lut((data / "power.lut").read_text("utf-8"))
    prm = json.loads(ir_path.read_text("utf-8"))["params"]
    coast = [tuple(p) for p in prm["engine.coast_curve"]["value"]]
    ceiling = float(prm["engine.speed_ceiling"]["value"])
    if abs(ceiling - float(_ini_value(eng, "ENGINE_DATA", "LIMITER"))) > 0.5:
        raise SystemExit(
            "techo del IR distinto del LIMITER del paquete: ¿IR de otro coche?"
        )
    fit = CS.fit_coast_settings(coast, power, minimum, ceiling, max_offset)
    if fit is None:
        return None
    base = coast_ref_least_squares(coast, ceiling, int(minimum))
    entry = minimum + fit.activation_rpm
    ref_t = float(_ini_value(eng, "COAST_REF", "TORQUE"))
    s_base = ref_t / (float(_ini_value(eng, "COAST_REF", "RPM")) - minimum)
    r1 = minimum + 300
    pred = {
        "decay_s_to_MINplus300": {
            "BeamNG": CS.decay_time(
                lambda r: CS.interp(coast, r), inertia, ceiling, r1
            ),
            "paquete_original": CS.decay_time(
                lambda r: s_base * (r - minimum), inertia, ceiling, r1
            ),
            "variante": CS.decay_time(
                lambda r: CS.coast_brake(
                    r, fit.slope, fit.offset, minimum, entry, power
                ),
                inertia,
                ceiling,
                r1,
            ),
        },
        "brake_Nm": {
            str(int(r)): {
                "BeamNG": CS.interp(coast, r),
                "original": s_base * (r - minimum),
                "variante": CS.coast_brake(
                    r, fit.slope, fit.offset, minimum, entry, power
                ),
            }
            for r in (minimum + 100, minimum + 1000, (minimum + ceiling) / 2, ceiling)
        },
        "pedal_remap": "p -> p + g(rpm)*(1-p); g(ceiling) = %.3f (g=0 en MINIMUM)"
        % fit.offset,
        "rms_baseline_LS": base.rms_error if base else None,
    }
    return {
        "fit": fit.__dict__,
        "prediction": pred,
        "baseline_torque": ref_t,
        "max_offset": max_offset,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--ir", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--max-offset", type=float, default=0.10)
    a = ap.parse_args()
    res = build(a.package, a.ir, a.max_offset)
    if res is None:
        print(
            "sin variante: con o = 0 el error ya es mínimo (o la ley no es aplicable)"
        )
        return 0
    fit = CS.CoastSettingsFit(**res["fit"])
    out = new_output_dir(a.out) / a.package.name
    shutil.copytree(a.package, out)
    eng_p = out / "data" / "engine.ini"
    eng = eng_p.read_text("utf-8")
    eng = re.sub(
        r"^(TORQUE\s*=\s*)[^\s;]+",
        lambda m: f"{m.group(1)}{fit.ref_torque:g}",
        eng,
        count=1,
        flags=re.M,
    )
    eng = (
        eng.rstrip("\n")
        + "\n\n; G2.33 EXPERIMENTAL (src/ac/coast_settings.py)\n"
        + CS.render_engine_ini_sections(fit)
    )
    eng_p.write_text(eng, "utf-8", newline="\n")
    (out / "data" / "coast_settings.lut").write_text(
        CS.render_lut(fit.offset), "utf-8", newline="\n"
    )
    res["parent"] = {
        "package": str(a.package),
        "engine.ini_sha256": _sha(a.package / "data" / "engine.ini"),
    }
    res["variant_engine_ini_sha256"] = _sha(eng_p)
    (out.parent / "variant.json").write_text(json.dumps(res, indent=1), "utf-8")
    print(json.dumps(res["prediction"]["decay_s_to_MINplus300"]), fit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
