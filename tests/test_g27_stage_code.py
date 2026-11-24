"""G2.7, paso 0: huella de código de la etapa ``attributes`` (informe G2 §13.2.5).

* Toda importación de ``src.attrs``, ``src.ir`` o ``src.importers`` alcanzable desde la
  etapa (también las diferidas, dentro de funciones) está en su huella.
* Lo que esos paquetes importan directamente fuera de ellos está en la huella de la
  etapa o en la de una etapa previa (cuya clave entra en la de ``attributes``).
* Un cambio en un archivo de ``src/ir`` o ``src/importers`` cambia la huella.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.project import orchestrator
from src.project.stages import STAGES
from src.safe_paths import PROJECT_ROOT

PACKAGES = ("src/attrs", "src/ir", "src/importers")
BY_NAME = {s.name: s for s in STAGES}


def _module_file(mod: str) -> Path | None:
    p = PROJECT_ROOT / Path(*mod.split("."))
    if p.with_suffix(".py").is_file():
        return p.with_suffix(".py")
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return None


def _imports(f: Path) -> set[Path]:
    """Archivos de ``src`` importados por ``f`` (en cualquier punto del módulo)."""
    rel = f.relative_to(PROJECT_ROOT).with_suffix("")
    pkg = list(rel.parts[:-1])
    out: set[Path] = set()
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = pkg[: len(pkg) - (n.level - 1)] if n.level else []
            mod = ".".join(base + ([n.module] if n.module else []))
            names = [mod] + [f"{mod}.{a.name}" for a in n.names]
        for m in names:
            if m.startswith("src"):
                mf = _module_file(m)
                if mf is not None:
                    out.add(mf)
    return out


def _chain_code(name: str) -> set[str]:
    st = BY_NAME[name]
    code = set(st.code)
    for a in st.after:
        code |= _chain_code(a)
    return code


def _in_packages(p: Path) -> bool:
    return p.relative_to(PROJECT_ROOT).as_posix().startswith(PACKAGES)


class TestAttributesStageCode(unittest.TestCase):
    def setUp(self) -> None:
        self.code = set(BY_NAME["attributes"].code)
        self.upstream = _chain_code("physics")

    def test_reachable_package_modules_are_covered(self) -> None:
        roots = [
            PROJECT_ROOT / "src/attrs/report.py",
            PROJECT_ROOT / "src/attrs/store.py",
        ]
        seen: set[Path] = set()
        todo = list(roots)
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            todo += [g for g in _imports(f) if _in_packages(g)]
        reached = {p.relative_to(PROJECT_ROOT).as_posix() for p in seen}
        # las importaciones diferidas que motivaron el paso 0
        for must in ("src/ir/legacy_facts.py", "src/importers/beamng.py"):
            self.assertIn(must, reached)
        self.assertEqual(sorted(reached - self.code), [])

    def test_direct_outside_imports_are_covered(self) -> None:
        missing = set()
        for pkg in PACKAGES:
            for f in (PROJECT_ROOT / pkg).glob("*.py"):
                if f.name == "__main__.py":
                    continue
                for g in _imports(f):
                    rel = g.relative_to(PROJECT_ROOT).as_posix()
                    if not _in_packages(g) and rel not in self.code | self.upstream:
                        missing.add(f"{f.name} -> {rel}")
        self.assertEqual(sorted(missing), [])

    def test_every_package_file_is_listed(self) -> None:
        for pkg in PACKAGES:
            for f in (PROJECT_ROOT / pkg).glob("*.py"):
                if f.name != "__main__.py":
                    self.assertIn(f.relative_to(PROJECT_ROOT).as_posix(), self.code)

    def test_change_in_ir_or_importer_changes_hash(self) -> None:
        code = BY_NAME["attributes"].code
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in code:
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(PROJECT_ROOT / rel, dst)
            with mock.patch.object(orchestrator, "PROJECT_ROOT", root):
                h0 = orchestrator._code_hash(code)
                self.assertEqual(h0, orchestrator._code_hash(code))
                hashes = {h0}
                for rel in ("src/ir/legacy_facts.py", "src/importers/beamng.py"):
                    with open(root / rel, "a", encoding="utf-8") as f:
                        f.write("\n# cambio\n")
                    hashes.add(orchestrator._code_hash(code))
                self.assertEqual(len(hashes), 3)


if __name__ == "__main__":
    unittest.main()
