"""Resource path resolution for the GUI layer.

`importlib.resources` is the only path-resolution mechanism that
survives PyInstaller and zipapp packaging. `Path(__file__).parents[N]`
breaks under PyInstaller (`__file__` resolves into the unpacked
`_MEIPASS` tree; `parents[N]` then climbs out of the bundle) and
breaks under zipapp (no real `__file__` at all).

Use `profiles_dir()` instead of recomputing the path in each screen.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def profiles_dir() -> Path:
    """Absolute path of the bundled `yt_uniquifier/profiles/` directory.

    Returns a real `Path` (not a `Traversable`) so callers can pass it
    to `glob()`, `iterdir()`, and `open()` without ceremony. Works
    under editable installs, wheels, and PyInstaller-frozen bundles.
    """
    return Path(str(files("yt_uniquifier").joinpath("profiles")))
