"""Inspección de solo lectura de un vehículo BeamNG.

Uso:  python -m src.jbeam <carpeta_vehiculo> <archivo.pc> [carpetas_extra...]

Imprime el árbol de piezas activas, avisos y tolerancias aplicadas. No escribe
ningún archivo.
"""

from __future__ import annotations

import argparse
import collections
import sys

from .config import load_pc
from .parts import PartIndex
from .resolver import resolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.jbeam", description=__doc__)
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("extra_dirs", nargs="*", help="p. ej. la carpeta common/")
    args = parser.parse_args(argv)

    index = PartIndex.from_directories(args.vehicle_dir, *args.extra_dirs)
    pc = load_pc(args.pc)
    resolved = resolve(index, pc)

    kinds: collections.Counter[str] = collections.Counter()
    for notes in index.leniencies.values():
        kinds.update(n.kind for n in notes)
    print(
        f"Archivos .jbeam: {len(index.files)}  piezas: {len(index.parts)}  "
        f"errores de sintaxis: {len(index.file_errors)}"
    )
    print(f"Tolerancias aplicadas: {dict(kinds)}")
    for path, exc in index.file_errors.items():
        print(f"  ERROR {exc}")
    print(f"\nPiezas activas ({len(resolved.active)}), raíz '{resolved.root}':")
    for item in resolved.active:
        mark = "" if item.chosen_by != "pc" else "  [.pc]"
        print(f"{'  ' * item.depth}{item.name}{mark}")
    print(f"\nVariables: {len(resolved.variables)}")
    print(f"Avisos ({len(resolved.warnings) + len(pc.issues)}):")
    for warning in [*pc.issues, *resolved.warnings]:
        print(f"  - {warning}")
    return 0 if not index.file_errors else 1


if __name__ == "__main__":
    sys.exit(main())
