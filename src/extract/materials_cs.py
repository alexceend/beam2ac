"""Lector de ``materials.cs`` (TorqueScript) de las exportaciones de Automation 200409.

El exportador 200409 escribe los materiales en ``materials.cs`` en lugar de
``<config>.materials.json`` (G2.8, alcance_automation_2026-11-01.md). Forma observada en
los 7 vehículos del corpus (``samples_beamng/*/vehicles/*/materials.cs``)::

    singleton Material(<nombre>)
    {
        mapTo = "<nombre>";
        diffuseMap[0] = "vehicles/common/null.dds";
        diffuseColor[0] = "0.168627 0.168627 0.168627 1";
        instanceDiffuse[2] = true;
        doubleSided = "1";
        materialTag0 = "beamng"; materialTag2 = "vehicle";
    };

Este módulo devuelve la MISMA forma que ``lax_json.load(<config>.materials.json)``:
``{nombre: {"name", "class", "mapTo", "Stages": [dict × 4], <claves de material>}}``.
``clave[i] = v`` va a ``Stages[i]``; ``clave = v`` al material. Conversión de valores
(solo sintáctica; el significado lo decide quien lo usa):

* ``diffuseColor`` (y cualquier clave ``*Color``): texto de números separados por
  espacios → lista de float, como el ``diffuseColor`` de materials.json;
* ``true``/``false`` sin comillas → bool; en los campos booleanos de ``Material``
  (:data:`BOOL_FIELDS`) también ``"1"``/``"0"`` → bool, como en materials.json;
* el resto se conserva como texto (sin comillas).

Tokenizador tolerante (no regex sobre el texto, regla del proyecto): identificadores,
cadenas con escapes ``\\"``, números, ``( ) { } [ ] = ;``, y comentarios ``//`` y
``/* */``. Un bloque que no sea ``singleton|new Material(...) {...}`` se salta entero
contando llaves; una sentencia mal formada dentro de un material se registra en
``issues`` y se salta hasta el ``;`` siguiente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STAGES = 4  # BeamNG: hasta 4 capas por material (materials.json tiene siempre 4)
_PUNCT = set("(){}[]=;,")


class MaterialsCsError(ValueError):
    """``materials.cs`` ilegible."""


@dataclass
class Token:
    kind: str  # "id" | "str" | "num" | "p"
    text: str
    line: int


@dataclass
class MaterialsCs:
    materials: dict[str, dict[str, Any]]
    issues: list[str] = field(default_factory=list)


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            line += text.count("\n", i, end)
            i = end
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "\n":
                    line += 1
                buf.append(text[j])
                j += 1
            out.append(Token("str", "".join(buf), line))
            i = j + 1
        elif c in _PUNCT:
            out.append(Token("p", c, line))
            i += 1
        elif c.isalnum() or c in "_$%.-+":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$%.-+:"):
                j += 1
            word = text[i:j]
            kind = "num" if _is_number(word) else "id"
            out.append(Token(kind, word, line))
            i = j
        else:  # carácter desconocido: se ignora (tolerancia)
            i += 1
    return out


def _is_number(word: str) -> bool:
    try:
        float(word)
        return True
    except ValueError:
        return False


# Campos booleanos de ``Material`` que materials.json escribe como true/false y
# materials.cs como "1"/"0" o true/false (en TorqueScript un campo bool acepta los dos).
BOOL_FIELDS = frozenset(
    {
        "doubleSided",
        "instanceDiffuse",
        "translucent",
        "alphaTest",
        "castShadows",
        "dynamicCubemap",
        "translucentZWrite",
        "useAnisotropic",
        "vertColor",
        "glow",
        "emissive",
    }
)


def _value(key: str, tok: Token) -> Any:
    if tok.kind == "id" and tok.text.lower() in ("true", "false"):
        return tok.text.lower() == "true"
    if key in BOOL_FIELDS and tok.text.strip().lower() in ("1", "0", "true", "false"):
        return tok.text.strip().lower() in ("1", "true")
    if key.lower().endswith("color") and tok.kind in ("str", "num"):
        try:
            return [float(x) for x in tok.text.split()]
        except ValueError:
            return tok.text
    return tok.text


def parse(text: str) -> MaterialsCs:
    toks = tokenize(text)
    res = MaterialsCs({})
    i, n = 0, len(toks)

    def at(k: int, kind: str, txt: str | None = None) -> bool:
        return k < n and toks[k].kind == kind and (txt is None or toks[k].text == txt)

    def skip_block(k: int) -> int:
        depth = 0
        while k < n:
            if at(k, "p", "{"):
                depth += 1
            elif at(k, "p", "}"):
                depth -= 1
                if depth == 0:
                    k += 1
                    return k + 1 if at(k, "p", ";") else k
            k += 1
        return k

    while i < n:
        if (
            at(i, "id")
            and toks[i].text in ("singleton", "new")
            and at(i + 1, "id", "Material")
            and at(i + 2, "p", "(")
        ):
            j = i + 3
            name_tok = toks[j] if j < n else None
            if name_tok is None or name_tok.kind not in ("id", "str", "num"):
                res.issues.append(f"línea {toks[i].line}: Material sin nombre")
                i = skip_block(i)
                continue
            while j < n and not at(j, "p", ")"):
                j += 1
            if not at(j + 1, "p", "{"):
                res.issues.append(f"línea {toks[i].line}: Material sin cuerpo")
                i = j + 1
                continue
            name = name_tok.text
            mat: dict[str, Any] = {
                "name": name,
                "class": "Material",
                "Stages": [{} for _ in range(STAGES)],
            }
            k = j + 2
            while k < n and not at(k, "p", "}"):
                # clave [ índice ] = valor ;
                if not at(k, "id"):
                    res.issues.append(
                        f"línea {toks[k].line}: {name}: se esperaba una clave, hay "
                        f"{toks[k].text!r}"
                    )
                    while k < n and not at(k, "p", ";") and not at(k, "p", "}"):
                        k += 1
                    k += 1 if at(k, "p", ";") else 0
                    continue
                key, line = toks[k].text, toks[k].line
                k += 1
                index = None
                if at(k, "p", "[") and at(k + 1, "num") and at(k + 2, "p", "]"):
                    index = int(float(toks[k + 1].text))
                    k += 3
                if not at(k, "p", "=") or k + 1 >= n or toks[k + 1].kind == "p":
                    res.issues.append(f"línea {line}: {name}: '{key}' sin valor")
                    while k < n and not at(k, "p", ";") and not at(k, "p", "}"):
                        k += 1
                    k += 1 if at(k, "p", ";") else 0
                    continue
                value = _value(key, toks[k + 1])
                k += 2
                if at(k, "p", ";"):
                    k += 1
                if index is None:
                    if key in mat and key not in ("castShadows",):
                        res.issues.append(f"línea {line}: {name}: '{key}' repetida")
                    mat[key] = value
                elif 0 <= index < STAGES:
                    mat["Stages"][index][key] = value
                else:
                    res.issues.append(
                        f"línea {line}: {name}: capa {index} fuera de 0..{STAGES - 1}"
                    )
            i = k + 1
            if at(i, "p", ";"):
                i += 1
            if name in res.materials:
                res.issues.append(f"material repetido: {name} (vale el último)")
            res.materials[name] = mat
            continue
        if at(i, "p", "{"):
            i = skip_block(i)
            continue
        i += 1
    return res


def load(path: str | Path) -> MaterialsCs:
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    res = parse(text)
    if not res.materials:
        raise MaterialsCsError(f"{path}: ningún 'singleton Material' legible")
    return res
