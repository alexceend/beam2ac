# PyInstaller: .venv-ui\Scripts\python.exe -m PyInstaller packaging/beam2ac.spec --noconfirm
# Datos de solo lectura (código fuente incluido: las huellas de etapas y el esqueleto de banco de sonido lo necesitan).
# NO se empaqueta docs/references: restricción de redistribución.
import sys
from pathlib import Path

root = Path(SPECPATH).parent
datas = [
    (str(root / "src"), "src"),
    (str(root / "catalog"), "catalog"),
    (str(root / "pyproject.toml"), "."),
]
# measurements/ no se versiona (medidas de coches de terceros): solo se empaqueta si existe en local.
if (root / "measurements").is_dir():
    datas.append((str(root / "measurements"), "measurements"))
a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=["webview.platforms.winforms", "clr_loader", "pythonnet"] if sys.platform == "win32" else [],
    excludes=["pytest", "black", "flake8"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="beam2ac", console=sys.platform != "win32")
coll = COLLECT(exe, a.binaries, a.datas, name="beam2ac")
