"""Prepara (sin ejecutar FMOD) la compilación del banco de sonido para otro id de coche.

    python tools/audio/prepare_fmod_build.py --car-id beam2ac_seat_ibiza_acfr --out output/<nueva>
    python tools/audio/prepare_fmod_build.py --collect output/<nueva>

Paso 1 (este script): copia la plantilla del SDK de audio (docs/references/…/ac_fmod_sdk_1_9)
a una carpeta NUEVA y comprueba la copia. Después escribe ``spec.json`` y ``build_events.js``
a partir de los de v16sn (output/seat_ibiza_acfr/sound_work_2026-10-25), cambiando solo el
id del coche, y un ``LEEME.md`` con las dos órdenes de ``fmodstudiocl`` que debe ejecutar
el usuario. FMOD genera GUID nuevos: es la alternativa limpia a reasignar el banco de v16sn
(``export --audio-source-id``).

Paso 2 (manual, el usuario): ejecutar las órdenes del ``LEEME.md``.

Paso 3 (``--collect``): copia ``Build/Desktop/<id>.bank`` y ``Build/GUIDs.txt`` a
``<carpeta>/prebuilt/`` y los valida con ``src.ac.audio.load_prebuilt``. Después:
``python -m src.project export … --audio-bank <carpeta>/prebuilt``.
``common.bank`` nunca se copia (contiene los sonidos globales de Kunos).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ac.audio import load_prebuilt  # noqa: E402
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

SDK = ROOT / "docs/references/assetto-corsa/audio/ac_fmod_sdk_1_9"
WORK = ROOT / "output/seat_ibiza_acfr/sound_work_2026-10-25"
FMOD = Path(r"C:\Program Files\FMOD SoundSystem\FMOD Studio 1.08.12\fmodstudiocl.exe")


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def prepare(car_id: str, out: Path) -> Path:
    out = new_output_dir(out)
    before = tree(SDK)
    prj = out / "fmod_project" / "ac_fmod_sdk_1_9"
    shutil.copytree(SDK, ensure_safe_output_path(prj))
    if tree(prj) != before or tree(SDK) != before:
        raise SystemExit("la copia de la plantilla no es idéntica (o la plantilla cambió)")
    spec = json.loads((WORK / "spec.json").read_text(encoding="utf-8"))
    old = spec["car"]
    spec["car"] = car_id
    missing = [f for f in spec["audio_files"] if not Path(f).is_file()]
    if missing:
        raise SystemExit(f"faltan grabaciones: {missing[:3]}")
    (out / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    js = (WORK / "scripts" / "build_ibiza_events.template.js").read_text(encoding="utf-8")
    (out / "build_events.js").write_text(
        js.replace("/*SPEC*/null", json.dumps(spec)), encoding="utf-8"
    )
    fspro = prj / "ac_fmod_sdk_1_9.fspro"
    readme = f"""# Compilación manual del banco de sonido para `{car_id}`

Preparado por `tools/audio/prepare_fmod_build.py`. FMOD **no** se ha ejecutado.

- Plantilla del SDK copiada y verificada en `fmod_project/` ({len(before)} archivos).
- `spec.json`: el de v16sn con el id cambiado (`{old}` → `{car_id}`). Las mismas 76 grabaciones
  de Automation y los mismos 17 eventos secundarios silenciosos; sin audio de Kunos.
- `build_events.js`: crea `cars/{car_id}`, el banco y los 19 eventos con la API de scripts
  (FMOD genera los GUID).

## Órdenes (ejecútelas usted, en una consola, desde esta carpeta)

```
"{FMOD}" -script build_events.js "{fspro}"
"{FMOD}" -build -banks {car_id} -platforms Desktop -export-guids "{fspro}"
```

Son las mismas órdenes que en la fase 2 de `{WORK.relative_to(ROOT).as_posix()}/README.md`.
No use `-diagnostic` (falla también con la plantilla intacta).

## Después

```
python tools/audio/prepare_fmod_build.py --collect "{out}"
python -m src.project export output/projects/ibiza_35b84_b1 --local-driver --ac-root <AC> --audio-bank "{out / 'prebuilt'}"
```
"""
    (out / "LEEME.md").write_text(readme, encoding="utf-8")
    (out / "prepare.json").write_text(
        json.dumps(
            {"car_id": car_id, "from_spec": str(WORK / "spec.json"), "sdk_files": len(before)},
            indent=1,
        ),
        encoding="utf-8",
    )
    return out


def collect(out: Path) -> Path:
    car_id = json.loads((out / "prepare.json").read_text(encoding="utf-8"))["car_id"]
    build = out / "fmod_project" / "ac_fmod_sdk_1_9" / "Build"
    bank = build / "Desktop" / f"{car_id}.bank"
    guids = build / "GUIDs.txt"
    if not bank.is_file() or not guids.is_file():
        raise SystemExit(f"aún no hay {bank} y {guids}: ejecute las órdenes del LEEME.md")
    dst = new_output_dir(out / "prebuilt")
    shutil.copyfile(bank, dst / bank.name)
    shutil.copyfile(guids, dst / "GUIDs.txt")
    audio = load_prebuilt(dst, car_id)  # valida: GUID del banco y de los eventos
    print(f"banco válido: {len(audio.events)} eventos, {audio.buses} buses -> {dst}")
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--car-id")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--collect", type=Path)
    a = ap.parse_args(argv)
    if a.collect:
        collect(a.collect)
        return 0
    if not (a.car_id and a.out):
        ap.error("--car-id y --out (o --collect)")
    print(prepare(a.car_id, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
