"""Datos de frenos, diferencial, embrague y caja de los vehículos (solo lectura).

    python tools/analysis/brake_diff_vehicle_survey.py --out output/<nueva>

Para cada vehículo distingue lo DECLARADO en el JBeam (tal cual, ``declared``), lo
DERIVADO por los extractores del proyecto (``derived``) y lo AUSENTE (``missing``,
claves que el Lua del juego sustituye por un valor por defecto). No escribe física.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.extract.brakes_tyres import build_brakes_tyres, front_share  # noqa: E402
from src.extract.drivetrain import read_devices  # noqa: E402
from src.jbeam import PartIndex, load_pc, resolve  # noqa: E402
from src.pipeline.physics_preview import load_vehicle_zip  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

VEH = Path("G:/SteamLibrary/steamapps/common/BeamNG.drive/content/vehicles")
MODS = Path.home() / "AppData/Local/BeamNG/BeamNG.drive/current/mods"

# Claves que el Lua del juego lee con valor por defecto (fuente: línea citada)
DIFF_DEFAULTS = {  # differential.lua
    "diffType": ("open", "l. 325"),
    "lsdPreload": (50, "l. 333"),
    "lsdLockCoef": (0.2, "l. 334"),
    "lsdRevLockCoef": ("= lsdLockCoef", "l. 335"),
    "lockTorque": (500, "l. 346"),
    "lockSpring": (
        "auto: 250²·min(I_salidas) si faltan lockSpring y lockTorque",
        "l. 234-237, 353",
    ),
    "lockDampRatio": (0.1, "l. 348"),
    "diffTorqueSplit": (0.5, "l. 315"),
    "speedLimitCoef": ("1 (·2e-7)", "l. 301"),
}
CLUTCH_DEFAULTS = {  # frictionClutch.lua
    "lockTorque": ("1,25·maxTorque + maxRPM·inercia·π/30", "l. 174"),
    "clutchFreePlay": (0.125, "l. 272"),
    "lockSpring": ("250²·I·lockSpringCoef", "l. 190"),
}
BRAKE_DEFAULTS = {  # wheels.lua
    "brakeInputSplit": (1, "l. 1064"),
    "brakeSplitCoef": (1, "l. 1065"),
    "parkingTorque": (0, "l. 1051"),
    "enableABS": (False, "l. 1046"),
    "absSlipRatioTarget": (0.18, "l. 1038"),
    "brakePressureInDelay": (0.04, "l. 1083"),
}


def _plain(v: Any) -> Any:
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return str(v)


def survey(name: str, resolved) -> dict[str, Any]:
    out: dict[str, Any] = {"vehicle": name, "errors": []}
    try:
        bt = build_brakes_tyres(resolved)
        wheels = []
        for b in bt.brakes:
            raw = _plain(b.raw)
            wheels.append(
                {
                    "wheel": b.wheel,
                    "declared": {
                        "brakeTorque": b.rate,
                        "brakeInputSplit": b.input_split,
                        "brakeSplitCoef": b.split_coef,
                        "parkingTorque": b.parking_torque,
                        **{
                            k: raw.get(k)
                            for k in (
                                "enableABS",
                                "enableABSactuator",
                                "absSlipRatioTarget",
                                "enableBrakeThermals",
                                "padMaterial",
                                "brakeType",
                                "brakeDiameter",
                                "brakeMass",
                            )
                            if k in raw
                        },
                    },
                    "derived": {"max_torque_pedal_1": b.max_torque},
                    "missing": [
                        k
                        for k in BRAKE_DEFAULTS
                        if k not in raw
                        and not (k == "brakeInputSplit" and b.input_split is not None)
                        and not (k == "brakeSplitCoef" and b.split_coef is not None)
                        and not (k == "parkingTorque" and b.parking_torque is not None)
                    ],
                }
            )
        out["brakes"] = wheels
        front = [b for b in bt.brakes if b.wheel.startswith("F")]
        rear = [b for b in bt.brakes if b.wheel.startswith("R")]
        try:
            out["brake_front_share_pedal_1"] = front_share(front, rear, 1.0)
            out["brake_front_share_pedal_0_3"] = front_share(front, rear, 0.3)
        except Exception as exc:  # noqa: BLE001 - se registra
            out["errors"].append(f"front_share: {exc}")
        out["brake_issues"] = bt.issues
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"frenos: {type(exc).__name__}: {exc}")
    try:
        devices = read_devices(resolved)
        devs = {}
        for dname, d in devices.items():
            params = _plain(getattr(d, "params", {}))
            entry = {"type": d.type, "declared": params}
            if d.type == "differential":
                entry["missing_with_default"] = {
                    k: v for k, v in DIFF_DEFAULTS.items() if k not in params
                }
            if d.type == "frictionClutch":
                entry["missing_with_default"] = {
                    k: v for k, v in CLUTCH_DEFAULTS.items() if k not in params
                }
            devs[dname] = entry
        out["powertrain"] = devs
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"powertrain: {type(exc).__name__}: {exc}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    res = []
    d = Path("sample_beamng/vehicles/seat_ibiza_acfr")
    res.append(
        survey(
            "seat_ibiza_acfr/35b84",
            resolve(PartIndex.from_directories(d), load_pc(d / "35b84.pc")),
        )
    )
    for z in (
        "spicy_mandala",
        "composed_fox",
        "rune_susui_espol_vtec",
        "porsche_gt3_rs",
    ):
        s = load_vehicle_zip(MODS / f"{z}.zip", VEH / "common.zip")
        res.append(survey(f"{z}/{s.variant}", resolve(s.index, s.pc)))
    out = new_output_dir(args.out)
    ensure_safe_output_path(out / "vehicles.json").write_text(
        json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(res, indent=1, ensure_ascii=False, default=str)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
