"""Punto de entrada del ejecutable (PyInstaller): ventana nativa en Windows, navegador en Linux."""

import sys

if __name__ == "__main__":
    import multiprocessing

    # en el ejecutable, los procesos hijos entran por aquí: sin esto abrirían otra app
    multiprocessing.freeze_support()
    from src.ac import tyre_fit

    tyre_fit.PARALLEL = True
    if sys.platform == "win32":
        from src.app.desktop_window import main

        sys.exit(main())
    # Linux: pywebview exige GTK/Qt del sistema (no empaquetable) -> servidor local + navegador.
    from src.app.__main__ import main

    sys.exit(main(["desktop", "--open"]))
