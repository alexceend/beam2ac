"""INI de Assetto Corsa a partir de los atributos RESUELTOS (fase B1).

Une el motor de atributos (reglas + decisiones del catálogo y del proyecto) con los
archivos de AC. No es un segundo sistema de reglas: toma los resultados de
``src.attrs`` tal cual y solo decide qué se puede ESCRIBIR y con qué trazabilidad.

Se escribe un resultado si:

* su estado es ``known``, ``inferred`` o ``format_requirement``;
* tiene valor y NO requiere aprobación (una propuesta sin aprobar no se aplica);
* su unidad es la del atributo de destino o convertible a ella (``src.attrs.units``);
* el valor es representable según el ``dtype`` del atributo.

Todo lo demás queda como BLOQUEO con su motivo: ``manual_required`` (falta una
decisión), ``ambiguous`` (conflicto), propuesta pendiente, unidad incompatible, valor
no representable o archivo referenciado que esta etapa no genera. ``not_applicable`` se
registra aparte (no es un bloqueo). Ningún atributo sin resolver se convierte en un
valor por defecto ni en 0.

Tablas referenciadas (fase B4, opción L1 de
docs/research/propuesta_frontshare_luts_2026-10-26.md): un atributo
``ac:<archivo>/<SECCIÓN>/<CLAVE>/table`` es el contenido de la LUT cuyo nombre da
el valor de ``ac:<archivo>/<SECCIÓN>/<CLAVE>`` (p. ej. ``WEAR_CURVE``). El esquema no
depende del nombre del archivo. Si la clave que nombra el archivo no se escribe, la
tabla queda bloqueada; si dos claves nombran el mismo archivo con tablas distintas, es
un conflicto y no se escribe ninguna.

Clase epistémica de cada clave (se conserva): ``confirmado`` (``known``), ``inferido``
(``inferred`` por regla), ``decidido`` (``inferred``/``known`` por decisión) y
``requisito_de_formato``.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.attrs import units

WRITABLE = ("known", "inferred", "format_requirement")
FILE_SUFFIXES = (".lut", ".rto", ".kn5", ".ksanim", ".knh")
HEADER = "; generado por beam2ac, etapa resolved_ini: trazabilidad en trace.json"
TABLE_SUFFIX = "/table"


def table_reference(target: str) -> str | None:
    """Clave que nombra el archivo de un atributo ``…/table`` (o ``None``)."""
    if target.startswith("ac:") and target.endswith(TABLE_SUFFIX):
        ref = target[: -len(TABLE_SUFFIX)]
        if ref[3:].count("/") == 2:
            return ref
    return None


def table_problems(value: Any) -> list[str]:
    """Estructura de una LUT de AC: filas ``[x, y]`` numéricas y finitas, al menos dos,
    con x estrictamente creciente (así son las 15 LUT de neumático de referencia y
    todas las ``power.lut``; AC interpola entre filas)."""
    if not isinstance(value, (list, tuple)):
        return ["la tabla no es una lista de filas"]
    if len(value) < 2:
        return [f"la tabla necesita al menos 2 filas (tiene {len(value)})"]
    out: list[str] = []
    xs: list[float] = []
    for i, row in enumerate(value):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            out.append(f"fila {i}: se esperaba [x, y], hay {row!r}")
            continue
        for v in row:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                out.append(f"fila {i}: valor no numérico {v!r}")
            elif not math.isfinite(v):
                out.append(f"fila {i}: valor no finito {v!r}")
        if isinstance(row[0], (int, float)) and not isinstance(row[0], bool):
            xs.append(float(row[0]))
    if not out:
        bad = [i for i in range(1, len(xs)) if not xs[i] > xs[i - 1]]
        if bad:
            out.append(f"x no estrictamente creciente en las filas {bad}")
    return out


def parse_lut(text: str) -> list[tuple[float, float]]:
    """Lee una LUT ``x|y`` (ignora líneas vacías y comentarios ``;``)."""
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 2:
            raise ValueError(f"línea {n}: se esperaba x|y: {raw!r}")
        try:
            rows.append((float(parts[0]), float(parts[1])))
        except ValueError:
            raise ValueError(f"línea {n}: número inválido: {raw!r}") from None
    return rows


@dataclass
class KeyTrace:
    target: str
    file: str
    section: str | None
    key: str | None
    rendered: str
    value: Any
    unit: str
    attribute_unit: str
    dtype: str
    status: str
    epistemic: str
    source: str
    rule: str
    decision: str
    confidence: str
    transform: Any
    inputs: Any
    assumptions: list[str]
    open_issues: list[str]
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Blocker:
    target: str
    # decisión_necesaria | conflicto | propuesta_pendiente | unidad | valor | archivo
    # (y, en la etapa, validación)
    kind: str
    detail: str


@dataclass
class ResolvedIni:
    files: dict[str, str]  # nombre -> texto
    traces: list[KeyTrace]
    blockers: list[Blocker]
    not_applicable: list[str]

    def to_trace_json(self) -> dict[str, Any]:
        return {
            "schema": "beam2ac.resolved_ini/1",
            "keys": [asdict(t) for t in self.traces],
            "blockers": [asdict(b) for b in self.blockers],
            "not_applicable": self.not_applicable,
        }


def evidence_index(catalog: Any) -> dict[str, list[dict[str, Any]]]:
    """Evidencia citada por cada regla (``id@versión``) y decisión (``id``)."""
    from src.attrs import schema as S

    out: dict[str, list[dict[str, Any]]] = {}
    for key, rule in catalog.rules.items():
        out[key] = [S._to_plain(e) for e in rule.evidence]
    for d in catalog.decisions:
        out[d.id] = [S._to_plain(e) for e in d.evidence]
    return out


def split_target(target: str) -> tuple[str, str | None, str | None]:
    """``ac:brakes.ini/DATA/MAX_TORQUE`` → (``brakes.ini``, ``DATA``, ``MAX_TORQUE``);
    ``ac:power.lut`` → (``power.lut``, None, None)."""
    if not target.startswith("ac:"):
        raise ValueError(f"destino no es de AC: {target!r}")
    parts = target[3:].split("/")
    if len(parts) == 1:
        return parts[0], None, None
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(f"destino con forma desconocida: {target!r}")


def fmt_number(v: Any) -> str:
    """Número finito con hasta 12 cifras significativas (determinista)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"no numérico: {v!r}")
    if not math.isfinite(v):
        raise ValueError(f"no finito: {v!r}")
    if isinstance(v, int):
        return str(v)
    s = format(v, ".12g")
    return "0" if s == "-0" else s


def render_value(value: Any, dtype: str) -> str:
    """Texto del valor para un INI de AC, o ``ValueError`` si no es representable."""
    if dtype == "number":
        return fmt_number(value)
    if dtype == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"no entero: {value!r}")
        if isinstance(value, float) and not value.is_integer():
            raise ValueError(f"no entero: {value!r}")
        return str(int(value))
    if dtype == "vector":
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError(f"vector esperado: {value!r}")
        return ", ".join(fmt_number(x) for x in value)
    if dtype in ("text", "enum"):
        s = str(value)
        if not s or any(c in s for c in "\r\n;[]="):
            # AC: ``;`` corta el valor, ``[``/``]`` abren sección (lectura de INI de AC)
            raise ValueError(f"texto no representable en un INI de AC: {s!r}")
        return s
    raise ValueError(f"dtype no admitido en un INI: {dtype!r}")


def render_table(value: Any) -> str:
    problems = table_problems(value)
    if problems:
        raise ValueError("tabla inválida: " + "; ".join(problems))
    lines = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f"fila de tabla inválida: {row!r}")
        lines.append(f"{fmt_number(row[0])}|{fmt_number(row[1])}")
    return "\n".join(lines) + "\n"


def epistemic(result: dict[str, Any]) -> str:
    if result["status"] == "format_requirement":
        return "requisito_de_formato"
    if result.get("source") == "decision":
        return "decidido"
    return "confirmado" if result["status"] == "known" else "inferido"


def build(
    results: list[dict[str, Any]],
    attributes: dict[str, dict[str, Any]],
    evidence: dict[str, list[dict[str, Any]]] | None = None,
) -> ResolvedIni:
    """Construye los INI y la trazabilidad a partir de los resultados del motor.

    ``attributes``: definiciones del catálogo por id (``unit``, ``dtype``).
    ``evidence``: referencias de evidencia por regla o decisión (clave ``rule``/``id``).
    El orden de archivos, secciones y claves es el de ``results`` (orden del conjunto
    de destinos del catálogo), así que la salida es determinista.
    """
    evidence = evidence or {}
    traces: list[KeyTrace] = []
    blockers: list[Blocker] = []
    not_applicable: list[str] = []
    sections: dict[str, dict[str, list[tuple[str, str]]]] = {}
    tables: dict[str, str] = {}
    references: list[tuple[str, str, str]] = []  # (destino, archivo, archivo referido)
    by_target = {r["target"]: r for r in results}
    # LUT referenciadas: archivo -> [(destino, texto, traza)]; se resuelven al final
    ref_tables: dict[str, list[tuple[str, str, KeyTrace]]] = {}
    for r in results:
        target = r["target"]
        ref_key = table_reference(target)
        try:
            if ref_key is not None:
                fname, section, key = "", None, None  # el nombre lo da ref_key
            else:
                fname, section, key = split_target(target)
        except ValueError as exc:
            blockers.append(Blocker(target, "valor", str(exc)))
            continue
        status = r["status"]
        if status == "not_applicable":
            not_applicable.append(target)
            continue
        if status == "manual_required":
            reasons = "; ".join(str(x) for x in (r.get("reasons") or [])) or "sin regla"
            blockers.append(Blocker(target, "decisión_necesaria", reasons))
            continue
        if status == "ambiguous":
            issues = (
                "; ".join(str(x) for x in (r.get("open_issues") or [])) or "conflicto"
            )
            blockers.append(Blocker(target, "conflicto", issues))
            continue
        if status not in WRITABLE:
            blockers.append(Blocker(target, "valor", f"estado no escribible: {status}"))
            continue
        if r.get("requires_approval"):
            blockers.append(
                Blocker(
                    target,
                    "propuesta_pendiente",
                    f"{r.get('rule')}: propuesta sin aprobar (no se aplica)",
                )
            )
            continue
        value = r.get("value")
        if value is None:
            blockers.append(Blocker(target, "valor", f"estado {status} sin valor"))
            continue
        attr = attributes.get(target)
        if attr is None:
            blockers.append(
                Blocker(target, "valor", "atributo de destino no catalogado")
            )
            continue
        a_unit, r_unit, dtype = (
            attr["unit"],
            r.get("unit") or "undocumented",
            attr["dtype"],
        )
        if dtype == "table" and r_unit != a_unit:
            blockers.append(
                Blocker(target, "unidad", f"tabla en {r_unit!r}, destino en {a_unit!r}")
            )
            continue
        if (
            dtype in ("number", "vector")
            and r_unit != a_unit
            and a_unit != "undocumented"
        ):
            try:
                ok = r_unit != "undocumented" and units.compatible(r_unit, a_unit)
            except ValueError:
                ok = False
            if not ok:
                blockers.append(
                    Blocker(
                        target,
                        "unidad",
                        f"resultado en {r_unit!r}, destino en {a_unit!r}",
                    )
                )
                continue
            try:
                value = (
                    units.convert(value, r_unit, a_unit)
                    if dtype == "number"
                    else [units.convert(x, r_unit, a_unit) for x in value]
                )
            except (ValueError, TypeError) as exc:
                blockers.append(Blocker(target, "unidad", str(exc)))
                continue
        try:
            rendered = (
                render_table(value) if dtype == "table" else render_value(value, dtype)
            )
        except ValueError as exc:
            blockers.append(
                Blocker(
                    target,
                    "valor",
                    (
                        f"{exc} (no se escribe)"
                        if dtype == "table"
                        else f"{exc}; el catálogo declara dtype {dtype!r}: el valor no "
                        "es representable sin corregir el catálogo (no se escribe)"
                    ),
                )
            )
            continue
        if ref_key is not None:
            if dtype != "table":
                blockers.append(
                    Blocker(target, "valor", f"{target} debe ser dtype table")
                )
                continue
            name = _referenced_name(by_target.get(ref_key), attributes.get(ref_key))
            if name is None or name.startswith("<"):
                blockers.append(
                    Blocker(
                        target,
                        "archivo",
                        f"la clave {ref_key} que nombra el archivo no tiene un valor "
                        f"escribible ({name or 'sin resultado'}); "
                        "la tabla no se escribe",
                    )
                )
                continue
            fname = name
        if dtype in ("text", "enum") and rendered.lower().endswith(FILE_SUFFIXES):
            references.append((target, fname, rendered))
        ev_key = r.get("decision") or r.get("rule") or ""
        traces.append(
            KeyTrace(
                target=target,
                file=fname,
                section=section,
                key=key,
                rendered=(
                    rendered if dtype != "table" else f"<tabla de {len(value)} filas>"
                ),
                value=value,
                unit=a_unit,
                attribute_unit=a_unit if r_unit == a_unit else r_unit,
                dtype=dtype,
                status=status,
                epistemic=epistemic(r),
                source=r.get("source") or "",
                rule=r.get("rule") or "",
                decision=r.get("decision") or "",
                confidence=r.get("confidence") or "",
                transform=r.get("transform"),
                inputs=r.get("inputs"),
                assumptions=list(r.get("assumptions") or []),
                open_issues=list(r.get("open_issues") or []),
                evidence=list(evidence.get(ev_key, [])),
            )
        )
        if ref_key is not None:
            ref_tables.setdefault(fname, []).append((target, rendered, traces.pop()))
        elif dtype == "table" or section is None:
            tables[fname] = rendered
        else:
            sections.setdefault(fname, {}).setdefault(section, []).append(
                (key, rendered)
            )
    files: dict[str, str] = {}
    for fname, secs in sections.items():
        lines = [HEADER, ""]
        for sec, keys in secs.items():
            lines.append(f"[{sec}]")
            lines += [f"{k}={v}" for k, v in keys]
            lines.append("")
        files[fname] = "\n".join(lines)
    files.update(tables)
    for fname, entries in sorted(ref_tables.items()):
        if fname in files or len({e[1] for e in entries}) > 1:
            for target, _text, _trace in entries:
                blockers.append(
                    Blocker(
                        target,
                        "conflicto",
                        f"{fname}: lo nombran {[e[0] for e in entries]} con tablas "
                        "distintas, o choca con otro archivo; no se escribe",
                    )
                )
            continue
        files[fname] = entries[0][1]
        traces += [e[2] for e in entries]
    # referencias a archivos (p. ej. POWER_CURVE=power.lut): bloqueo si no se generan
    for target, fname, ref in references:
        if ref not in files:
            blockers.append(
                Blocker(
                    target,
                    "archivo",
                    f"{fname} referencia {ref!r}, que esta etapa no genera "
                    "(la clave se escribe, pero el archivo falta)",
                )
            )
    return ResolvedIni(dict(sorted(files.items())), traces, blockers, not_applicable)


def _referenced_name(
    result: dict[str, Any] | None, attr: dict[str, Any] | None
) -> str | None:
    """Nombre de archivo que escribe la clave que referencia una tabla, o un motivo
    entre ``<…>`` si no se escribe o no es un nombre de LUT simple."""
    if result is None or attr is None:
        return None
    if result.get("status") not in WRITABLE or result.get("requires_approval"):
        return f"<estado {result.get('status')}>"
    try:
        name = render_value(result.get("value"), attr.get("dtype", "text"))
    except ValueError as exc:
        return f"<{exc}>"
    if (
        not name.lower().endswith(".lut")
        or any(c in name for c in "/\\:")
        or (name in (".", ".."))
    ):
        return f"<no es un nombre de LUT simple: {name!r}>"
    return name


# --- comparación con una referencia (leída como AC) ---------------------------------

_DEC = re.compile(r"^[+-]?\d*\.?(\d*)")


def _decimals(text: str) -> int:
    m = _DEC.match(text.strip())
    return len(m.group(1)) if m and "." in m.group(0) else 0


def _floats(text: str) -> list[float] | None:
    try:
        return [float(x) for x in text.split(",")]
    except ValueError:
        return None


def compare_value(ours: str, ref: str) -> str:
    """Clasifica: ``idéntico`` | ``mismo_valor_otro_formato`` |
    ``igual_a_la_precisión`` | ``diferente``.

    ``igual_a_la_precisión``: la diferencia cabe en el redondeo del número con menos
    decimales de los dos (p. ej. 1119.57029 frente a 1119.6). Es una diferencia de
    redondeo, no una equivalencia demostrada del valor exacto.
    """
    if ours == ref:
        return "idéntico"
    a, b = _floats(ours), _floats(ref)
    if a is None or b is None or len(a) != len(b):
        return "diferente"
    if a == b:
        return "mismo_valor_otro_formato"
    oparts, rparts = ours.split(","), ref.split(",")
    for x, y, ox, ry in zip(a, b, oparts, rparts):
        tol = 0.5 * 10 ** -min(_decimals(ox), _decimals(ry))
        if abs(x - y) > tol + 1e-12:
            return "diferente"
    return "igual_a_la_precisión"


def compare_tables(ours: str, ref: str) -> dict[str, Any]:
    def rows(t: str) -> list[tuple[str, str]]:
        out = []
        for line in t.splitlines():
            line = line.split(";")[0].strip()
            if "|" in line:
                x, y = line.split("|", 1)
                out.append((x.strip(), y.strip()))
        return out

    a, b = rows(ours), rows(ref)
    if len(a) != len(b):
        return {"status": "diferente", "detail": f"{len(a)} filas frente a {len(b)}"}
    worst = "idéntico"
    order = [
        "idéntico",
        "mismo_valor_otro_formato",
        "igual_a_la_precisión",
        "diferente",
    ]
    diffs = []
    for (x1, y1), (x2, y2) in zip(a, b):
        s = max(compare_value(x1, x2), compare_value(y1, y2), key=order.index)
        if s == "diferente":
            diffs.append(f"{x1}|{y1} frente a {x2}|{y2}")
        worst = max(worst, s, key=order.index)
    return {"status": worst, "rows": len(a), "different_rows": diffs[:10]}
