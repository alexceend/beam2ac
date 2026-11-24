"""Mueve métodos de test (o clases enteras) de ``tests/`` a ``tests_not_required/``.

Uso: ``python tools/tests/move_methods.py <origen.py> <Clase> [metodo ...]``
Sin métodos se mueve la clase entera. El original conserva el resto; lo movido deja de existir allí
(una sola fuente de verdad). El destino es ``tests_not_required/<origen>`` con una cabecera de
procedencia. Los nombres de módulo que el código movido usa se resuelven copiando las importaciones
del original o importando los helpers de ``tests.<origen>`` (sin recoger tests ajenos).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _lines(src: str) -> list[str]:
    return src.splitlines(keepends=True)


def main(argv: list[str]) -> int:
    origin, cls_name, methods = argv[0], argv[1], set(argv[2:])
    src_path = ROOT / "tests" / origin
    text = src_path.read_text(encoding="utf8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = _lines(text)
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)

    def span(n: ast.AST) -> tuple[int, int]:
        start = min([n.lineno] + [d.lineno for d in getattr(n, "decorator_list", [])])
        return start - 1, n.end_lineno

    fns = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}
    missing = methods - set(fns)
    if missing:
        raise SystemExit(f"métodos inexistentes: {sorted(missing)}")
    whole = not methods
    moved = list(cls.body) if whole else [fns[m] for m in sorted(methods)]
    keep_members = [] if whole else [
        n
        for n in cls.body
        if n not in moved
        and isinstance(n, ast.FunctionDef)
        and not n.name.startswith("test")
        or (n not in moved and isinstance(n, (ast.Assign, ast.AnnAssign)))
    ]
    # el destino lleva los miembros no-test (setUpClass, constantes) + los métodos movidos
    body_nodes = [n for n in cls.body if n in moved or n in keep_members]
    if whole:
        body_nodes = list(cls.body)

    header = lines[cls.lineno - 1 + 0 : cls.body[0].lineno - 1]
    # cabecera de clase = desde decoradores/`class` hasta la primera sentencia (incluye docstring si la hay)
    cstart = span(cls)[0]
    first = cls.body[0]
    hdr_end = first.lineno - 1 if not (
        isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant)
    ) else first.end_lineno
    header = lines[cstart:hdr_end]
    chunks = []
    for n in body_nodes:
        if isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant):
            continue
        a, b = span(n)
        chunks.append("".join(lines[a:b]))
    new_class = "".join(header) + (nl).join(c.rstrip("\r\n") + nl for c in chunks)

    # nombres usados por la clase movida
    used = {x.id for n in body_nodes for x in ast.walk(n) if isinstance(x, ast.Name)}
    used |= {x.value.id for n in body_nodes for x in ast.walk(n)
             if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)}
    imports, helpers = [], []
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            names = {(a.asname or a.name).split(".")[0] for a in n.names}
            if names & used:
                a, b = span(n)
                imports.append("".join(lines[a:b]))
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n is not cls:
            if n.name in used:
                helpers.append(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            tg = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tg:
                if isinstance(t, ast.Name) and t.id in used:
                    helpers.append(t.id)
    mod = "tests." + origin[:-3]
    out_dir = ROOT / "tests_not_required"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "__init__.py").touch()
    dest = out_dir / origin
    doc = (
        f'"""Apartado de ``tests/{origin}`` (clase ``{cls_name}``'
        + ("" if whole else f", métodos {sorted(methods)}")
        + f"). Ver ``tests_not_required/README.md``.\"\"\"{nl}{nl}"
    )
    imp_helpers = ""
    if helpers:
        imp_helpers = f"from {mod} import ({', '.join(sorted(set(helpers)))}){nl}"  # noqa: E501
    if dest.exists():
        body = dest.read_text(encoding="utf8")
        body = body.rstrip() + nl + nl + nl + new_class
        # los imports nuevos se añaden tras el docstring si faltan
        extra = "".join(i for i in imports + [imp_helpers] if i and i not in body)
        marker = f'"""{nl}{nl}'
        idx = body.index(marker, 3) + len(marker) if body.startswith('"""') else 0
        body = body[:idx] + extra + body[idx:]
        dest.write_text(body, encoding="utf8", newline="")
    else:
        dest.write_text(
            doc + "".join(imports) + imp_helpers + nl + nl + new_class,
            encoding="utf8",
            newline="",
        )

    # quitar del original
    if whole:
        a, b = span(cls)
        remove = [(a, b)]
    else:
        remove = [span(fns[m]) for m in methods]
    for a, b in sorted(remove, reverse=True):
        del lines[a:b]
    src_path.write_text("".join(lines), encoding="utf8", newline="")
    print(f"movido {cls_name} {sorted(methods) or '(clase)'} -> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
