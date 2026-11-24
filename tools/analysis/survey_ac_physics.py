"""Encuesta de solo lectura de la física de los coches de AC con ``data/`` desempaquetada.

    python tools/analysis/survey_ac_physics.py --ac-cars <AC>/content/cars --out output/<nueva>

Para cada coche (``data.acd`` no se lee) recoge, leyendo como AC (``read_ini_ac``):

* suspensión: ``[FRONT]/[REAR] TYPE`` y las claves de cada tipo; ``CG_LOCATION``,
  ``WHEELBASE``;
* transmisión: ``TRACTION TYPE``;
* frenos: ``MAX_TORQUE``, ``FRONT_SHARE``, ``HANDBRAKE_TORQUE``, la masa (``TOTALMASS``) y el
  radio de las ruedas, para la deceleración que permitiría el par de freno con la
  convención de AC (``2·MAX_TORQUE/(r·m)``);
* motor: pico de ``power.lut`` frente a ``ui_car.json`` (par y potencia), separación en rpm,
  ``LIMITER``;
* aerodinámica: secciones de ``aero.ini``, claves y tablas de cada ala.

Origen de cada coche: ``src.catalog.ac_refs.classify_origin`` (heurística por prefijo y
autor). Se guarda el sha256 de cada INI para no contar dos veces la física copiada entre
mods. Solo se usa como evidencia de FORMATO y CONVENCIONES.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.car_validate import read_ini_ac  # noqa: E402
from src.catalog.ac_refs import classify_origin  # noqa: E402
from src.safe_paths import new_output_dir  # noqa: E402

G = 9.80665


def f(ini, sec: str, key: str) -> float | None:
    try:
        return float(ini.sections[sec][key].split(";")[0].split(",")[0])
    except (KeyError, ValueError, AttributeError):
        return None


def lut(path: Path) -> list[tuple[float, float]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split(";")[0].strip()
        if "|" in line:
            try:
                x, y = line.split("|")[:2]
                rows.append((float(x), float(y)))
            except ValueError:
                pass
    return rows


def num(text: Any) -> float | None:
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def survey_car(car: Path) -> dict[str, Any]:
    data = car / "data"
    ini = {p.name: read_ini_ac(p) for p in data.glob("*.ini")}
    ui = {}
    if (car / "ui" / "ui_car.json").is_file():
        try:
            ui = json.loads(
                (car / "ui" / "ui_car.json").read_text(
                    encoding="utf-8", errors="replace"
                ),
                strict=False,
            )
        except json.JSONDecodeError:
            ui = {}
    author = str(ui.get("author", ""))
    origin = classify_origin(car.name, ui if isinstance(ui, dict) else {})["class"]
    s, d, b, e, c, t, a = (
        ini.get(n)
        for n in (
            "suspensions.ini",
            "drivetrain.ini",
            "brakes.ini",
            "engine.ini",
            "car.ini",
            "tyres.ini",
            "aero.ini",
        )
    )
    out: dict[str, Any] = {"car": car.name, "origin": origin, "author": author}
    out["sha"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        for p in sorted(data.glob("*.ini"))
    }
    if s:
        out["front_type"] = s.sections.get("FRONT", {}).get("TYPE")
        out["rear_type"] = s.sections.get("REAR", {}).get("TYPE")
        out["rear_keys"] = sorted(s.sections.get("REAR", {}))
        out["cg_location"] = f(s, "BASIC", "CG_LOCATION")
        out["wheelbase"] = f(s, "BASIC", "WHEELBASE")
    if d:
        out["traction"] = d.sections.get("TRACTION", {}).get("TYPE")
    if b:
        out["max_torque"] = f(b, "DATA", "MAX_TORQUE")
        out["front_share"] = f(b, "DATA", "FRONT_SHARE")
        out["handbrake"] = f(b, "DATA", "HANDBRAKE_TORQUE")
    mass = f(c, "BASIC", "TOTALMASS") if c else None
    out["totalmass"] = mass
    rf = f(t, "FRONT", "RADIUS") if t else None
    rr = f(t, "REAR", "RADIUS") if t else None
    out["radius_front"], out["radius_rear"] = rf, rr
    mt, fs = out.get("max_torque"), out.get("front_share")
    if mt and fs is not None and rf and rr and mass:
        # frenos de AC: cada rueda delantera MAX_TORQUE·bias, cada trasera
        # (1−bias)·MAX_TORQUE ⇒ fuerza total = 2·MAX_TORQUE·(bias/rf + (1−bias)/rr)
        force = 2 * mt * (fs / rf + (1 - fs) / rr)
        out["brake_decel_g"] = round(force / (mass * G), 3)
    if e:
        out["limiter"] = f(e, "ENGINE_DATA", "LIMITER")
        pl = lut(
            data / (e.sections.get("HEADER", {}).get("POWER_CURVE") or "power.lut")
        )
        if pl:
            peak_t = max(pl, key=lambda r: r[1])
            peak_p = max(pl, key=lambda r: r[0] * r[1])
            steps = sorted({round(b2[0] - a2[0], 3) for a2, b2 in zip(pl, pl[1:])})
            out["lut_peak_torque"] = peak_t
            out["lut_peak_power_hp"] = round(
                peak_p[0] * peak_p[1] * 2 * math.pi / 60 / 745.69987158227, 1
            )
            out["lut_rpm_steps"] = steps[:6]
            out["lut_rows"] = len(pl)
    specs = ui.get("specs", {}) if isinstance(ui, dict) else {}
    out["ui_bhp"], out["ui_torque"] = num(specs.get("bhp")), num(specs.get("torque"))
    out["ui_weight"] = num(specs.get("weight"))
    if a:
        out["aero_sections"] = sorted(a.sections)
        wings = {}
        for sec, keys in a.sections.items():
            if sec.startswith(("WING_", "FIN_")):
                w = {k: keys[k] for k in sorted(keys)}
                for k in ("LUT_AOA_CL", "LUT_AOA_CD", "LUT_GH_CL", "LUT_GH_CD"):
                    if k in keys:
                        rows = lut(data / keys[k].split(";")[0].strip())
                        w[k + "_rows"] = rows[:40]
                wings[sec] = w
        out["aero"] = wings
    return out


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ac-cars", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    cars = sorted(
        p
        for p in a.ac_cars.iterdir()
        if (p / "data").is_dir() and not p.name.startswith("beam2ac_")
    )
    rows = [survey_car(c) for c in cars]
    summary = {
        "cars": len(rows),
        "origin": dict(Counter(r["origin"] for r in rows)),
        "front_type": dict(Counter(r.get("front_type") for r in rows)),
        "rear_type": dict(Counter(r.get("rear_type") for r in rows)),
    }
    out = new_output_dir(a.out)
    (out / "survey.json").write_text(
        json.dumps({"summary": summary, "cars": rows}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
