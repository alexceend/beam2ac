"""Candidatos de emparejamiento BeamNG ↔ Assetto Corsa (solo lectura).

Un candidato NUNCA es una equivalencia: indica que dos vehículos podrían representar el
mismo coche real y cada factor se muestra por separado (a favor / en contra / no
comparable). La confirmación es humana (``review = "pendiente"``).

Factores (cada uno con su dato de origen):

* **marca**: la marca de AC aparece como palabra en el nombre o la marca de BeamNG;
* **modelo**: palabras de modelo compartidas (sin marca ni palabras genéricas);
* **potencia** y **masa**: diferencia relativa entre la configuración de BeamNG
  (``info_*.json``) y ``ui_car.json`` de AC (``specs``), con tolerancia ±15 %;
* **año**: dentro del rango ``Years`` de BeamNG.

Clasificación: ``candidato`` (marca, todo el modelo y potencia compatible) o
``hipótesis`` (modelo parcial o potencia en contra). Ver :func:`compare`.
Las marcas oficiales de
BeamNG son ficticias; no se infiere su coche real.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.importers.automation import is_automation_info

GENERIC = {
    "ks",
    "the",
    "and",
    "de",
    "del",
    "la",
    "le",
    "acfr",
    "mod",
    "car",
    "cars",
    "v1",
    "v2",
    "edition",
    "sport",
    "sports",
    "custom",
    "race",
    "racing",
    "street",
    "road",
    "drift",
    "tuned",
    "spec",
    "pack",
    "streetcarpack",
    "beamng",
    "automation",
}
TOLERANCE = 0.15


def declares_automation(meta: dict[str, Any]) -> bool:
    """Marca de procedencia de un ``info.json`` de BeamNG: ``is_automation_info``
    (``Type``, ``Author`` y ``ExporterVersion`` en lista cerrada). Es DELIBERADAMENTE más
    estricto que ``importers.automation.is_automation`` (G2.50): ese acepta además un
    ``ExporterVersion`` desconocido si el Lua del origen coincide con el de una versión
    conocida, y aquí solo hay ``info.json``, sin Lua. Consecuencia: en el emparejamiento,
    una exportación de build nueva aparece como «no declara Automation» (solo afecta al
    factor de procedencia del candidato; la conversión no usa este detector). No tiene
    nada que ver con el origen de los coches de AC (``ac_refs.classify_origin``)."""
    return is_automation_info(meta)


def tokens(text: str | None) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def number(text: Any) -> float | None:
    """Primer número de un texto de ``specs`` (``"500hp"`` → 500), o ``None``."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    m = re.search(r"\d+(?:[.,]\d+)?", str(text or ""))
    return float(m.group(0).replace(",", ".")) if m else None


@dataclass
class BngVehicle:
    origin: str  # zip
    name: str
    brand: str
    years: tuple[int, int] | None
    configs: list[dict[str, Any]]  # {file, power_hp, weight_kg, drivetrain}
    automation: bool


@dataclass
class AcCar:
    id: str
    brand: str
    year: int | None
    power_hp: float | None
    weight_kg: float | None


@dataclass
class Candidate:
    bng: str
    bng_name: str
    ac: str
    classification: str
    for_: list[str] = field(default_factory=list)
    against: list[str] = field(default_factory=list)
    not_comparable: list[str] = field(default_factory=list)
    best_config: str | None = None
    review: str = "pendiente"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["for"] = d.pop("for_")
        return d


def _rel(a: float, b: float) -> float:
    """Diferencia relativa CON signo de ``a`` (BeamNG) respecto a ``b`` (AC)."""
    return (a - b) / max(abs(b), 1e-9)


def compare(b: BngVehicle, a: AcCar) -> Candidate | None:
    """Comparación con factores explícitos, o ``None`` si no comparten marca y modelo.

    ``candidato`` exige: marca de AC en el nombre de BeamNG, TODAS las palabras de
    modelo de BeamNG presentes en AC y potencia dentro de ±15 % (si se puede comparar).
    La masa fuera de tolerancia cuenta en contra pero, por ser orientativa (puede
    incluir o no piloto y combustible), no rebaja la clasificación por sí sola.
    """
    bng_tokens = set(tokens(b.name)) | set(tokens(b.brand))
    brand_tokens = set(tokens(a.brand))
    if not brand_tokens or not brand_tokens <= bng_tokens:
        return None
    model_ac = set(tokens(a.id)) - brand_tokens - GENERIC
    model_bng = bng_tokens - brand_tokens - GENERIC
    shared = sorted(model_ac & model_bng)
    if not shared:
        return None
    c = Candidate(b.origin, b.name, a.id, "hipótesis")
    c.for_.append(f"marca «{a.brand}» en el nombre de BeamNG «{b.name}»")
    c.for_.append(f"modelo compartido: {shared}")
    missing = sorted(model_bng - model_ac)
    if missing:
        c.against.append(f"palabras de modelo de BeamNG que no están en AC: {missing}")
    if b.automation:
        c.not_comparable.append(
            "origen exportado desde Automation: es una recreación, no el coche de AC"
        )
    best = None
    for cfg in b.configs:
        if cfg.get("power_hp") and a.power_hp:
            err = abs(_rel(cfg["power_hp"], a.power_hp))
            if best is None or err < best[0]:
                best = (err, cfg)
    if best is None:  # sin potencia: se compara la primera configuración con masa
        best = next(((0.0, cfg) for cfg in b.configs if cfg.get("weight_kg")), None)
    power_ok: bool | None = None
    if best is None:
        c.not_comparable.append("sin potencia ni masa comparables")
    else:
        cfg = best[1]
        c.best_config = cfg["file"]
        for label, mine, theirs, unit in (
            ("potencia", cfg.get("power_hp"), a.power_hp, "hp"),
            ("masa", cfg.get("weight_kg"), a.weight_kg, "kg"),
        ):
            if not mine or not theirs:
                c.not_comparable.append(f"{label}: falta en uno de los dos")
                continue
            r = _rel(mine, theirs)
            ok = abs(r) <= TOLERANCE
            txt = (
                f"{label} {mine:g} {unit} (BeamNG) frente a {theirs:g} {unit} (AC): "
                f"{r:+.1%}"
            )
            if label == "masa":
                txt += " (orientativo)"
            else:
                power_ok = ok
            (c.for_ if ok else c.against).append(txt)
        c.not_comparable.append(
            "la masa de AC (ui_car.json) y la de BeamNG (info) pueden incluir o no "
            "piloto y combustible"
        )
    if a.year and b.years:
        lo, hi = b.years
        (c.for_ if lo <= a.year <= hi else c.against).append(
            f"año AC {a.year} frente a BeamNG {lo}–{hi}"
        )
    if not missing and power_ok is not False:
        c.classification = "candidato"
    return c


# --- carga desde los inventarios existentes y los mods del usuario --------------------


def load_bng_inventory(path: Path) -> list[BngVehicle]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for z in data["zips"]:
        for meta in z.get("model_meta", {}).values():
            if meta.get("Type") not in ("Car", "Truck", "Automation"):
                continue
            years = meta.get("Years")
            yr = (
                (int(years["min"]), int(years["max"]))
                if isinstance(years, dict) and years.get("min") and years.get("max")
                else None
            )
            out.append(
                BngVehicle(
                    z["zip"],
                    meta.get("Name") or "",
                    meta.get("Brand") or "",
                    yr,
                    [
                        {
                            "file": c["file"],
                            "power_hp": number(c.get("Power")),
                            "weight_kg": number(c.get("Weight")),
                            "drivetrain": c.get("Drivetrain"),
                        }
                        for c in z.get("configs", [])
                    ],
                    declares_automation(meta),
                )
            )
    return out


def load_mod_zips(mods_dir: Path) -> list[BngVehicle]:
    """Metadatos de los mods (``info.json`` e ``info_*.json``) leídos del zip."""
    out = []
    for z in sorted(Path(mods_dir).glob("*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                names = zf.namelist()
                roots = [
                    n for n in names if n.count("/") == 2 and n.endswith("/info.json")
                ]
                for root in roots:
                    prefix = root[: -len("info.json")]
                    meta = json.loads(zf.read(root).decode("utf-8", "replace"))
                    cfgs = []
                    for n in names:
                        if n.startswith(prefix + "info_") and n.endswith(".json"):
                            c = json.loads(zf.read(n).decode("utf-8", "replace"))
                            cfgs.append(
                                {
                                    "file": n,
                                    "power_hp": number(c.get("Power")),
                                    "weight_kg": number(c.get("Weight")),
                                    "drivetrain": c.get("Drivetrain"),
                                }
                            )
                    out.append(
                        BngVehicle(
                            z.name,
                            meta.get("Name") or "",
                            meta.get("Brand") or "",
                            None,
                            cfgs,
                            declares_automation(meta),
                        )
                    )
        except (zipfile.BadZipFile, ValueError, KeyError):
            continue
    return out


def load_ac_inventory(path: Path) -> list[AcCar]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cars = (
        data["cars"] if isinstance(data["cars"], list) else list(data["cars"].values())
    )
    out = []
    for c in cars:
        if str(c["id"]).startswith("beam2ac"):
            continue  # paquetes del propio proyecto: no son referencias
        specs = c.get("specs") or {}
        year = c.get("year")
        out.append(
            AcCar(
                c["id"],
                c.get("brand") or "",
                int(year) if isinstance(year, (int, float)) else None,
                number(specs.get("bhp")),
                number(specs.get("weight")),
            )
        )
    return out


def pair(bng: list[BngVehicle], ac: list[AcCar]) -> list[Candidate]:
    out = [c for b in bng for a in ac if (c := compare(b, a)) is not None]
    order = {"candidato": 0, "hipótesis": 1}
    return sorted(out, key=lambda c: (order[c.classification], c.bng, c.ac))
