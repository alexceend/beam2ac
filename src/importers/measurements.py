"""Medidas hechas en el juego de origen y asociadas a un origen concreto (G2.19).

Algunas magnitudes del origen no se pueden calcular a partir de los archivos: las
calcula el C++ cerrado del juego (p. ej. la aerodinámica por triángulos de BeamNG,
G2.15 §5).
Para ellas el proyecto guarda medidas hechas en un banco del juego con el vehículo SIN
modificar, en ``measurements/`` (solo lectura para el conversor).

Una medida solo se usa si su ``source_fingerprint`` coincide con la huella del origen
cargado (:func:`source_fingerprint`): el contenido interpretado de TODAS las piezas del
índice y de la ``.pc``. Nunca se busca por nombre de vehículo ni de archivo: el mismo
nombre con otro contenido no recibe la medida, y otro nombre con el mismo contenido sí.

Formato de un registro (``beam2ac.measurement/1``)::

    {"schema": "beam2ac.measurement/1", "kind": "<tipo>",
     "source_fingerprint": "<sha256>", "game": {...}, "bench": {...},
     ...datos propios del tipo...}

Tipos conocidos: ``beamng_aero_bench`` (``src/extract/aero_bench.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# BEAM2AC_MEASUREMENTS_ROOT: carpeta alternativa (pruebas sin medidas del banco)
def _measurements_root() -> Path:
    env = os.environ.get("BEAM2AC_MEASUREMENTS_ROOT")
    if env:
        return Path(env)
    from src.safe_paths import DATA_ROOT

    if DATA_ROOT == PROJECT_ROOT:
        return PROJECT_ROOT / "measurements"
    # instalado: las medidas se escriben, así que viven en datos de usuario (copia inicial del programa)
    root = DATA_ROOT / "measurements"
    bundled = PROJECT_ROOT / "measurements"
    if not root.exists() and bundled.is_dir():
        shutil.copytree(bundled, root)
    return root


MEASUREMENTS_ROOT = _measurements_root()
SCHEMA = "beam2ac.measurement/1"


class MeasurementError(ValueError):
    """Registro de medida mal formado."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def source_fingerprint(source: Any) -> str:
    """Huella del contenido de un origen cargado (``VehicleSource``).

    sha256 del JSON canónico de ``{"parts": {nombre: datos interpretados},
    "pc": {"parts": …, "vars": …}}`` con TODAS las piezas del índice (no solo las
    activas: una pieza nueva en el origen ya es otro vehículo). No depende de rutas,
    saltos de línea, comentarios ni del orden de los archivos."""
    from src.jbeam.sources import ZipMember

    # G2.45: las piezas de ``common.zip`` (``vehicles/common/…``) son del juego, no
    # del vehículo: con ``--common-zip`` el índice las incluye y la huella dejaba de
    # coincidir con la de la misma exportación sin él (medidas perdidas)
    parts = {
        name: part.data
        for name, part in source.index.parts.items()
        if not (
            isinstance(part.file, ZipMember)
            and part.file.member.startswith("vehicles/common/")
        )
    }
    pc = {"parts": source.pc.parts, "vars": source.pc.vars}
    blob = _canonical({"parts": parts, "pc": pc}).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --- huella ACOTADA por tipo de medida (G2.47) ----------------------------------------
# La huella completa de arriba es la identidad exacta del origen. Una medida solo depende
# de lo que el juego usa para medirla: las piezas ACTIVAS de la configuración (no las que
# ningún slot ofrece, ni las de ``common.zip`` que solo traen sonido) y, de ellas, las
# secciones físicas del tipo. Añadir una pieza de interior/sonido/pintura NO cambia la
# huella acotada; añadir un nodo, un neumático o (solo para aerodinámica) un triángulo,
# sí. Es conservadora: ante la duda (nodos, vigas, masa del depósito) invalida.
_COMMON_SECTIONS = frozenset(
    {
        "nodes", "beams", "torsionbars", "rails", "slidenodes", "hydros", "refNodes",
        "pressureWheels", "energyStorage", "mainTank", "scalenodeWeight",
        "scalebeamSpring", "scalebeamDamp", "scalebeamDeform", "scalebeamStrength",
        # controles de estabilidad/tracción/freno: actúan sobre las ruedas durante el banco
        "esc", "tractionControl", "brakeControl", "motorTorqueControl",
    }
)  # fmt: skip
SCOPES: dict[str, frozenset[str]] = {
    "beamng_tyre_bench": _COMMON_SECTIONS,
    "beamng_aero_bench": _COMMON_SECTIONS
    | {"triangles", "quads", "dragModel", "downforceModel", "downforceNodeGroups"},
}
_VAR = re.compile(r"\$[A-Za-z_]\w*")


def scoped_fingerprint(source: Any, kind: str) -> str:
    """Huella de lo que afecta a la medida ``kind`` (``s1-<sha256>``): secciones del
    alcance de las piezas activas (con su ``nodeOffset`` efectivo) y el valor efectivo de
    las variables que esas secciones citan. No depende de piezas inactivas, de
    ``common.zip`` ni de secciones ajenas (motor, luces, sonido, carrocería gráfica)."""
    from src.jbeam import resolve

    if kind not in SCOPES:
        raise MeasurementError(f"tipo de medida sin alcance definido: {kind!r}")
    sections = SCOPES[kind]
    res = resolve(source.index, source.pc)
    parts = {}
    for a in res.active:
        data = {k: v for k, v in a.part.data.items() if k in sections}
        if data or a.node_offset:
            parts[a.name] = {"data": data, "node_offset": a.node_offset}
    names = sorted(set(_VAR.findall(_canonical(parts))))
    values = res.variable_values()
    variables = {n: values.get(n) for n in names}
    blob = _canonical({"parts": parts, "vars": variables}).encode("utf-8")
    return "s1-" + hashlib.sha256(blob).hexdigest()


def match_kind(
    rec: dict[str, Any],
    source: Any,
    fp: str | None = None,
    scoped: dict[str, str] | None = None,
) -> str | None:
    """``"exacta"`` (misma huella completa), ``"compatible"`` (la misma huella acotada
    del tipo, registrada al medir) o ``None``. ``fp``/``scoped`` = huellas del origen ya
    calculadas (``find`` las reutiliza para todos los registros)."""
    if rec["source_fingerprint"] == (fp or source_fingerprint(source)):
        return "exacta"
    want = rec.get("scoped_fingerprint")
    kind = rec["kind"]
    if want and kind in SCOPES:
        cache = scoped if scoped is not None else {}
        if kind not in cache:
            cache[kind] = scoped_fingerprint(source, kind)
        if want == cache[kind]:
            return "compatible"
    return None


def measurement_files(root: Path | None = None) -> list[Path]:
    base = Path(root) if root is not None else MEASUREMENTS_ROOT
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.json") if p.is_file())


def load_record(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise MeasurementError(f"{path}: no es un registro {SCHEMA}")
    for key in ("kind", "source_fingerprint"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise MeasurementError(f"{path}: falta '{key}'")
    return data


_HEADER_KEYS = ("kind", "source_fingerprint", "scoped_fingerprint", "label")
_HEADERS: dict[tuple[str, int, int], dict[str, Any]] = {}


def record_header(path: Path) -> dict[str, Any]:
    """Las claves de :func:`load_record` que identifican un registro (solo lectura),
    memorizadas mientras el archivo no cambie: se consultan todos en cada búsqueda y
    las muestras que contienen son grandes. Un registro inválido falla igual."""
    st = Path(path).stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    head = _HEADERS.get(key)
    if head is None:
        rec = load_record(path)
        head = {k: rec[k] for k in _HEADER_KEYS if k in rec}
        _HEADERS[key] = head
    return head


def find(
    source: Any, kind: str, root: Path | None = None
) -> tuple[Path, dict[str, Any]] | None:
    """El registro de ``kind`` que corresponde al origen (huella exacta o acotada), o ``None``.

    Dos registros igual de válidos son un error (no se elige); ``_match`` dice cuál."""
    files = measurement_files(root)
    if not files:
        return None
    hits = []
    fp, scoped = source_fingerprint(source), {}
    for path in files:
        head = record_header(path)
        if head["kind"] != kind:
            continue
        how = match_kind(head, source, fp, scoped)
        if how:
            hits.append((how, path, load_record(path)))
    # una medida EXACTA del propio origen gana a las compatibles de otro
    exact = [h for h in hits if h[0] == "exacta"]
    hits = exact or hits
    if len(hits) > 1:
        raise MeasurementError(
            f"{len(hits)} registros '{kind}' para el mismo origen "
            f"{source_fingerprint(source)[:12]}: "
            + ", ".join(str(p) for _, p, _ in hits)
        )
    if not hits:
        return None
    how, path, rec = hits[0]
    return path, dict(rec, _match=how)


def tree_sha256(root: Path | None = None) -> str:
    """Huella de TODOS los registros (para la caché de etapas del proyecto)."""
    base = Path(root) if root is not None else MEASUREMENTS_ROOT
    h = hashlib.sha256()
    for path in measurement_files(base):
        h.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()
