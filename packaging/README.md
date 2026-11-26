# Empaquetado (PyInstaller)

    .venv-ui\Scripts\python.exe -m pip install pyinstaller
    .venv-ui\Scripts\python.exe -m PyInstaller packaging/beam2ac.spec --noconfirm --distpath output/installer/dist --workpath output/installer/build

Resultado: `output/installer/dist/beam2ac/beam2ac.exe` (carpeta autónoma; se distribuye entera, p. ej. en un zip).

- Instalado, lo que se escribe (proyectos, exportaciones, ajustes) va a `%LOCALAPPDATA%\beam2ac` (`BEAM2AC_DATA_DIR` lo cambia) y `%APPDATA%\beam2ac`; la carpeta del programa es de solo lectura.
- No incluye Assetto Corsa ni BeamNG: el usuario indica sus rutas en Ajustes.
- No incluye `docs/references` (material de referencia de terceros, sin permiso de redistribución). El banco de sonido se genera con el esqueleto de `src/audio/data/`.
- Prueba: `beam2ac.exe --selftest --out <carpeta nueva>` (con datos vacíos `ok` sale false porque no hay proyectos).
