"""G2.22: ejecuta el banco LSD autónomo en AC (un coche por sesión) y guarda los registros.

    python -m tools.acbench.run_bench --stage <preparación> --state <estado> --out output/<nueva>
        [--cars a1 a2 …] [--items N12+ D30- …] [--scale 0.5] [--run-id r01]

Por cada coche: instala si falta, lanza AC (salida inmediata), ejecuta el plan
(:mod:`tools.acbench.plan`) con la máquina de estados (:mod:`tools.acbench.runner`), cierra AC
y copia el CSV de la app a ``<out>/<car_id>_<run_id>.csv`` junto a ``.segments.json``. Nada se
analiza aquí (``python -m tools.acbench.analysis <out> --out …``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from src.safe_paths import ensure_safe_output_path
from tools.acbench import analysis, install
from tools.acbench.plan import VariantPlan, plan_for
from tools.acbench.runner import AcIo, Runner
from tools.acbench.session import AcSession

APP_LOGS = install.AC_ROOT / "apps" / "lua" / install.APP_ID / "registros"


def run_car(car_id: str, stage: Path, state: Path, out: Path, run_id: str, items: list[str] | None,
            scale: float, seg_base: int, extra: bool = False) -> dict:
    plan = plan_for(car_id, scale, extra)
    if items:
        plan = VariantPlan(car_id, [it for it in plan.items if it.key in items])
    variant = analysis.BY_ID[car_id]
    sess_dir = out / "sesiones" / f"{car_id}_{run_id}"
    t0 = time.time()
    status = "ok"
    reps = []
    try:
        with AcSession(car_id, state, stage, sess_dir) as s:
            s.wait_ready(timeout=300)
            menu = s.leave_menu()
            if not menu["ok"]:
                raise RuntimeError("AC sigue en el menú de salida")
            runner = Runner(AcIo(s), plan, variant, out, run_id, seg_base=seg_base)
            reps = runner.run()
    except Exception as e:  # registra y sigue con el siguiente coche
        status = f"error: {e!r}"
    csv_src = APP_LOGS / f"{car_id}_{run_id}.csv"
    if csv_src.is_file():
        shutil.copy2(csv_src, out / csv_src.name)
    return {"car_id": car_id, "run_id": run_id, "status": status, "minutes": round((time.time() - t0) / 60, 1),
            "reps": len(reps), "valid": sum(r.valid for r in reps), "saturated": sum(r.saturated for r in reps),
            "csv": csv_src.is_file()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", type=Path, required=True)
    ap.add_argument("--state", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cars", nargs="*", default=None, help="sufijos (a0 … b0) o ids completos")
    ap.add_argument("--items", nargs="*", default=None)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--run-id", default=time.strftime("r%Y%m%d_%H%M%S"))
    ap.add_argument("--seg-base", type=int, default=0)
    ap.add_argument("--extra", action="store_true", help="solo el plan suplementario (plan.EXTRA)")
    a = ap.parse_args(argv)
    out = ensure_safe_output_path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cars = [c if c.startswith("beam2ac_") else f"beam2ac_g221lsd_{c}" for c in (a.cars or [
        "a0", "a1", "a2", "a3", "a4", "b0", "a5"])]
    summary = []
    for k, car in enumerate(cars):
        r = run_car(car, a.stage, a.state, out, a.run_id, a.items, a.scale, a.seg_base + 10000 * k, a.extra)
        summary.append(r)
        print(json.dumps(r, ensure_ascii=False), flush=True)
        (out / f"resumen_{a.run_id}.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
