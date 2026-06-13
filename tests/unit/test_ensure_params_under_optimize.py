"""A2 (v0.5.5) regression: ``ensure_params`` / ``ensure_rng`` raise
``PipelineError`` regardless of ``python -O`` / ``PYTHONOPTIMIZE``.

Pre-fix every transform builder guarded its ``params: BaseModel`` arg
with ``assert isinstance(params, XParams)``. Under ``-O`` / a PyInstaller
release built with ``optimize=2`` the assert becomes a no-op; a wrong
``BaseModel`` subclass would then flow through and the builder would
either emit a wrong filter string or ``AttributeError`` on a subclass-
specific field.

Post-fix every builder calls ``ensure_params(params, XParams)`` (or
``ensure_rng(rng)``) which raises ``PipelineError`` via an ``if``
statement that ``-O`` cannot strip.

This test covers the helper itself; the snapshot tests in
``test_transforms.py`` cover end-to-end behaviour through the real
builders.
"""

from __future__ import annotations

import random
import subprocess
import sys

import pytest

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.transforms.audio_compand import CompandParams
from yt_uniquifier.core.transforms.audio_eq import AudioEqParams
from yt_uniquifier.core.transforms.base import ensure_params, ensure_rng


def test_ensure_params_returns_narrowed_value() -> None:
    p = CompandParams()
    out = ensure_params(p, CompandParams)
    assert out is p  # identity preserved
    assert isinstance(out, CompandParams)


def test_ensure_params_raises_on_wrong_type() -> None:
    wrong = AudioEqParams()  # valid BaseModel, wrong subclass
    with pytest.raises(PipelineError, match="expected CompandParams.*AudioEqParams"):
        ensure_params(wrong, CompandParams)


def test_ensure_rng_returns_narrowed_value() -> None:
    rng = random.Random(42)
    out = ensure_rng(rng)
    assert out is rng


def test_ensure_rng_raises_on_wrong_type() -> None:
    with pytest.raises(PipelineError, match="expected random.Random.*str"):
        ensure_rng("not an rng")  # type: ignore[arg-type]


def test_helpers_survive_python_O_O_O() -> None:
    """End-to-end: re-run the helper checks under ``python -OO`` to
    prove the new behaviour does NOT depend on ``assert``.

    Spawns a fresh interpreter with ``-OO`` so ``__debug__`` is False and
    every ``assert`` statement is dropped by the bytecode compiler. The
    snippet exercises both helpers and asserts (via the subprocess's
    exit code, not via Python ``assert``) that ``PipelineError`` is
    still raised.
    """
    snippet = (
        "import random\n"
        "from yt_uniquifier.core.errors import PipelineError\n"
        "from yt_uniquifier.core.transforms.audio_eq import AudioEqParams\n"
        "from yt_uniquifier.core.transforms.audio_compand import CompandParams\n"
        "from yt_uniquifier.core.transforms.base import ensure_params, ensure_rng\n"
        "\n"
        "try:\n"
        "    ensure_params(AudioEqParams(), CompandParams)\n"
        "except PipelineError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('ensure_params did not raise under -OO')\n"
        "\n"
        "try:\n"
        "    ensure_rng('nope')\n"
        "except PipelineError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('ensure_rng did not raise under -OO')\n"
        "\n"
        "# Sanity: __debug__ must be False here.\n"
        "if __debug__:\n"
        "    raise SystemExit('-OO did not disable __debug__')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-OO", "-c", snippet],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"under -OO: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
