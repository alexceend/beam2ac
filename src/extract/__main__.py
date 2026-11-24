"""Masa y geometría de una configuración BeamNG.

Uso:  python -m src.extract <carpeta_vehiculo> <archivo.pc> [--json RUTA]

Sin ``--json`` no escribe nada. Con ``--json`` escribe el informe en la ruta dada
(rechaza rutas dentro de las muestras de referencia).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.jbeam import PartIndex, load_pc, resolve

from .mass_geometry import check_reference_mass, compute_mass_geometry, write_report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m src.extract", description=__doc__)
    parser.add_argument("vehicle_dir")
    parser.add_argument("pc")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args(argv)

    resolved = resolve(PartIndex.from_directories(args.vehicle_dir), load_pc(args.pc))
    g = compute_mass_geometry(resolved)
    info = Path(args.vehicle_dir) / f"info_{Path(args.pc).stem}.json"
    ref = check_reference_mass(g, info)

    print(f"Nodos: {g.node_count}  masa nodos: {g.node_mass:.2f} kg")
    print(f"Ruedas generadas: {g.wheel_generated_mass:.2f} kg")
    print(f"MASA TOTAL: {g.total_mass:.2f} kg")
    if ref:
        print(f"  referencia {ref.reference_kg:g} kg -> {ref.explanation}")
    print(f"CdG BeamNG {tuple(round(v, 4) for v in g.cog_beamng)}")
    print(f"CdG AC     {tuple(round(v, 4) for v in g.cog_ac)}")
    for w in g.wheels:
        print(
            f"  rueda {w.name:4} {w.side:6} centro AC "
            f"{tuple(round(v, 4) for v in w.center_ac)} r={w.radius} "
            f"masa gen.={w.generated_mass:.2f} kg I~{w.spin_inertia:.3f} kg·m²"
        )
    for a in g.axles:
        track = f"{a.track:.4f} m" if a.track is not None else "—"
        print(f"  eje {a.index}: z_AC={a.longitudinal_ac:.4f} vía={track} {a.wheels}")
    if g.wheelbase is not None:
        print(
            f"Batalla: {g.wheelbase:.4f} m  "
            f"reparto delantero: {g.front_axle_fraction:.4f}"
        )
    if g.cog_height is not None:
        print(f"Altura CdG sobre suelo (hipótesis): {g.cog_height:.4f} m")
    i = g.inertia_ac
    print(
        f"Inercia AC (aprox.): pitch {i[0][0]:.1f}  "
        f"yaw {i[1][1]:.1f}  roll {i[2][2]:.1f}"
    )
    for issue in g.issues:
        print(f"  aviso: {issue}")
    if args.json_path:
        print(f"Informe: {write_report(g, args.json_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
