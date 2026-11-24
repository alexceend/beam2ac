"""Compuestos de neumático de Assetto Corsa añadidos al coche convertido (opcional, 2026-11-22).

El neumático CONVERTIDO queda como compuesto 0 con el nombre «Exported». Los compuestos que se marquen
se añaden como ``[FRONT_n]/[REAR_n]/[THERMAL_FRONT_n]/[THERMAL_REAR_n]`` (n = 1, 2, …) y salen en el menú
de neumáticos de boxes. Sus valores NO derivan de Automation ni de BeamNG: son PLANTILLAS DE DISEÑO leídas en
tiempo de ejecución de la instalación de AC del usuario (solo lectura; nada de Kunos se guarda en el repositorio),
con la geometría (ancho, radios, inercia, amortiguación y rigidez del neumático) del coche convertido.
El paquete resultante contiene material del juego: no se puede compartir (igual que el piloto).

Sin calibrar en el juego: el agarre del compuesto es el de su coche de origen (``FZ0``, ``DX_REF``…), no el de este
coche ni su masa.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPORTED_NAME = "Exported"
EXPORTED_SHORT = "E"

# claves de tamaño/estructura que se toman del neumático convertido y no de la plantilla
GEOMETRY_KEYS = ("WIDTH", "RADIUS", "RIM_RADIUS", "ANGULAR_INERTIA", "DAMP", "RATE")
AXLES = ("FRONT", "REAR")
SECTIONS = ("FRONT", "REAR", "THERMAL_FRONT", "THERMAL_REAR")


@dataclass(frozen=True)
class Compound:
    id: str
    label: str
    car: str  # carpeta en content/cars (solo lectura)
    name: str  # NAME del compuesto en su tyres.ini


COMPOUNDS: tuple[Compound, ...] = (
    Compound("street", "Street", "ks_toyota_ae86", "Street"),
    Compound("street90s", "Street 90s", "ks_toyota_ae86", "Street 90s"),
    Compound("semislick", "Semislicks", "ks_mazda_mx5_nc", "Semislicks"),
    Compound("slick_soft", "Slick soft", "ks_toyota_celica_st185", "Slicks Soft"),
    Compound("slick_medium", "Slick medium", "ks_toyota_celica_st185", "Slicks Medium"),
    Compound("slick_hard", "Slick hard", "ks_toyota_celica_st185", "Slicks Hard"),
)
BY_ID = {c.id: c for c in COMPOUNDS}


class CompoundError(ValueError):
    pass


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """Secciones en orden con sus claves; descarta comentarios (``;``) y líneas sueltas."""
    out: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and "]" in line:
            cur = out.setdefault(line[1 : line.index("]")], {})
        elif cur is not None and line and not line.startswith(";") and "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip()] = v.split(";", 1)[0].strip()
    return out


def render_ini(sections: dict[str, dict[str, str]]) -> str:
    out: list[str] = []
    for name, kv in sections.items():
        out += [f"[{name}]"] + [f"{k}={v}" for k, v in kv.items()] + [""]
    return "\n".join(out)


def _suffix(i: int) -> str:
    return "" if i == 0 else f"_{i}"


def _find_index(secs: dict[str, dict[str, str]], name: str) -> int | None:
    for i in range(0, 10):
        s = secs.get(f"FRONT{_suffix(i)}")
        if s is None:
            return None
        if s.get("NAME", "").lower() == name.lower():
            return i
    return None


def _source(
    ac_root: Path, comp: Compound
) -> tuple[dict[str, dict[str, str]], int, Path]:
    data = ac_root / "content" / "cars" / comp.car / "data"
    f = data / "tyres.ini"
    if not f.is_file():
        raise CompoundError(
            f"{comp.label}: falta {comp.car}/data/tyres.ini en la instalación de AC"
        )
    secs = parse_ini(f.read_text("utf-8", "replace"))
    idx = _find_index(secs, comp.name)
    if idx is None:
        raise CompoundError(
            f"{comp.label}: no hay un compuesto «{comp.name}» en {comp.car}"
        )
    return secs, idx, data


def available(ac_root: Path | str | None) -> list[dict[str, Any]]:
    """Lista de compuestos con su disponibilidad en la instalación de AC (solo lectura)."""
    root = Path(ac_root) if ac_root else None
    out = []
    for c in COMPOUNDS:
        ok, why = False, "sin ruta de Assetto Corsa"
        if root is not None:
            try:
                _source(root, c)
                ok, why = True, ""
            except CompoundError as exc:
                why = str(exc)
        out.append(
            {"id": c.id, "label": c.label, "car": c.car, "ok": ok, "reason": why}
        )
    return out


def apply(
    tyres_text: str, ac_root: Path | str, ids: list[str]
) -> tuple[str, dict[str, bytes], list[str]]:
    """``(tyres.ini nuevo, {archivo de data/: bytes}, notas)``. Lanza ``CompoundError`` si algo falta."""
    unknown = [i for i in ids if i not in BY_ID]
    if unknown:
        raise CompoundError(f"compuestos desconocidos: {unknown}")
    if len(set(ids)) != len(ids):
        raise CompoundError("compuestos repetidos")
    root = Path(ac_root)
    secs = parse_ini(tyres_text)
    for ax in AXLES:
        if ax not in secs:
            raise CompoundError(f"el tyres.ini convertido no tiene [{ax}]")
    # un coche con compuestos propios previos no se mezcla: solo se admite el compuesto 0
    if "FRONT_1" in secs:
        raise CompoundError("el tyres.ini convertido ya tiene más de un compuesto")
    for ax in AXLES:
        secs[ax]["NAME"], secs[ax]["SHORT_NAME"] = EXPORTED_NAME, EXPORTED_SHORT
    base = {ax: dict(secs[ax]) for ax in AXLES}
    notes = [f"neumático convertido = compuesto 0 «{EXPORTED_NAME}»"]
    files: dict[str, bytes] = {}
    for n, cid in enumerate(ids, start=1):
        comp = BY_ID[cid]
        src, idx, data = _source(root, comp)
        for sec in SECTIONS:
            s = src.get(f"{sec}{_suffix(idx)}")
            if s is None:
                continue  # p. ej. un compuesto sin térmica
            new = dict(s)
            ax = "FRONT" if sec.endswith("FRONT") else "REAR"
            if sec in AXLES:  # geometría del coche convertido, no la de la plantilla
                for k in GEOMETRY_KEYS:
                    if k in base[ax]:
                        new[k] = base[ax][k]
            for k, v in list(
                new.items()
            ):  # tablas .lut referenciadas: se copian de la instalación
                if v.lower().endswith(".lut"):
                    name = v
                    if name in files and files[name] != (data / v).read_bytes():
                        name = f"{cid}_{v}"
                    f = data / v
                    if not f.is_file():
                        raise CompoundError(f"{comp.label}: falta {comp.car}/data/{v}")
                    files[name] = f.read_bytes()
                    new[k] = name
            secs[f"{sec}_{n}"] = new
        notes.append(
            f"compuesto {n}: {comp.label} (plantilla de {comp.car}; geometría del coche convertido; "
            "agarre del compuesto de origen, sin calibrar)"
        )
    return render_ini(secs), files, notes
