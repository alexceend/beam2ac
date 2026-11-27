"""python -m src.app serve … (editor de un vehículo; src/app/server.py) y
python -m src.app desktop [--projects DIR] [--port N] [--open] (aplicación de
escritorio con varios proyectos; src/app/desktop.py). Imprimen la URL;
`desktop --open` además abre el navegador (`--window` usa su propia ventana)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.pipeline.physics_preview import (
    load_vehicle_dir,
    load_vehicle_zip,
    preview_physics,
)

from .server import make_server
from .service import ReviewService


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m src.app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--vehicle-dir", type=Path)
    s.add_argument("--vehicle-zip", type=Path)
    s.add_argument("--common-zip", type=Path)
    s.add_argument("--pc")
    s.add_argument(
        "--project", type=Path, required=True, help="carpeta del proyecto bajo output/"
    )
    s.add_argument(
        "--refs", type=Path, help="index.json del catálogo de referencias de AC"
    )
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--driver-mass", type=float, default=70.0)
    dk = sub.add_parser("desktop", help="aplicación de escritorio (varios proyectos)")
    dk.add_argument("--projects", type=Path, default=None)
    dk.add_argument("--port", type=int, default=8766)
    dk.add_argument("--open", action="store_true", help="abrir el navegador")
    dk.add_argument(
        "--window", action="store_true", help="ventana nativa (entorno .venv-ui)"
    )
    args = ap.parse_args(argv)
    if args.cmd == "desktop" and args.window:
        # pywebview vive en .venv-ui: se relanza ahí la ventana (src.app.desktop_window)
        import subprocess

        py = Path(__file__).resolve().parents[2] / ".venv-ui" / "Scripts" / "python.exe"
        if not py.is_file():  # instalación sin .venv-ui: pywebview en el Python actual
            py = Path(sys.executable)
        cmd = [str(py), "-m", "src.app.desktop_window"]
        if args.projects:
            cmd += ["--projects", str(args.projects)]
        return subprocess.call(cmd, cwd=Path(__file__).resolve().parents[2])
    if args.cmd == "desktop":
        from . import settings
        from .desktop import make_desktop_server
        from .workspace import PROJECTS_ROOT, Workspace

        settings.apply()  # rutas guardadas por el usuario (AC, Steam, BeamNG)
        projects = args.projects or settings.projects_dir() or PROJECTS_ROOT
        server = make_desktop_server(Workspace(projects), args.port)
        host, port = server.server_address[:2]
        print(f"beam2ac (escritorio): http://{host}:{port}/  (Ctrl+C para salir)")
        if args.open:
            import webbrowser

            webbrowser.open(f"http://{host}:{port}/")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if bool(args.vehicle_dir) == bool(args.vehicle_zip):
        ap.error("indica --vehicle-dir o --vehicle-zip")
    if args.vehicle_dir:
        if not args.pc:
            ap.error("--vehicle-dir necesita --pc")
        source = load_vehicle_dir(args.vehicle_dir, args.pc)
    else:
        source = load_vehicle_zip(args.vehicle_zip, args.common_zip, args.pc)
    refs = json.loads(args.refs.read_text(encoding="utf-8")) if args.refs else None
    service = ReviewService(
        preview_physics(source),
        args.project,
        driver_mass_kg=args.driver_mass,
        reference_index=refs,
    )
    server = make_server(service, args.port)
    host, port = server.server_address[:2]
    print(f"Interfaz de atributos: http://{host}:{port}/  (Ctrl+C para salir)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    from src.ac import tyre_fit

    tyre_fit.PARALLEL = True  # este módulo tiene guarda: seguro con ``spawn``
    sys.exit(main())
