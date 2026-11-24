"""G2.8: huella de código de TODAS las etapas por cierre de importaciones.

* Lo que importa cada ``run_*`` de ``stages.py`` (y las funciones auxiliares de
  ``stages.py`` que llama) está en la huella de la etapa o de una previa.
* La huella de cada etapa es cerrada: todo lo que importa un archivo de la huella
  (también de forma diferida) está en ella.
* Ningún archivo de la huella usa importaciones dinámicas (no las vería el análisis).
* Demostración del defecto corregido: con la lista a mano anterior, cambiar
  ``src/jbeam/resolver.py`` (resuelve la configuración: de él depende todo el
  resultado de ``physics``) no cambiaba la clave de la etapa; ahora sí.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.project import orchestrator
from src.project.code_deps import (
    direct_imports,
    import_closure,
    imports_in,
    module_file,
)
from src.project.stages import MODEL_STAGE, STAGES
from src.safe_paths import PROJECT_ROOT

ALL = STAGES + (MODEL_STAGE,)
BY_NAME = {s.name: s for s in ALL}
STAGES_PY = PROJECT_ROOT / "src/project/stages.py"

# Huella de ``physics`` ANTES de G2.8 (lista a mano + stages.py) y de ``source``.
OLD_PHYSICS_CHAIN = {
    "src/project/stages.py",
    "src/pipeline/physics_preview.py",
    "src/ac/engine.py",
    "src/ac/coast_ref.py",
    "src/ac/drivetrain.py",
    "src/ac/chassis.py",
    "src/ac/assemble.py",
    "src/extract/engine_camso.py",
    "src/extract/drivetrain.py",
    "src/extract/traction.py",
    "src/extract/brakes_tyres.py",
    "src/extract/suspension.py",
    "src/extract/mass_geometry.py",
    "src/extract/aero.py",
    "src/jbeam/parts.py",
    "src/jbeam/sources.py",
    "src/pipeline/source_inventory.py",
    "src/extract/paints.py",
}


def _chain(name: str) -> set[str]:
    st = BY_NAME[name]
    out = set(st.code)
    for a in st.after + st.optional:
        out |= _chain(a)
    return out


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _reachable_imports(fn: str, funcs: dict[str, ast.FunctionDef]) -> set[Path]:
    """Archivos de ``src`` importados dentro de ``fn`` y de las funciones de
    ``stages.py`` que llama (por nombre, transitivamente)."""
    seen: set[str] = set()
    todo = [fn]
    files: set[Path] = set()
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        node = funcs[name]
        files |= imports_in(node, STAGES_PY)
        todo += [
            n.id for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in funcs
        ]
    return files


class TestStageCodeClosure(unittest.TestCase):
    def test_run_functions_imports_are_covered(self) -> None:
        tree = ast.parse(STAGES_PY.read_text(encoding="utf-8"))
        funcs = _functions(tree)
        for st in ALL:
            fn = st.run.__name__
            with self.subTest(stage=st.name, fn=fn):
                got = {
                    p.relative_to(PROJECT_ROOT).as_posix()
                    for p in _reachable_imports(fn, funcs)
                }
                # _model_tool_inputs entra por extra_inputs y su módulo ya está
                self.assertEqual(sorted(got - _chain(st.name)), [])

    def test_each_stage_code_is_closed(self) -> None:
        for st in ALL:
            with self.subTest(stage=st.name):
                missing = set()
                for rel in st.code:
                    if rel == "src/project/stages.py":
                        continue
                    for g in direct_imports(PROJECT_ROOT / rel):
                        r = g.relative_to(PROJECT_ROOT).as_posix()
                        if r not in st.code:
                            missing.add(f"{rel} -> {r}")
                self.assertEqual(sorted(missing), [])

    def test_no_dynamic_imports_in_stage_code(self) -> None:
        for rel in sorted({p for st in ALL for p in st.code}):
            tree = ast.parse((PROJECT_ROOT / rel).read_text(encoding="utf-8"))
            for n in ast.walk(tree):
                if isinstance(n, ast.Call):
                    f = n.func
                    name = getattr(f, "attr", None) or getattr(f, "id", None)
                    self.assertNotIn(
                        name, ("import_module", "__import__"), f"{rel}: {name}"
                    )

    def test_previous_lists_are_kept(self) -> None:
        """La huella solo crece: las listas a mano anteriores siguen dentro."""
        self.assertLessEqual(OLD_PHYSICS_CHAIN, _chain("physics"))

    def test_physics_now_covers_configuration_code(self) -> None:
        new = _chain("physics")
        for rel in (
            "src/jbeam/resolver.py",
            "src/jbeam/merge.py",
            "src/jbeam/config.py",
            "src/jbeam/expr.py",
            "src/jbeam/tables.py",
            "src/extract/nodes.py",
            "src/extract/wheels.py",
            "src/coords.py",
        ):
            self.assertNotIn(rel, OLD_PHYSICS_CHAIN, rel)
            self.assertIn(rel, new, rel)

    def test_resolver_change_changes_physics_key_only_with_new_code(self) -> None:
        new_code = BY_NAME["physics"].code
        old_code = tuple(sorted(OLD_PHYSICS_CHAIN))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in set(new_code) | set(old_code):
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PROJECT_ROOT / rel, dst)
            with mock.patch.object(orchestrator, "PROJECT_ROOT", root):
                old0, new0 = (
                    orchestrator._code_hash(old_code),
                    orchestrator._code_hash(new_code),
                )
                with open(root / "src/jbeam/resolver.py", "a", encoding="utf-8") as f:
                    f.write("\n# cambio\n")
                self.assertEqual(old0, orchestrator._code_hash(old_code))  # obsoleto
                self.assertNotEqual(new0, orchestrator._code_hash(new_code))


class TestImportClosure(unittest.TestCase):
    def test_deferred_and_package_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/a").mkdir(parents=True)
            (root / "src/__init__.py").write_text("", "utf-8")
            (root / "src/a/__init__.py").write_text("from .b import x\n", "utf-8")
            (root / "src/a/b.py").write_text("x = 1\n", "utf-8")
            (root / "src/a/c.py").write_text("y = 2\n", "utf-8")
            (root / "src/r.py").write_text(
                "def f():\n    from src.a import c\n    return c\n", "utf-8"
            )
            got = import_closure(("src/r.py",), root)
            self.assertEqual(
                got,
                (
                    "src/__init__.py",
                    "src/a/__init__.py",
                    "src/a/b.py",
                    "src/a/c.py",
                    "src/r.py",
                ),
            )

    def test_module_file(self) -> None:
        self.assertEqual(
            module_file("src.jbeam").relative_to(PROJECT_ROOT).as_posix(),
            "src/jbeam/__init__.py",
        )
        self.assertIsNone(module_file("src.no_existe"))


if __name__ == "__main__":
    unittest.main()
