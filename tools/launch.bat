@echo off
set "ROOT=%~dp0.."

:: Load venv
call "%ROOT%\.venv-ui\Scripts\activate.bat"

:: Move to project root
cd /d "%ROOT%"

:: Execute Python
python -m src.app desktop --window
