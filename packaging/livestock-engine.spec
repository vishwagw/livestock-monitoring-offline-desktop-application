# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone spatial engine.

Built as a one-folder bundle: it starts in well under a second (a one-file
build would unpack ~100 MB to a temp folder on every run) and is shipped
inside the Electron app under ``resources/engine``.

    pyinstaller --noconfirm --clean packaging/livestock-engine.spec
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

# pyproj needs PROJ's database (proj.db) at runtime; sklearn's clustering
# pulls in compiled submodules that static analysis can miss.
datas = collect_data_files("pyproj")
hiddenimports = (
    collect_submodules("livestock_engine")
    + collect_submodules("sklearn.cluster")
    + collect_submodules("sklearn.neighbors")
    + collect_submodules("sklearn.utils")
    + ["scipy.optimize", "scipy.spatial.distance"]
)

# Nothing interactive, plotting or test-related is needed in the field.
# (unittest must stay: scipy imports numpy.testing at runtime.)
excludes = [
    "tkinter", "_tkinter", "matplotlib", "IPython", "jupyter", "notebook",
    "pytest", "_pytest", "pandas", "PIL", "docutils", "sphinx", "pydoc_data",
    "lib2to3", "sklearn.datasets.tests", "sklearn.tests",
    "scipy.tests", "numpy.tests",
]

a = Analysis(
    [str(ROOT / "packaging" / "engine_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="livestock-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != "win32",
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=sys.platform != "win32",
    upx=False,
    name="livestock-engine",
)
