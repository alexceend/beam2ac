"""Del inventario de WAV (``scan_audio``) al ``spec`` que consume el generador de bancos.

Detección (``src.extract.audio_sources``) → validación (``wavinfo``) → asignación de eventos
(aquí) → generación del banco (``engine_gen``). Todo lo de este módulo es puro.

Diseño del motor = el de los eventos ``engine_ext``/``engine_int`` del SDK de AC
(``docs/research/audio_pipeline_for_future_cars.md``): dos pistas (carga / retención) con
un sonido en bucle por muestra, colocado en el parámetro ``rpms`` con ``AutopitchModulator``
(raíz = su rpm) y fundido cruzado con los vecinos; volumen de pista según ``throttle``.
Constantes de mezcla, GUID de carpetas/grupos y los 17 eventos secundarios silenciosos:
``data/template_spec.json`` (extraídos de la plantilla del SDK en la fase 2 del Ibiza).

Límites explícitos: sin muestras propias del limitador, cambios, turbo, derrape, viento… ⇒
eventos silenciosos con el nombre exacto; sin nivelado: la diferencia de volumen entre
carga y retención es señal y no se toca.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from src.audio.wavinfo import wav_info

DATA = Path(__file__).parent / "data"
FADE_FRACTION = (
    0.5  # anchura del fundido / separación entre muestras (plantilla ≈ 400/750)
)
MAX_GAP_RATIO = 1.5  # entre muestras vecinas: más allá se avisa de hueco
RPM_HEADROOM = 2.0  # rpms_max del evento = 2·limitador (AC Audio Pipeline 1.9, p. 6)


def template() -> dict[str, Any]:
    return json.loads((DATA / "template_spec.json").read_text(encoding="utf-8"))


def placement(rpms: list[float], top: float) -> list[dict[str, Any]]:
    """Por muestra: inicio, longitud y fundidos (a, b) en rpm. La frontera con la vecina es la
    media geométrica; el fundido mide ``FADE_FRACTION`` de la separación."""
    rpms = sorted(rpms)
    out = []
    for i, r in enumerate(rpms):
        fi = fo = None
        if i:
            b = math.sqrt(rpms[i - 1] * r)
            h = FADE_FRACTION * (r - rpms[i - 1]) / 2
            fi = (b - h, b + h)
        if i < len(rpms) - 1:
            b = math.sqrt(r * rpms[i + 1])
            h = FADE_FRACTION * (rpms[i + 1] - r) / 2
            fo = (b - h, b + h)
        start = fi[0] if fi else 0.0
        end = fo[1] if fo else float(top)
        out.append(
            {
                "rpm": r,
                "start": start,
                "length": end - start,
                "fade_in": fi,
                "fade_out": fo,
            }
        )
    return out


def _usable(
    series: dict[str, Any], root: Path, cache: dict[str, dict]
) -> tuple[list, list]:
    """(muestras utilizables ordenadas por rpm, descartadas con motivo)."""
    good, dropped, seen = [], [], set()
    for rel, rpm in sorted(series["points"], key=lambda p: p[1]):
        path = root / rel
        info = cache.setdefault(str(path), wav_info(path))
        if rpm <= 0 or rpm != rpm:
            dropped.append({"file": rel, "rpm": rpm, "reason": "rpm no válida"})
        elif not info["ok"]:
            dropped.append(
                {"file": rel, "rpm": rpm, "reason": "; ".join(info["problems"])}
            )
        elif rpm in seen:
            dropped.append(
                {"file": rel, "rpm": rpm, "reason": "rpm repetida en la serie"}
            )
        else:
            seen.add(rpm)
            good.append((str(path).replace("\\", "/"), rpm))
    return good, dropped


def build_plan(
    scan: dict[str, Any],
    car_id: str,
    idle_rpm: float | None = None,
    limiter_rpm: float | None = None,
) -> dict[str, Any]:
    """Valida los WAV y asigna las series a las pistas. Devuelve ``{status, spec?, ...}``.

    ``status``: ``sin_fuente`` | ``insuficiente`` | ``listo`` | ``listo_con_fallback``.
    Un ``listo`` es solo «hay con qué construir»: ni proyecto, ni banco, ni escucha.
    """
    blend = next((b for b in scan.get("blends", []) if b["series"]), None)
    if blend is None:
        return {
            "status": "sin_fuente",
            "warnings": ["sin muestras de motor"],
            "dropped": [],
        }
    root = Path(scan["root"])
    cache: dict[str, dict] = {}
    by_role: dict[str, list] = {"load": [], "coast": []}
    dropped: list[dict[str, Any]] = []
    warnings: list[str] = []
    for s in blend["series"]:
        if s["role"] == "mixed":
            warnings.append(f"serie {s['index']} mezcla carga y retención: no se usa")
            continue
        good, bad = _usable(s, root, cache)
        dropped += bad
        by_role[s["role"]].append(good)
    if blend.get("missing_files"):
        warnings.append(f"{len(blend['missing_files'])} WAV del blend no existen")
    extra = sum(len(v) - 1 for v in by_role.values() if len(v) > 1)
    if extra:
        warnings.append(
            f"{extra} serie(s) repetida(s) de un mismo papel: se usa la primera"
        )
    chosen = {
        r: (max(v, key=len) if v else []) for r, v in by_role.items()
    }  # la más completa
    chosen = {r: c for r, c in chosen.items() if c}
    result: dict[str, Any] = {
        "event": blend.get("event"),
        "dropped": dropped,
        "wav": {
            Path(k).as_posix(): {x: v[x] for x in v if x != "file"}
            for k, v in cache.items()
        },
    }
    dup: dict[str, list[str]] = {}
    for k, v in result["wav"].items():
        dup.setdefault(v.get("sha256", k), []).append(k)
    same = [v for v in dup.values() if len(v) > 1]
    if same:
        warnings.append(f"{len(same)} grupo(s) de WAV idénticos (mismo sha256)")
    if not chosen:
        return {
            **result,
            "status": "insuficiente",
            "warnings": warnings + ["ninguna serie utilizable"],
        }
    fallback = None
    if set(chosen) != {"load", "coast"}:
        have = next(iter(chosen))
        fallback = (
            f"solo hay serie de {'carga' if have == 'load' else 'retención'}: se reutiliza en la "
            "pista que falta (con su curva de throttle); el volumen entre acelerar y soltar no se diferencia"
        )
        chosen = {"load": chosen[have], "coast": chosen[have]}
        warnings.append(fallback)
    lo = min(c[0][1] for c in chosen.values())
    hi = max(c[-1][1] for c in chosen.values())
    if limiter_rpm and hi < limiter_rpm:
        warnings.append(
            f"las muestras llegan a {hi:.0f} rpm, bajo el limitador ({limiter_rpm:.0f}): se extienden por pitch"
        )
    if idle_rpm and lo > idle_rpm:
        warnings.append(
            f"las muestras empiezan en {lo:.0f} rpm, sobre el ralentí ({idle_rpm:.0f})"
        )
    for role, c in chosen.items():
        r = [x[1] for x in c]
        gaps = [(a, b) for a, b in zip(r, r[1:]) if b / a > MAX_GAP_RATIO]
        if gaps:
            warnings.append(f"serie {role}: hueco(s) de rpm sin muestra {gaps[:3]}")
    tpl = template()
    top_ext = max(20000.0, RPM_HEADROOM * (limiter_rpm or 0))
    tops = {"engine_ext": top_ext, "engine_int": 0.75 * top_ext}
    engine = copy.deepcopy(tpl["engine"])
    for en, ev in engine.items():
        ev["rpms_max"] = tops[en]
        for tr in ev["tracks"]:
            role = "load" if tr["name"].startswith("load") else "coast"
            keep = [(f, r) for f, r in chosen[role] if r <= tops[en]]
            pl = placement([r for _, r in keep], tops[en])
            for p, (f, _) in zip(pl, keep):
                p["file"] = f
            tr["name"] = f"{car_id} {tr['name']}"[:60]
            tr["sounds"] = pl
    files = sorted(
        {
            f
            for ev in engine.values()
            for tr in ev["tracks"]
            for p in tr["sounds"]
            for f in [p["file"]]
        }
    )
    spec = {
        "car": car_id,
        "cars_folder": tpl["cars_folder"],
        "bank_folder": tpl["bank_folder"],
        "fade_shape": tpl["fade_shape"],
        "engine": engine,
        "secondary": tpl["secondary"],
        "audio_files": files,
    }
    return {
        **result,
        "status": "listo_con_fallback" if fallback else "listo",
        "rpm_range": [lo, hi],
        "series_used": {r: len(c) for r, c in chosen.items()},
        "warnings": warnings,
        "spec": spec,
    }
