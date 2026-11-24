# Empaquetado (PyInstaller)

    .venv-ui\Scripts\python.exe -m pip install pyinstaller
    .venv-ui\Scripts\python.exe -m PyInstaller packaging/beam2ac.spec --noconfirm --distpath output/installer/dist --workpath output/installer/build

Resultado: `output/installer/dist/beam2ac/beam2ac.exe` (carpeta autónoma; se distribuye entera, p. ej. en un zip).

- Instalado, lo que se escribe (proyectos, exportaciones, ajustes) va a `%LOCALAPPDATA%\beam2ac` (`BEAM2AC_DATA_DIR` lo cambia) y `%APPDATA%\beam2ac`; la carpeta del programa es de solo lectura.
- No incluye Blender, FMOD Studio, Assetto Corsa ni BeamNG: el usuario indica sus rutas en Ajustes.
- No incluye `docs/references` (SDK de FMOD, sin permiso de redistribución): sin él la generación del banco FMOD informa de que falta.
- Prueba: `beam2ac.exe --selftest --out <carpeta nueva>` (con datos vacíos `ok` sale false porque no hay proyectos).
