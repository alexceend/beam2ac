"""G2.22 F5: repetibilidad entre dos ejecuciones independientes del banco y veredicto final.

    python -m tools.acbench.repeat <analisis_A> <analisis_B> --out output/<nueva>

``<analisis_X>`` = carpetas de :mod:`tools.acbench.analysis` (``resultados.json`` +
``veredicto.json``) de ejecuciones con sesiones de AC DISTINTAS. Regla pre-registrada
(``preregistro.json``): un parámetro queda en ``coincide_lectura_C`` solo si lo está en las dos
y sus medianas de ρ difieren ≤ 0,05; si no, ``no_repetido`` (o el peor estado de las dos).
El ``veredicto.json`` resultante mantiene el esquema de G2.21 (``beam2ac.g221.lsd_ac_verdict/1``)
para la puerta de ``tools/catalog/add_lsd_awd_200409_rules.py``. Este script NO ejecuta esa
herramienta.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.safe_paths import claim_output_dir, ensure_safe_output_path

MAX_MEDIAN_DIFF = 0.05
MATCH = "coincide_lectura_C"


def combine(a: dict, b: dict, va: dict, vb: dict) -> tuple[dict, list[str]]:
    params, lines = {}, []
    for k in sorted(set(a["params"]) | set(b["params"])):
        pa, pb = a["params"].get(k, {}), b["params"].get(k, {})
        sa, sb = pa.get("status", "ausente"), pb.get("status", "ausente")
        ma, mb = pa.get("rho_median"), pb.get("rho_median")
        diff = abs(ma - mb) if ma is not None and mb is not None else None
        if sa == MATCH and sb == MATCH and diff is not None and diff <= MAX_MEDIAN_DIFF:
            st = MATCH
        elif sa == MATCH or sb == MATCH:
            st = "no_repetido"
        else:
            st = sa if sa == sb else f"{sa}|{sb}"
        params[k] = st
        f = (lambda x: f"{x:.4f}" if x is not None else "—")
        lines.append(f"| {k} | {f(ma)} ({pa.get('blocks', 0)}) · {sa} | {f(mb)} ({pb.get('blocks', 0)}) · {sb} | "
                     f"{f(diff)} | {st} |")
    return params, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    x = ap.parse_args(argv)
    ra, rb = (json.loads((p / "resultados.json").read_text("utf-8")) for p in (x.a, x.b))
    va, vb = (json.loads((p / "veredicto.json").read_text("utf-8")) for p in (x.a, x.b))
    files_a = {i["sha256"] for i in va["inputs"]}
    files_b = {i["sha256"] for i in vb["inputs"]}
    if files_a & files_b:
        raise SystemExit("las dos ejecuciones comparten CSV: no son independientes")
    params, lines = combine(ra, rb, va, vb)
    source = "ac_in_game" if va["source"] == vb["source"] == "ac_in_game" else "sintetico"
    verdict = {
        "schema": "beam2ac.g221.lsd_ac_verdict/1",
        "analyzer": "tools/acbench/analysis.py + tools/acbench/repeat.py (G2.22 F5)",
        "source": source,
        "acs_exe": va["acs_exe"],
        "acs_matches_g211_analysis": va["acs_matches_g211_analysis"] and vb["acs_matches_g211_analysis"],
        "inputs": va["inputs"] + vb["inputs"],
        "params": params,
        "repeatability": {"run_a": str(x.a), "run_b": str(x.b), "max_median_diff": MAX_MEDIAN_DIFF},
        "criteria": va.get("criteria"),
        "all_measured_match_reading": all(s == MATCH for s in params.values()),
    }
    out = claim_output_dir(ensure_safe_output_path(x.out))
    (out / "veredicto.json").write_text(json.dumps(verdict, indent=1, ensure_ascii=False), "utf-8")
    md = ["# Repetibilidad del banco LSD (F5)", "", f"A: `{x.a}` · B: `{x.b}` · fuente: **{source}**", "",
          "| Parámetro | A: ρ mediana (bloques) · estado | B: ρ mediana (bloques) · estado | dif. | final |",
          "|---|---|---|---|---|"] + lines
    (out / "repetibilidad.md").write_text("\n".join(md) + "\n", "utf-8")
    sys.stdout.buffer.write(("\n".join(md) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
