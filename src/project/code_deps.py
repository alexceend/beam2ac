"""Código del que depende una etapa: cierre de importaciones estáticas (G2.8).

Antes, cada etapa declaraba a mano los ``*.py`` de su huella. La auditoría de G2.8
(``docs/research/g28_genericidad_2026-11-01.md`` §2) encontró que la de ``physics`` no
incluía, por ejemplo, ``src/jbeam/resolver.py``, ``merge.py``, ``config.py`` ni
``src/extract/nodes.py``/``wheels.py``, que esa etapa ejecuta: un cambio en ellos no
cambiaba la clave y el orquestador reutilizaba un resultado obsoleto.

Regla: el código de una etapa son sus raíces más todo lo que importan, de forma
transitiva, incluidas las importaciones diferidas (dentro de funciones) y el
``__init__.py`` de cada paquete que Python ejecuta al importar un submódulo. Es un
análisis estático (``ast``):

* sobreincluye (un módulo importado por una función que la etapa no llama también
  cuenta): solo provoca recálculos de más, nunca resultados obsoletos;
* no ve importaciones dinámicas (``importlib``/``__import__``); en ``src`` no hay
  ninguna en el código de las etapas (``tests/test_g28_stage_code.py`` lo vigila);
* solo sigue módulos de ``src`` (la biblioteca estándar no forma parte de la huella).
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from src.safe_paths import PROJECT_ROOT


def module_file(module: str, root: Path = PROJECT_ROOT) -> Path | None:
    """Archivo de un módulo ``src.…`` (``.py`` o ``__init__.py``), o ``None``."""
    p = root / Path(*module.split("."))
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def _package(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root).with_suffix("")
    return list(rel.parts[:-1])


def _modules_in(node: ast.AST, path: Path, root: Path) -> list[str]:
    """Módulos que nombra una sentencia ``import``/``from … import``."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        pkg = _package(path, root)
        base = pkg[: len(pkg) - (node.level - 1)] if node.level else []
        mod = ".".join(base + ([node.module] if node.module else []))
        # ``from paquete import submódulo`` importa el submódulo
        return [mod] + [f"{mod}.{a.name}" for a in node.names]
    return []


def _statements(tree: ast.AST):
    """``ast.walk`` sin bajar a las expresiones (un ``import`` es una sentencia)."""
    todo = [tree]
    while todo:
        node = todo.pop()
        yield node
        for name in ("body", "orelse", "finalbody", "handlers", "cases"):
            block = getattr(node, name, None)
            if isinstance(block, list):
                todo += block


def direct_imports(path: Path, root: Path = PROJECT_ROOT) -> set[Path]:
    """Archivos de ``src`` que importa ``path`` en cualquier punto (también diferido),
    con el ``__init__.py`` de cada paquete intermedio."""
    st = path.stat()
    return set(_direct_cached(path, root, st.st_mtime_ns, st.st_size))


@lru_cache(maxsize=None)
def _direct_cached(path: Path, root: Path, mtime_ns: int, size: int) -> frozenset:
    # las etapas comparten casi todos los módulos: un solo ast.parse por archivo
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return frozenset(imports_in(tree, path, root))


def imports_in(tree: ast.AST, path: Path, root: Path = PROJECT_ROOT) -> set[Path]:
    """Como :func:`direct_imports`, sobre un nodo AST de ``path`` (p. ej. una
    función); ``path`` resuelve las importaciones relativas."""
    out: set[Path] = set()
    for node in _statements(tree):
        for mod in _modules_in(node, path, root):
            if mod != "src" and not mod.startswith("src."):
                continue
            parts = mod.split(".")
            for i in range(1, len(parts) + 1):
                f = module_file(".".join(parts[:i]), root)
                if f is not None:
                    out.add(f)
    return out


@lru_cache(maxsize=None)
def _closure_cached(roots: tuple[str, ...], root: str) -> tuple[str, ...]:
    base = Path(root)
    seen: set[Path] = set()
    todo = [base / r for r in roots]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        if not f.is_file():
            raise FileNotFoundError(f"raíz de la huella inexistente: {f}")
        seen.add(f)
        todo.extend(direct_imports(f, base) - seen)
        # importar un módulo ejecuta antes el __init__.py de sus paquetes
        for parent in f.relative_to(base).parents:
            init = base / parent / "__init__.py"
            if parent.parts and init.is_file() and init not in seen:
                todo.append(init)
    return tuple(sorted(p.relative_to(base).as_posix() for p in seen))


def import_closure(roots: tuple[str, ...] | list[str], root: Path = PROJECT_ROOT):
    """Raíces + cierre transitivo de sus importaciones de ``src`` (rutas relativas,
    ordenadas)."""
    return _closure_cached(tuple(sorted(roots)), str(root))
