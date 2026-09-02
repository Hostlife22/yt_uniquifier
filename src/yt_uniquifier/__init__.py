"""yt-uniquifier — production-grade re-encoder with controlled micro-transforms.

The public ``__version__`` is sourced from installed package metadata so the
single source of truth stays in ``pyproject.toml``. The fallback string only
fires when the package is imported from a source checkout that was never
installed (e.g. ``python -c 'import yt_uniquifier'`` from the repo root with
no ``pip install -e .``).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("yt-uniquifier")
except PackageNotFoundError:  # source checkout, never installed
    __version__ = "1.3.2+source"

__all__ = ["__version__"]
