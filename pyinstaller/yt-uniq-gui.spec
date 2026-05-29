# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for yt-uniq-gui (v0.5.4).
#
# Build:
#   pip install pyinstaller
#   pyinstaller pyinstaller/yt-uniq-gui.spec --clean
#
# Output:
#   dist/yt-uniq-gui.app          (macOS bundle)
#   dist/yt-uniq-gui/             (Windows / Linux dir distribution)
#
# Caveats:
# - Unsigned. macOS Gatekeeper / Windows SmartScreen will warn on first launch.
# - PyQt6-WebEngine pulls ~150 MB of Chromium; resulting bundle is large.
# - If your platform fails, fall back to: pipx install 'yt-uniquifier[gui]'

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Bundle YAML profiles + QA HTML template.
datas = []
datas += collect_data_files("yt_uniquifier", subdir="profiles", include_py_files=False)
datas += collect_data_files(
    "yt_uniquifier", subdir="core/qa/templates", include_py_files=False,
)

hiddenimports = []
hiddenimports += collect_submodules("PyQt6")

a = Analysis(
    ["../src/yt_uniquifier/gui/app_pyqt.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tests"],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="yt-uniq-gui",
    console=False,
    icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name="yt-uniq-gui",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="yt-uniq-gui.app",
        icon=None,
        bundle_identifier="com.yt-uniquifier.gui",
    )
