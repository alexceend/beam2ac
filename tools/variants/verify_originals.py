"""Comprueba los scripts originales preservados en ``tools/variants/original``.

    python tools/variants/verify_originals.py [--base tools/variants/original]

``SHA256SUMS.txt`` tiene una línea por archivo:
``<sha256>  <ruta relativa>  <- <origen>`` (con CRLF). ``sha256sum -c`` no lo entiende
por la parte ``<- <origen>``; este script sí.
Solo lectura. Código de salida 0 si los archivos listados coinciden y no hay archivos
sin listar; 1 en otro caso. Fase A (revisión 2026-11-10).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent / "original"
SUMS = "SHA256SUMS.txt"


@dataclass(frozen=True)
class Entry:
    sha256: str
    rel: str
    origin: str


def parse_sums(text: str) -> list[Entry]:
    entries = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, rest = line.split("  ", 1)
        except ValueError:
            raise ValueError(f"{SUMS}:{n}: línea sin «<sha256>  <ruta>»") from None
        rel, _, origin = rest.partition("  <- ")
        if len(digest) != 64 or not rel.strip():
            raise ValueError(f"{SUMS}:{n}: línea mal formada")
        entries.append(Entry(digest.lower(), rel.strip(), origin.strip()))
    return entries


def verify(base: Path = BASE) -> list[str]:
    """Lista de problemas (vacía si todo coincide)."""
    entries = parse_sums((base / SUMS).read_text(encoding="utf-8"))
    problems = []
    listed = set()
    for e in entries:
        listed.add(e.rel)
        p = base / e.rel
        if not p.is_file():
            problems.append(f"falta {e.rel}")
            continue
        if hashlib.sha256(p.read_bytes()).hexdigest() != e.sha256:
            problems.append(f"cambiado {e.rel}")
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        if (
            p.is_file()
            and rel != SUMS
            and rel not in listed
            and "__pycache__" not in rel
        ):
            problems.append(f"sin listar {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", type=Path, default=BASE)
    args = ap.parse_args(argv)
    problems = verify(args.base)
    n = len(parse_sums((args.base / SUMS).read_text(encoding="utf-8")))
    for p in problems:
        print(p)
    print(f"{n} archivos listados; problemas: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
