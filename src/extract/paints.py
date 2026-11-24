"""Pinturas de un vehículo de BeamNG: lectura, normalización, procedencia y diagnóstico.

Solo lectura. No elige colores por su cuenta: si la pintura de la configuración no se
puede determinar con las reglas de BeamNG, lo dice (``PaintSelection.resolved = False``)
y el consumidor decide qué hacer (p. ej. dejar sin textura los materiales que dependen
de ella).

Fuentes y formatos (evidencia en docs/research/importacion_samples_2026-10-30.md):

* **``info.json`` → ``paints``** [C, 55 vehículos oficiales y los exportados de
  Automation]: ``{nombre: {baseColor: [r, g, b, a], metallic, roughness, clearcoat,
  clearcoatRoughness[, class]}}``. ``a`` de ``baseColor`` es el factor de brillo de
  BeamNG (hasta 2), no una opacidad.
* **``info.json`` → ``colors``** (formato antiguo) [C, ``core/vehicles.lua``
  ``convertVehicleInfo``]: ``{nombre: "r g b a m1 m2 m3 m4"}``; BeamNG lo convierte a
  ``paints``. Aquí se normaliza igual y se anota la procedencia.
* **``info.json`` / ``info_<config>.json`` → ``defaultPaintName1..3``** y sus nombres
  antiguos ``default_color``, ``default_color_2``, ``default_color_3`` [C, misma
  función: ``defaultPaintNameN = default_color(_N)``].
* **Herencia de huecos** [C, ``core/vehiclePaints.lua`` l. 307-317]: en la
  configuración, un ``defaultPaintNameN`` ausente o vacío toma el ``defaultPaintName1``
  del modelo (N = 1) o el de la propia configuración (N = 2, 3).
* **``defaultMultiPaintSetup``** [C, l. 307-308: tiene precedencia sobre
  ``defaultPaintName1``]: la resolución de esos conjuntos no está implementada; si
  aparece, la selección queda SIN RESOLVER (no se adivina).
* **``.pc`` → ``paints``** [C, 90 configuraciones oficiales; ``core/vehicle/colors.lua``
  la escribe como ``config.paints[1..3]``]: lista de pinturas guardadas. Su precedencia
  frente a ``defaultPaintName`` al aparecer el coche no está verificada; se informa y se
  sigue usando ``defaultPaintName`` (comportamiento anterior del conversor).
* **``paints.meta``** [C por inspección, solo en exportaciones de Automation; 0 en los
  123 zips oficiales]: N registros binarios de 48 bytes, uno por pintura ``DefaultN``
  en orden; cada uno = 4 ``float64`` (``baseColor`` RGBA) + 4 ``float32``
  (``clearcoat``, ``clearcoatRoughness``, ``metallic``, ``roughness``). Visto con N = 1
  (3 muestras: = ``Default1``) y N = 2 (Ibiza: = ``Default1`` y ``Default2``). Es
  opcional: el exportador no lo escribe cuando el coche usa pinturas por zonas. Es
  redundante con ``info.json`` (no se usa para elegir color; solo se contrasta). Un
  tamaño que no sea múltiplo de 48 se conserva en hexadecimal con un diagnóstico.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any

# Campos de una pintura de BeamNG (createVehiclePaint / validateVehiclePaint).
REQUIRED_FIELDS = ("baseColor",)
OPTIONAL_FIELDS = ("metallic", "roughness", "clearcoat", "clearcoatRoughness", "class")
LEGACY_DEFAULT_KEYS = {
    "default_color": "defaultPaintName1",
    "default_color_2": "defaultPaintName2",
    "default_color_3": "defaultPaintName3",
}
META_SIZE = 48
META_LAYOUT = "<4d4f"  # baseColor RGBA (f64) + clearcoat, clearcoatRoughness,
# metallic, roughness (f32)
META_TOLERANCE = 1e-5  # los f32 del final no reproducen 6 decimales exactos


@dataclass(frozen=True)
class PaintDiagnostic:
    severity: str  # "info" | "warning" | "error"
    code: str
    message: str
    source: str


@dataclass(frozen=True)
class Paint:
    name: str
    base_color: tuple[float, float, float, float]
    metallic: float | None
    roughness: float | None
    clearcoat: float | None
    clearcoat_roughness: float | None
    paint_class: str | None
    unknown: dict[str, Any]  # claves no reconocidas, sin tocar
    source: str  # p. ej. "info.json:paints/Default1"

    @property
    def rgb(self) -> tuple[float, float, float]:
        return self.base_color[0], self.base_color[1], self.base_color[2]


@dataclass(frozen=True)
class PaintSelection:
    slot: int  # 1..3
    name: str | None
    paint: Paint | None
    origin: str  # de dónde sale el nombre (o por qué no hay)

    @property
    def resolved(self) -> bool:
        return self.paint is not None


@dataclass
class PaintReport:
    paints: dict[str, Paint] = field(default_factory=dict)
    selection: list[PaintSelection] = field(default_factory=list)
    pc_paints: list[Paint] = field(default_factory=list)
    meta: dict[str, Any] | None = None
    raw_unparsed: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[PaintDiagnostic] = field(default_factory=list)

    def selected(self, slot: int = 1) -> PaintSelection | None:
        for s in self.selection:
            if s.slot == slot:
                return s
        return None

    def to_json(self) -> dict[str, Any]:
        def paint(p: Paint) -> dict[str, Any]:
            return {
                "name": p.name,
                "baseColor": list(p.base_color),
                "metallic": p.metallic,
                "roughness": p.roughness,
                "clearcoat": p.clearcoat,
                "clearcoatRoughness": p.clearcoat_roughness,
                "class": p.paint_class,
                "unknown": p.unknown,
                "source": p.source,
            }

        return {
            "paints": {k: paint(v) for k, v in sorted(self.paints.items())},
            "selection": [
                {
                    "slot": s.slot,
                    "name": s.name,
                    "resolved": s.resolved,
                    "origin": s.origin,
                }
                for s in self.selection
            ],
            "pc_paints": [paint(p) for p in self.pc_paints],
            "meta": self.meta,
            "raw_unparsed": self.raw_unparsed,
            "diagnostics": [d.__dict__ for d in self.diagnostics],
        }


class PaintFormatError(ValueError):
    """Pintura con un formato que no se puede interpretar."""


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaintFormatError(f"{what}: {value!r} no es un número")
    if not math.isfinite(value):
        raise PaintFormatError(f"{what}: {value!r} no es finito")
    return float(value)


def parse_paint(name: str, data: Any, source: str) -> Paint:
    """Una pintura en formato ``paints`` (dict). Lanza :class:`PaintFormatError`."""
    if not isinstance(data, dict):
        raise PaintFormatError(f"{source}: no es un objeto ({type(data).__name__})")
    for key in REQUIRED_FIELDS:
        if key not in data:
            raise PaintFormatError(f"{source}: falta '{key}'")
    bc = data["baseColor"]
    if not isinstance(bc, (list, tuple)) or len(bc) not in (3, 4):
        raise PaintFormatError(f"{source}: baseColor {bc!r} no tiene 3 o 4 componentes")
    comps = [_number(c, f"{source}.baseColor") for c in bc]
    if len(comps) == 3:
        # BeamNG trata el 4.º componente como factor (createVehiclePaint lo exige);
        # sin él no se inventa: se marca como ausente con NaN y se avisa fuera.
        comps.append(float("nan"))

    def opt(key: str) -> float | None:
        return _number(data[key], f"{source}.{key}") if key in data else None

    cls = data.get("class")
    if cls is not None and not isinstance(cls, str):
        raise PaintFormatError(f"{source}: class {cls!r} no es texto")
    unknown = {
        k: v for k, v in data.items() if k not in REQUIRED_FIELDS + OPTIONAL_FIELDS
    }
    return Paint(
        name,
        (comps[0], comps[1], comps[2], comps[3]),
        opt("metallic"),
        opt("roughness"),
        opt("clearcoat"),
        opt("clearcoatRoughness"),
        cls,
        unknown,
        source,
    )


def parse_legacy_color(name: str, text: Any, source: str) -> Paint:
    """``colors`` antiguo: ``"r g b a m1 m2 m3 m4"`` (convertVehicleInfo).

    Los 4 valores metálicos van en el orden de ``vehicleMetallicPaintString``:
    metallic, roughness, clearcoat, clearcoatRoughness.
    """
    if not isinstance(text, str):
        raise PaintFormatError(f"{source}: {text!r} no es texto")
    parts = text.split()
    if len(parts) not in (4, 8):
        raise PaintFormatError(f"{source}: {len(parts)} valores (se esperan 4 u 8)")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise PaintFormatError(f"{source}: {text!r} no son números") from None
    met: list[float | None] = list(vals[4:8]) if len(vals) == 8 else [None] * 4
    return Paint(
        name,
        (vals[0], vals[1], vals[2], vals[3]),
        met[0],
        met[1],
        met[2],
        met[3],
        None,
        {},
        source,
    )


def decode_paints_meta(data: bytes) -> list[dict[str, Any]]:
    """``paints.meta`` del exportador de Automation: N registros de 48 bytes, uno por
    pintura ``DefaultN`` en orden (ver el docstring del módulo)."""
    if not data or len(data) % META_SIZE:
        raise PaintFormatError(
            f"paints.meta de {len(data)} bytes (formato conocido: múltiplo de "
            f"{META_SIZE})"
        )
    records = []
    for off in range(0, len(data), META_SIZE):
        vals = struct.unpack(META_LAYOUT, data[off : off + META_SIZE])
        if not all(math.isfinite(v) for v in vals):
            raise PaintFormatError(f"paints.meta: registro en {off} no finito")
        r, g, b, a, cc, ccr, met, rough = vals
        records.append(
            {
                "baseColor": [r, g, b, a],
                "clearcoat": cc,
                "clearcoatRoughness": ccr,
                "metallic": met,
                "roughness": rough,
            }
        )
    return records


def _same_paint(meta: dict[str, Any], p: Paint) -> bool:
    pairs = list(zip(meta["baseColor"], p.base_color)) + [
        (meta["clearcoat"], p.clearcoat),
        (meta["clearcoatRoughness"], p.clearcoat_roughness),
        (meta["metallic"], p.metallic),
        (meta["roughness"], p.roughness),
    ]
    return all(b is not None and abs(a - b) <= META_TOLERANCE for a, b in pairs)


def _default_names(info: dict[str, Any], where: str) -> tuple[dict[int, Any], list]:
    """Nombres por defecto 1..3 con su clave de origen.

    Como ``convertVehicleInfo`` (core/vehicles.lua l. 592-633): si el archivo tiene
    ``colors`` o alguna clave ``default_color*``, BeamNG asigna SIEMPRE
    ``defaultPaintNameN = default_color(_N)``, aunque la antigua no exista (queda
    vacía) y aunque hubiera una moderna.
    """
    legacy_mode = "colors" in info or any(k in info for k in LEGACY_DEFAULT_KEYS)
    names: dict[int, tuple[Any, str]] = {}
    diags: list[PaintDiagnostic] = []
    for legacy, modern in LEGACY_DEFAULT_KEYS.items():
        slot = int(modern[-1])
        if legacy_mode:
            if modern in info:
                diags.append(
                    PaintDiagnostic(
                        "warning",
                        "pintura.clave_sobrescrita",
                        f"'{modern}' existe, pero BeamNG lo sobrescribe con "
                        f"'{legacy}' ({info.get(legacy)!r}) porque el archivo usa "
                        "el formato antiguo (convertVehicleInfo)",
                        where,
                    )
                )
            if legacy in info:
                names[slot] = (info[legacy], f"{where}:{legacy} (antigua)")
        elif modern in info:
            names[slot] = (info[modern], f"{where}:{modern}")
    return names, diags


def read_paints(
    model_info: dict[str, Any],
    config_info: dict[str, Any],
    pc_data: dict[str, Any] | None = None,
    meta_bytes: bytes | None = None,
    *,
    model_info_name: str = "info.json",
    config_info_name: str = "info_<config>.json",
    pc_name: str = "<config>.pc",
) -> PaintReport:
    rep = PaintReport()
    diag = rep.diagnostics.append

    # 1. catálogo de pinturas del modelo
    paints = model_info.get("paints")
    if isinstance(paints, dict):
        for name, data in paints.items():
            src = f"{model_info_name}:paints/{name}"
            try:
                p = parse_paint(str(name), data, src)
            except PaintFormatError as exc:
                rep.raw_unparsed[src] = data
                diag(
                    PaintDiagnostic("error", "pintura.no_interpretable", str(exc), src)
                )
                continue
            if math.isnan(p.base_color[3]):
                diag(
                    PaintDiagnostic(
                        "warning",
                        "pintura.basecolor_sin_factor",
                        "baseColor con 3 componentes; el factor (4.º) queda sin valor",
                        src,
                    )
                )
            if p.unknown:
                diag(
                    PaintDiagnostic(
                        "info",
                        "pintura.campos_desconocidos",
                        f"campos no reconocidos conservados: {sorted(p.unknown)}",
                        src,
                    )
                )
            rep.paints[p.name] = p
    elif paints is not None:
        rep.raw_unparsed[f"{model_info_name}:paints"] = paints
        diag(
            PaintDiagnostic(
                "error",
                "pintura.no_interpretable",
                f"'paints' es {type(paints).__name__}, no un objeto",
                model_info_name,
            )
        )
    colors = model_info.get("colors")
    if isinstance(colors, dict):
        for name, text in colors.items():
            src = f"{model_info_name}:colors/{name} (antigua)"
            try:
                p = parse_legacy_color(str(name), text, src)
            except PaintFormatError as exc:
                rep.raw_unparsed[src] = text
                diag(
                    PaintDiagnostic("error", "pintura.no_interpretable", str(exc), src)
                )
                continue
            if p.name in rep.paints:
                diag(
                    PaintDiagnostic(
                        "warning",
                        "pintura.duplicada",
                        f"'{p.name}' en 'paints' y en 'colors'; se conserva 'paints'",
                        src,
                    )
                )
                continue
            rep.paints[p.name] = p
    for key in ("multiPaintSetups", "libraryPaints"):
        val = model_info.get(key)
        if val:  # listas vacías: nada que conservar
            rep.raw_unparsed[f"{model_info_name}:{key}"] = val
            diag(
                PaintDiagnostic(
                    "info",
                    "pintura.no_soportado",
                    f"'{key}' conservado sin interpretar",
                    model_info_name,
                )
            )

    # 2. selección por defecto (vehiclePaints.lua l. 278-317)
    model_names, d1 = _default_names(model_info, model_info_name)
    config_names, d2 = _default_names(config_info, config_info_name)
    rep.diagnostics.extend(d1 + d2)
    config_multi = config_info.get("defaultMultiPaintSetup")
    model_multi = model_info.get("defaultMultiPaintSetup")
    for where, multi in (
        (config_info_name, config_multi),
        (model_info_name, model_multi),
    ):
        if multi:
            rep.raw_unparsed[f"{where}:defaultMultiPaintSetup"] = multi
            diag(
                PaintDiagnostic(
                    "warning",
                    "pintura.multipaint_sin_resolver",
                    f"defaultMultiPaintSetup={multi!r} tiene precedencia sobre "
                    "defaultPaintName (vehiclePaints.lua l. 278-308) y su resolución "
                    "no está implementada: los huecos afectados quedan sin resolver",
                    where,
                )
            )
    for slot in (1, 2, 3):
        name, origin = config_names.get(slot, (None, ""))
        from_model = False
        if name in (None, ""):
            if slot == 1 and 1 in model_names:
                name, origin = model_names[1]
                origin += " (heredado del modelo)"
                from_model = True
            elif slot > 1 and 1 in config_names and config_names[1][0]:
                name, origin = config_names[1]
                origin += f" (hueco {slot} heredado del 1)"
            elif slot > 1 and 1 in model_names:
                name, origin = model_names[1]
                origin += f" (hueco {slot} heredado del 1 del modelo)"
                from_model = True
            elif slot == 1 and model_multi:
                from_model = True
            elif (
                slot > 1
                and model_multi
                and not (1 in config_names and config_names[1][0])
            ):
                from_model = True
        if config_multi or (model_multi and from_model):
            rep.selection.append(
                PaintSelection(slot, None, None, "defaultMultiPaintSetup sin resolver")
            )
            continue
        if name in (None, ""):
            rep.selection.append(
                PaintSelection(slot, None, None, "ninguna fuente declara la pintura")
            )
            continue
        if not isinstance(name, str):
            # default_color como [r, g, b, a] (formato antiguo, convertVehicleInfo)
            src = f"{origin} (color en línea)"
            try:
                p = parse_paint(f"default_color_{slot}", {"baseColor": name}, src)
            except PaintFormatError as exc:
                rep.raw_unparsed[src] = name
                diag(
                    PaintDiagnostic("error", "pintura.no_interpretable", str(exc), src)
                )
                rep.selection.append(PaintSelection(slot, None, None, src))
                continue
            rep.selection.append(PaintSelection(slot, p.name, p, src))
            continue
        p = rep.paints.get(name)
        if p is None:
            diag(
                PaintDiagnostic(
                    "error",
                    "pintura.nombre_desconocido",
                    f"hueco {slot}: '{name}' no está en las pinturas del modelo "
                    f"({sorted(rep.paints)})",
                    origin,
                )
            )
        rep.selection.append(PaintSelection(slot, name, p, origin))
    first = rep.selected(1)
    if first is None or not first.resolved:
        diag(
            PaintDiagnostic(
                "warning",
                "pintura.sin_seleccion",
                "la configuración no permite determinar la pintura 1; los materiales "
                "que dependen de ella (instanceDiffuse) necesitan una decisión",
                config_info_name,
            )
        )

    # 3. pinturas guardadas en la .pc
    if pc_data is not None and "paints" in pc_data:
        raw = pc_data["paints"]
        if isinstance(raw, list):
            for i, data in enumerate(raw, 1):
                src = f"{pc_name}:paints[{i}]"
                try:
                    rep.pc_paints.append(parse_paint(f"pc_paint_{i}", data, src))
                except PaintFormatError as exc:
                    rep.raw_unparsed[src] = data
                    diag(
                        PaintDiagnostic(
                            "error", "pintura.no_interpretable", str(exc), src
                        )
                    )
            diag(
                PaintDiagnostic(
                    "warning",
                    "pintura.pc_precedencia_sin_verificar",
                    f"la .pc guarda {len(raw)} pinturas; su precedencia frente a "
                    "defaultPaintName no está verificada: se informan y se usa "
                    "defaultPaintName",
                    pc_name,
                )
            )
        else:
            rep.raw_unparsed[f"{pc_name}:paints"] = raw
            diag(
                PaintDiagnostic(
                    "error",
                    "pintura.no_interpretable",
                    f"'paints' es {type(raw).__name__}, no una lista",
                    pc_name,
                )
            )

    # 4. paints.meta (opcional, Automation)
    if meta_bytes is None:
        diag(
            PaintDiagnostic(
                "info",
                "pintura.meta_ausente",
                "sin paints.meta (opcional; solo lo escribe el exportador de "
                "Automation y no siempre)",
                "paints.meta",
            )
        )
    else:
        try:
            records = decode_paints_meta(meta_bytes)
        except PaintFormatError as exc:
            rep.meta = {"raw_hex": meta_bytes.hex(), "records": None}
            diag(
                PaintDiagnostic(
                    "warning", "pintura.meta_desconocido", str(exc), "paints.meta"
                )
            )
        else:
            out = []
            for i, rec in enumerate(records, 1):
                expected = f"Default{i}"
                exp = rep.paints.get(expected)
                matches = sorted(
                    n for n, p in rep.paints.items() if _same_paint(rec, p)
                )
                out.append(
                    {
                        "index": i,
                        "decoded": rec,
                        "matches": matches,
                        "expected": expected,
                        "matches_expected": bool(exp and _same_paint(rec, exp)),
                    }
                )
                if not (exp and _same_paint(rec, exp)):
                    diag(
                        PaintDiagnostic(
                            "warning",
                            "pintura.meta_sin_correspondencia",
                            f"registro {i} de paints.meta no coincide con "
                            f"'{expected}' de info.json (coincide con {matches}); "
                            "se conserva sin usar",
                            "paints.meta",
                        )
                    )
            rep.meta = {"raw_hex": meta_bytes.hex(), "records": out}
    return rep
