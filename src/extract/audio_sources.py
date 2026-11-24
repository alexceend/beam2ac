"""Detección (solo lectura) del audio de una exportación de Automation a BeamNG.

Layout observado (ibiza_audio_reference.md): ``art/sound/engine/<hash>/*.wav`` y
``art/sound/blends/<hash>.sfxBlend2D.json`` con ``samples`` = series de ``[archivo, rpm]``
(la serie de ``*_P.wav`` es carga; la otra, retención). Nada se convierte aquí: el
resultado distingue *extraído* (WAV encontrados y coherentes con el blend) de lo que
exige un banco FMOD (convertido/configurado/instalado/escuchado: no demostrados).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from src.jbeam import lax_json


def _load(path: Path) -> dict[str, Any]:
    v = lax_json.loads(path.read_text(encoding="utf-8", errors="replace")).value
    return v if isinstance(v, dict) else {}


def scan_audio(vehicle_dir: Path | str) -> dict[str, Any]:
    """Inventario del audio de motor. Nunca escribe; ``present`` False si no hay blend."""
    root = Path(vehicle_dir)
    # ``art/sound`` cuelga de la raíz del mod, no de ``vehicles/<id>`` (que es lo que dan los llamadores)
    if not (root / "art" / "sound").is_dir() and root.parent.name == "vehicles":
        root = root.parent.parent
    out_root = root
    blends = sorted((root / "art" / "sound" / "blends").glob("*.sfxBlend2D.json"))
    out: dict[str, Any] = {
        "present": False,
        "root": str(out_root),
        "blends": [],
        "problems": [],
    }
    for b in blends:
        try:
            data = _load(b)
        except Exception as exc:  # JSON ilegible: se informa, no se inventa
            out["problems"].append(f"{b.name}: ilegible ({type(exc).__name__})")
            continue
        series: list[dict[str, Any]] = []
        missing: list[str] = []
        for i, serie in enumerate(data.get("samples") or []):
            pts = []
            for item in serie if isinstance(serie, list) else []:
                if not (isinstance(item, list) and len(item) == 2):
                    continue
                rel, rpm = item
                if not (root / str(rel)).is_file():
                    missing.append(str(rel))
                try:
                    if not math.isfinite(float(rpm)):
                        raise ValueError(rpm)
                    pts.append((str(rel), float(rpm)))
                except (TypeError, ValueError):
                    out["problems"].append(f"{b.name}: rpm no numérica en {rel}")
            if pts:
                rpms = [p[1] for p in pts]
                loaded = sum(Path(p[0]).stem.endswith("_P") for p in pts)
                series.append(
                    {
                        "index": i,
                        "samples": len(pts),
                        "points": pts,  # (ruta relativa a la raíz del mod, rpm del blend)
                        "rpm_min": min(rpms),
                        "rpm_max": max(rpms),
                        # nombre → papel, solo por el sufijo observado en Automation
                        "role": (
                            "load"
                            if loaded == len(pts)
                            else "coast" if loaded == 0 else "mixed"
                        ),
                        "kinds": sorted({Path(p[0]).stem.split("_")[0] for p in pts}),
                    }
                )
        out["blends"].append(
            {
                "file": b.name,
                "event": data.get("eventName"),
                "series": series,
                "missing_files": missing,
            }
        )
    out["present"] = any(b["series"] for b in out["blends"])
    return out


def audio_spec(
    scan: dict[str, Any], idle_rpm: float | None, limiter_rpm: float | None
) -> dict[str, Any]:
    """Entrada de ``fmodstudiocl -script`` derivada del inventario y del motor resuelto.

    Solo hechos: cobertura de rpm de las muestras frente a ralentí/limitador. No asigna
    sonidos a admisión/escape/transmisión (el origen solo trae el motor); esos eventos
    quedan silenciosos (audio_pipeline_for_future_cars.md §3-4).
    """
    warnings: list[str] = []
    blend = next((b for b in scan.get("blends", []) if b["series"]), None)
    if blend is None:
        return {"status": "sin_fuente", "warnings": ["sin muestras de motor"]}
    if blend["missing_files"]:
        warnings.append(f"{len(blend['missing_files'])} WAV del blend no existen")
    lo = min(s["rpm_min"] for s in blend["series"])
    hi = max(s["rpm_max"] for s in blend["series"])
    if limiter_rpm and hi < limiter_rpm:
        warnings.append(
            f"las muestras llegan a {hi:.0f} rpm, bajo el limitador ({limiter_rpm:.0f})"
        )
    if idle_rpm and lo > idle_rpm:
        warnings.append(
            f"las muestras empiezan en {lo:.0f} rpm, sobre el ralentí ({idle_rpm:.0f})"
        )
    return {
        "status": "extraido" if not blend["missing_files"] else "incompleto",
        "event": blend["event"],
        "rpm_range": [lo, hi],
        "series": [
            {k: v for k, v in x.items() if k != "points"} for x in blend["series"]
        ],
        "silent_events": "todos salvo engine_ext/engine_int",
        "warnings": warnings,
        "not_done": "conversión a banco, instalación y escucha: no realizadas",
    }
