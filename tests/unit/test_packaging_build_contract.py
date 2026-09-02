"""Static contracts for reproducible desktop packaging."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_desktop_bundle_excludes_opt_in_runtime_stacks() -> None:
    """Installed developer extras must not silently inflate release bundles."""
    spec = (ROOT / "pyinstaller" / "yt-uniq-gui.spec").read_text(encoding="utf-8")
    for package in ("torch", "torchvision", "cv2", "scenedetect", "fastapi"):
        assert f'"{package}"' in spec


def test_make_build_is_noninteractive_and_repeatable() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PyInstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm" in makefile
