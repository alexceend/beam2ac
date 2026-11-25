"""Construye el ESQUELETO de banco de motor que usa ``src.audio.engine_gen`` (sin FMOD en uso).

Requiere (solo para regenerar el esqueleto, no para usarlo): FMOD Studio 1.08.12 y la plantilla del
SDK de audio de Assetto Corsa (``<AC>/sdk/audio/ac_fmod_sdk_1_9``). Construye con el mismo camino que
``src.audio.pipeline`` un banco con 2 ranuras por papel y AUDIO SINTÉTICO EN SILENCIO (ningún audio de
Kunos ni de terceros): lo que queda del SDK en el banco son las constantes de la plantilla
(buses, efectos y los 17 eventos secundarios sin muestras), que ``src.audio.template_spec`` ya
versionaba como números. Escribe en una carpeta NUEVA bajo ``output/`` (rutas seguras); para sustituir el esqueleto versionado se
copian a mano a ``src/audio/data/``:

* ``skeleton_engine.bank`` y ``skeleton_engine_guids.txt`` (solo líneas de banco, eventos y buses);
* ``skeleton_provenance.json`` (huellas del SDK, de la plantilla, de FMOD y del banco).

Uso: ``python -m tools.audio.make_skeleton [--out carpeta]`` (por defecto ``output/skeleton_nuevo``;
se niega a sobrescribir). Decisión del autor (2026-11-25): versionar este esqueleto.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ac.audio import trim_guids  # noqa: E402
from src.audio import fmod_build  # noqa: E402
from src.audio.plan import DATA, placement, template  # noqa: E402
from src.pipeline.contracts import detect_fmod  # noqa: E402
from src.safe_paths import ensure_safe_output_path  # noqa: E402

CAR = "beam2ac_skeleton"
RPMS = (803.0, 859.0)  # el mínimo que admite el generador (2 ranuras por papel)
SILENCE_SAMPLES = 2048


def _silent_wav(path: Path) -> None:
    data = bytes(2 * SILENCE_SAMPLES)
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, 1, 44100, 88200, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    path.write_bytes(hdr + data)


def make_spec(wavdir: Path) -> dict:
    tpl = template()
    import copy

    engine = copy.deepcopy(tpl["engine"])
    files: set[str] = set()
    top_ext = 20000.0
    tops = {"engine_ext": top_ext, "engine_int": 0.75 * top_ext}
    for en, ev in engine.items():
        ev["rpms_max"] = tops[en]
        for tr in ev["tracks"]:
            load = tr["name"].startswith("load")
            pl = placement(list(RPMS), tops[en])
            for p in pl:
                f = wavdir / f"EXH_{int(p['rpm'])}{'_P' if load else ''}.wav"
                if not f.exists():
                    _silent_wav(f)
                p["file"] = str(f).replace("\\", "/")
                files.add(p["file"])
            tr["name"] = f"{CAR} {tr['name']}"[:60]
            tr["sounds"] = pl
    return {
        "car": CAR,
        "cars_folder": tpl["cars_folder"],
        "bank_folder": tpl["bank_folder"],
        "fade_shape": tpl["fade_shape"],
        "engine": engine,
        "secondary": tpl["secondary"],
        "audio_files": sorted(files),
    }


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--out",
        default=str(ROOT / "output" / "skeleton_nuevo"),
        help="carpeta NUEVA bajo output/ (se copia a src/audio/data a propósito)",
    )
    args = ap.parse_args(argv)
    out = ensure_safe_output_path(args.out)
    targets = [
        out / n
        for n in (
            "skeleton_engine.bank",
            "skeleton_engine_guids.txt",
            "skeleton_provenance.json",
        )
    ]
    if any(t.exists() for t in targets):
        print(
            "ERROR: ya existe el esqueleto (no se sobrescribe); use --out con otra carpeta"
        )
        return 1
    fm = detect_fmod()
    if not fm["compatible"]:
        print(f"ERROR: se necesita FMOD Studio {fm['required']}")
        return 1
    fmod_cl = str(Path(fm["path"]) / "fmodstudiocl.exe")
    with tempfile.TemporaryDirectory(prefix="b2a_skel_") as tmp:
        tmp_p = Path(tmp)
        wav = tmp_p / "wav"
        wav.mkdir()
        spec = make_spec(wav)
        work = tmp_p / "proj"
        work.mkdir()
        fmod_build.prepare_project(spec, work, fmod_cl)
        fmod_build.run_build(work, fmod_cl, CAR)
        build = work / "fmod_project" / "ac_fmod_sdk_1_9" / "Build"
        bank = build / "Desktop" / f"{CAR}.bank"
        guids = build / "GUIDs.txt"
        out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bank, targets[0])
        lines = [
            ln.rstrip() for ln in trim_guids(guids.read_text(encoding="utf-8"), CAR)
        ]
        targets[1].write_bytes((chr(10).join(lines) + chr(10)).encode("utf-8"))
        prov = {
            "generator": "tools/audio/make_skeleton.py",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "car_id": CAR,
            "fmod_studio": fm["version"],
            "sdk_tree_sha256": fmod_build.sdk_tree_sha256(),
            "template_spec_sha256": hashlib.sha256(
                (DATA / "template_spec.json").read_bytes()
            ).hexdigest(),
            "build_events_template_sha256": hashlib.sha256(
                (DATA / "build_events.template.js").read_bytes()
            ).hexdigest(),
            "bank_sha256": sha(targets[0]),
            "guids_sha256": sha(targets[1]),
            "contents": (
                "Banco de plantilla del SDK de audio de Assetto Corsa 1.9 (buses, efectos y eventos "
                "secundarios sin muestras) con 2 ranuras de motor por papel de AUDIO SINTETICO EN SILENCIO; "
                "ningun audio de Kunos ni de terceros. Se usa solo como molde de src.audio.engine_gen."
            ),
        }
        targets[2].write_bytes(
            (json.dumps(prov, indent=1, ensure_ascii=False) + chr(10)).encode("utf-8")
        )
    print("esqueleto escrito en", out)
    for t in targets:
        print(f"  {t.name}  {t.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
