@echo off
rem Compila beam2ac a .exe (PyInstaller, carpeta autonoma). Ejecutar desde cualquier sitio.
rem Resultado: output\installer\dist\beam2ac\beam2ac.exe  (ver packaging\README.md)
setlocal
cd /d "%~dp0.."
set PY=.venv-ui\Scripts\python.exe

echo [1/6] Entorno .venv-ui
if not exist %PY% (
    python -m venv .venv-ui || goto :fail
)

echo [2/6] Dependencias de la interfaz (requirements-ui.txt, con huellas)
%PY% -m pip install --require-hashes -r requirements-ui.txt || goto :fail

echo [3/6] PyInstaller
%PY% -m PyInstaller --version >nul 2>&1 || %PY% -m pip install pyinstaller || goto :fail

echo [4/6] Compilacion
%PY% -m PyInstaller packaging\beam2ac.spec --noconfirm --clean --distpath output\installer\dist --workpath output\installer\build || goto :fail

echo [5/6] Autoprueba (salida en carpeta nueva)
set OUT=output\installer\selftest_%RANDOM%%RANDOM%
output\installer\dist\beam2ac\beam2ac.exe --selftest --out %OUT%
rem Con datos vacios la autoprueba da ok=false (no hay proyectos): es esperado, no es un fallo de compilacion.

echo [6/6] Zip listo para distribuir
set DIST=output\installer\dist\beam2ac
copy /y LICENSE %DIST%\LICENSE >nul
(
echo beam2ac - plug and play
echo.
echo 1. Descomprime TODA la carpeta en cualquier sitio con permiso de escritura ^(no hace falta instalar nada^).
echo 2. Abre beam2ac.exe.
echo 3. En Ajustes indica las rutas de Assetto Corsa y BeamNG ^(no se incluyen^).
echo.
echo Requisito de Windows: Microsoft Edge WebView2 Runtime ^(ya viene en Windows 11 y en Windows 10 actualizado^).
echo Tus proyectos y ajustes se guardan en %%LOCALAPPDATA%%\beam2ac y %%APPDATA%%\beam2ac.
) > %DIST%\LEEME.txt
del /q output\installer\beam2ac-win64.zip 2>nul
tar -a -cf output\installer\beam2ac-win64.zip -C output\installer\dist beam2ac || goto :fail

echo.
echo Listo: output\installer\beam2ac-win64.zip  ^(exe: %DIST%\beam2ac.exe^)
pause
exit /b 0

:fail
echo.
echo ERROR: la compilacion se detuvo (codigo %ERRORLEVEL%).
pause
exit /b 1
