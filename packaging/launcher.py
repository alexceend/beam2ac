"""Punto de entrada del ejecutable (PyInstaller): ventana nativa en Windows, navegador en Linux."""

import sys

if __name__ == "__main__":
    if sys.platform == "win32":
        from src.app.desktop_window import main

        sys.exit(main())
    # Linux: pywebview exige GTK/Qt del sistema (no empaquetable) -> servidor local + navegador.
    from src.app.__main__ import main

    sys.exit(main(["desktop", "--open"]))
