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
# - Optional ML/web/scene/observability stacks are deliberately excluded. Their
#   availability in a developer venv must not change the release artifact.
# - If your platform fails, fall back to: pipx install 'yt-uniquifier[gui]'

import sys
from importlib.metadata import version
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

APP_VERSION = version("yt-uniquifier")

# Bundle YAML profiles + QA HTML template.
datas = []
datas += copy_metadata("yt-uniquifier")
datas += collect_data_files("yt_uniquifier", subdir="profiles", include_py_files=False)
datas += collect_data_files(
    "yt_uniquifier", subdir="core/qa/templates", include_py_files=False,
)

# Transforms self-register on import via core/transforms/__init__.py. The
# init imports every submodule by name so PyInstaller should follow them,
# but be explicit so an empty registry — which would crash any encode
# with "unknown transform" — can never happen.
hiddenimports = []
hiddenimports += collect_submodules("PyQt6")
hiddenimports += collect_submodules("yt_uniquifier.core.transforms")
hiddenimports += collect_submodules("yt_uniquifier.gui")

# Note: PyInstaller >= 6 removed the `block_cipher` / `cipher=` parameters.
# Pass nothing rather than `None` so the spec is forward-compatible.
a = Analysis(
    ["../src/yt_uniquifier/gui/app_pyqt.py"],
    pathex=["../src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        "tests",
        "torch",
        "torchvision",
        "scipy",
        "cv2",
        "scenedetect",
        "fastapi",
        "uvicorn",
        "opentelemetry",
        "mkdocs",
        "pytest",
    ],
)
pyz = PYZ(a.pure, a.zipped_data)

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
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
        },
    )
