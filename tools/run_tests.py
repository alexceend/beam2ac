"""Lanzador de los tres niveles de pruebas.

    python tools/run_tests.py habitual   # nivel 1: tests/ sin los barridos «ampliada»
    python tools/run_tests.py ampliada   # nivel 2: tests/ completo (habitual + ampliada)
    python tools/run_tests.py completa   # nivel 3: tests/ + tests_not_required/ (TODO)

Los argumentos adicionales se pasan a pytest (p. ej. ``-k provenance``, ``--junitxml=x.xml``).
El nivel 3 no depende de recordar carpetas: las lista este archivo (comprobado por
``tests/test_test_tiers.py``).
"""

from __future__ import annotations

import subprocess
import sys

ALL_DIRS = ["tests", "tests_not_required"]
LEVELS = {
    "habitual": ["tests"],  # pytest.ini añade -m "not ampliada"
    "ampliada": ["tests", "-m", ""],
    "completa": [*ALL_DIRS, "-m", ""],
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in LEVELS:
        print(__doc__)
        return 2
    return subprocess.call(
        [sys.executable, "-m", "pytest", *LEVELS[argv[0]], *argv[1:]]
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
