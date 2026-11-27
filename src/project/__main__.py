"""Proyectos de conversión: crear, ejecutar etapas y ver el estado.

    python -m src.project new --out output/projects/<nombre>
        (--vehicle-dir D --pc P | --vehicle-zip Z [--common-zip C] [--pc P])
    python -m src.project run output/projects/<nombre> [--stage ...]
    python -m src.project status output/projects/<nombre>
    python -m src.project export output/projects/<nombre> [--car-id ID] [--no-model]
        [--local-driver --ac-root RUTA_AC] [--audio-bank DIR [--audio-source-id ID] | --audio-build]
    python -m src.project inspect output/projects/<nombre>/exports/<NNN>

``run`` reutiliza los resultados cuyas entradas no han cambiado y nunca sobrescribe.
``run --with-model`` añade la etapa ``model`` (KN5 en Python puro). ``export`` ejecuta
las etapas (con el modelo) y escribe un intento NUEVO en ``exports/<NNN>/`` con el
paquete, su manifiesto y su informe; no instala nada. ``inspect`` comprueba un intento
(hashes y veredicto) sin modificarlo.
Las decisiones de atributos se editan con
``python -m src.app serve --project <carpeta>`` (misma carpeta ``decisions/``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .manifest import (
    ProjectError,
    SourceSpec,
    create_project,
    list_zip_configs,
    load_project,
)
from .orchestrator import project_status, run_project
from .stages import STAGES, STAGES_WITH_MODEL


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="python -m src.project",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="crea un proyecto (no sobrescribe)")
    n.add_argument("--out", type=Path, required=True)
    n.add_argument("--vehicle-dir", type=Path)
    n.add_argument("--vehicle-zip", type=Path)
    n.add_argument("--common-zip", type=Path)
    n.add_argument("--pc")
    n.add_argument("--driver-mass", type=float, default=70.0)
    n.add_argument("--notes", default="")
    n.add_argument(
        "--reference-data",
        type=Path,
        help="carpeta data/ de referencia (solo lectura) para comparar los INI "
        "resueltos",
    )
    r = sub.add_parser("run", help="ejecuta las etapas (con caché)")
    r.add_argument("project", type=Path)
    r.add_argument("--stage", action="append", help="solo esta etapa (y sus previas)")
    r.add_argument(
        "--with-model", action="store_true", help="incluye la etapa model (KN5)"
    )
    e = sub.add_parser("export", help="paquete de AC en exports/<NNN>/ (no instala)")
    e.add_argument("project", type=Path)
    e.add_argument("--car-id", help="carpeta del coche (por defecto beam2ac_<id>)")
    e.add_argument(
        "--no-model", action="store_true", help="sin modelo 3D (el KN5 faltará)"
    )
    e.add_argument(
        "--local-driver",
        action="store_true",
        help="pose y manos del piloto (lee un .knh de Kunos de --ac-root; "
        "paquete NO distribuible)",
    )
    e.add_argument("--ac-root", type=Path, help="instalación de AC (solo lectura)")
    e.add_argument(
        "--allow-installed-car-id",
        action="store_true",
        help="permite un --car-id que ya está en content/cars (instalarlo lo "
        "sustituiría); queda como advertencia en el informe",
    )
    e.add_argument("--audio-bank", type=Path, help="carpeta con <id>.bank y GUIDs.txt")
    e.add_argument("--abs", choices=("auto", "on", "off"), default="auto")
    e.add_argument("--abs-levels", type=int, default=0, help="0 = solo on/off; 2-20")
    e.add_argument("--tc", choices=("auto", "on", "off"), default="auto")
    e.add_argument("--tc-levels", type=int, default=0, help="0 = solo on/off; 2-20")
    e.add_argument(
        "--thermal-tyres",
        choices=["street", "slick", "colab"],
        help="plantilla térmica de neumáticos (diseño, g254)",
    )
    e.add_argument(
        "--thermal-brakes",
        choices=[
            "auto",
            "basic",
            "premium",
            "sport",
            "semi-race",
            "full-race",
            "carbon-ceramic",
        ],
        help="[TEMPS_*] de frenos (hip, g254); auto = pastilla y discos del JBeam",
    )
    e.add_argument(
        "--no-backfire",
        action="store_true",
        help="no escribe [BACKFIRE] en sounds.ini (sin él AC usa MINRPM=9750 y no hay llamas)",
    )
    e.add_argument("--backfire-min-rpm", type=float, help="MINRPM de [BACKFIRE]")
    e.add_argument("--backfire-max-rpm", type=float, help="MAXRPM de [BACKFIRE]")
    e.add_argument(
        "--backfire-max-gas", type=float, help="MAXGAS de [BACKFIRE] (≤ 0,3)"
    )
    e.add_argument(
        "--audio-build",
        action="store_true",
        help="genera el banco de sonido del motor desde los WAV del origen. "
        "Ignorado si se pasa --audio-bank",
    )
    e.add_argument(
        "--audio-source-id",
        help="id para el que se construyó el banco (si es otro: se reasigna)",
    )
    b = sub.add_parser("builds", help="builds/: última build funcional por car_id")
    b.add_argument(
        "action",
        choices=["scan", "list"],
        help="scan = última por vehículo del histórico",
    )
    b.add_argument(
        "--apply",
        action="store_true",
        help="con scan: promociona (copia; no toca output/)",
    )
    i = sub.add_parser("inspect", help="comprueba un intento de exportación")
    i.add_argument("attempt", type=Path)
    s = sub.add_parser("status", help="resultados guardados por etapa")
    s.add_argument("project", type=Path)
    c = sub.add_parser("configs", help="lista las configuraciones (.pc) de un zip")
    c.add_argument("vehicle_zip", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "configs":
            print("\n".join(list_zip_configs(args.vehicle_zip)))
            return 0
        if args.cmd == "new":
            if bool(args.vehicle_dir) == bool(args.vehicle_zip):
                ap.error("indica --vehicle-dir o --vehicle-zip")
            spec = (
                SourceSpec("dir", str(args.vehicle_dir), args.pc)
                if args.vehicle_dir
                else SourceSpec(
                    "zip",
                    str(args.vehicle_zip),
                    args.pc,
                    str(args.common_zip) if args.common_zip else None,
                )
            )
            proj = create_project(
                args.out,
                spec,
                args.driver_mass,
                args.notes,
                reference_data=args.reference_data,
            )
            print(f"proyecto creado: {proj.root}")
            return 0
        if args.cmd == "builds":
            from . import builds as B
            from src.safe_paths import BUILDS_DIR

            if args.action == "list":
                for d in sorted(BUILDS_DIR.glob("beam2ac_*/build.json")):
                    print(
                        d.parent.name,
                        json.loads(d.read_text(encoding="utf-8"))["promoted"],
                    )
                return 0
            for c in B.scan_history():
                r = B.promote(c["attempt"]) if args.apply else {"promoted": None}
                print(
                    c["car_id"],
                    c["created"],
                    c["attempt"],
                    "->",
                    r.get("path") or r.get("reason") or "(dry-run)",
                )
            return 0
        if args.cmd == "inspect":
            from .export import inspect_export

            info = inspect_export(args.attempt)
            print(json.dumps(info, indent=1, ensure_ascii=False))
            return 0 if info["intact"] else 3
        proj = load_project(args.project)
        if args.cmd == "status":
            print(json.dumps(project_status(proj), indent=1, ensure_ascii=False))
            return 0
        if args.cmd == "export":
            from .export import default_ac_root, export_project

            try:
                res = export_project(
                    proj,
                    args.car_id,
                    with_model=not args.no_model,
                    local_driver=args.local_driver,
                    ac_root=args.ac_root,
                    audio_bank=args.audio_bank,
                    audio_source_id=args.audio_source_id,
                    audio_build=args.audio_build,
                    driver_aids=(
                        {
                            "abs": args.abs,
                            "abs_levels": args.abs_levels,
                            "tc": args.tc,
                            "tc_levels": args.tc_levels,
                        }
                        if (args.abs, args.abs_levels, args.tc, args.tc_levels)
                        != ("auto", 0, "auto", 0)
                        else None
                    ),
                    thermal_tyres=args.thermal_tyres,
                    backfire=not args.no_backfire,
                    backfire_params={
                        "min_rpm": args.backfire_min_rpm,
                        "max_rpm": args.backfire_max_rpm,
                        "max_gas": args.backfire_max_gas,
                    },
                    thermal_brakes=args.thermal_brakes,
                    allow_installed_car_id=args.allow_installed_car_id,
                    # Fase A: sin --ac-root, la colisión de car_id se comprueba contra
                    # la instalación local conocida (solo lectura)
                    car_id_check_root=args.ac_root or default_ac_root(),
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"intento: {res.dir}")
            print(f"veredicto: {res.verdict}")
            print(f"errores: {len(res.errors)}; advertencias: {len(res.warnings)}")
            for issue in res.errors[:30]:
                print(f"  ERROR {issue.code} {issue.target} {issue.detail}")
            print(f"informe: {res.dir / 'export_report.md'}")
            b = res.build or {}
            print(
                f"builds: {b.get('path') if b.get('promoted') else 'no promovida: ' + str(b.get('reason'))}"
            )
            return 0 if not res.errors else 2
        stages = STAGES_WITH_MODEL if args.with_model else STAGES
        results = run_project(proj, stages, tuple(args.stage) if args.stage else None)
        return 0 if all(r.status in ("ok", "cached") for r in results) else 2
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from src.ac import tyre_fit

    tyre_fit.PARALLEL = True  # este módulo tiene guarda: seguro con ``spawn``
    sys.exit(main())
