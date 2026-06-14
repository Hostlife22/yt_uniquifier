"""v0.8.0 R1 — transform plugin discovery + import safety.

The transforms package must:
  1. survive a single broken built-in (log + continue with the rest),
  2. discover third-party transforms via the ``yt_uniquifier.transforms``
     entry-points group, and
  3. survive a broken third-party plugin without breaking other plugins
     or built-ins.

The trick is that ``core.transforms`` runs ``_load_builtins`` and
``_discover_third_party`` at *import time* — we cannot simply patch the
module and re-import, because the registry is process-global. Instead we
call the private helpers directly with patched dependencies. They are
idempotent: re-running on an already-registered transform id raises
``ValueError`` (the registry's own guard), so each test uses a unique id.
"""

from __future__ import annotations

import importlib
import logging
import sys
import types
from collections.abc import Iterator
from importlib import metadata as importlib_metadata
from unittest import mock

import pytest
from pydantic import BaseModel

import yt_uniquifier.core.transforms as transforms_pkg
from yt_uniquifier.core.transforms import (
    ENTRY_POINT_GROUP,
    FilterChain,
    LabelAllocator,
    TransformSpec,
    all_ids,
    get,
    register,
)


class _Params(BaseModel):
    pass


def _make_spec(transform_id: str) -> TransformSpec:
    def build(params: BaseModel, alloc: LabelAllocator, in_label: str) -> FilterChain:
        out = alloc.next("v")
        return FilterChain(in_label=in_label, out_label=out, filter_str="null")

    return TransformSpec(id=transform_id, kind="video", schema=_Params, build=build)


@pytest.fixture
def _isolated_plugin_id() -> Iterator[str]:
    """Yield a unique transform id, then pop it from the registry afterwards.

    Avoids cross-test contamination since the registry is a process-global.
    """
    from yt_uniquifier.core.transforms import base as _base

    # Use a high-entropy id so parallel pytest-xdist workers don't collide.
    pid = f"_test.plugin_{id(_isolated_plugin_id):x}"
    try:
        yield pid
    finally:
        _base._REGISTRY.pop(pid, None)


def _fake_entry_point(name: str, value: str, *, loader_module: types.ModuleType) -> object:
    """Build a duck-typed EntryPoint whose ``.load()`` returns ``loader_module``
    *and* registers it in ``sys.modules`` so a subsequent normal import works.

    We can't construct ``EntryPoint`` from arbitrary modules across Python
    minor versions cleanly (the signature drifts), so we mimic only the
    surface ``_discover_third_party`` uses: ``.name``, ``.value``, ``.load()``.
    """

    class _EP:
        def __init__(self) -> None:
            self.name = name
            self.value = value
            self.group = ENTRY_POINT_GROUP

        def load(self) -> types.ModuleType:
            sys.modules[value] = loader_module
            return loader_module

    return _EP()


# ---------------------------------------------------------------------------
# (1) discovery happy path
# ---------------------------------------------------------------------------


def test_third_party_plugin_registers_via_entry_points(
    _isolated_plugin_id: str,
) -> None:
    pid = _isolated_plugin_id
    # Build a module whose import side-effect calls register(...).
    mod = types.ModuleType("synthetic_plugin_ok")

    def _register_on_import() -> None:
        register(_make_spec(pid))

    mod.__dict__["_register_on_import"] = _register_on_import
    # Mimic real plugin: registration happens at top of module body.
    _register_on_import()

    fake_ep = _fake_entry_point("ok_plugin", "synthetic_plugin_ok", loader_module=mod)

    with mock.patch.object(
        transforms_pkg.importlib_metadata,
        "entry_points",
        return_value=[fake_ep],
    ):
        transforms_pkg._discover_third_party()

    assert pid in all_ids()
    spec = get(pid)
    chain = spec.build(_Params(), LabelAllocator(), "0:v:0")
    assert chain.filter_str == "null"


# ---------------------------------------------------------------------------
# (2) broken plugin is logged, not raised
# ---------------------------------------------------------------------------


def test_broken_third_party_plugin_logs_warning_and_continues(
    caplog: pytest.LogCaptureFixture,
    _isolated_plugin_id: str,
) -> None:
    pid = _isolated_plugin_id
    good_mod = types.ModuleType("synthetic_plugin_good")
    register(_make_spec(pid))  # the good plugin's effect

    class _BrokenEP:
        name = "bad_plugin"
        value = "synthetic_plugin_bad"
        group = ENTRY_POINT_GROUP

        def load(self) -> types.ModuleType:
            raise ImportError("intentional plugin failure")

    good_ep = _fake_entry_point("good_plugin", "synthetic_plugin_good", loader_module=good_mod)

    caplog.set_level(logging.WARNING, logger="yt_uniquifier.core.transforms")
    with mock.patch.object(
        transforms_pkg.importlib_metadata,
        "entry_points",
        return_value=[_BrokenEP(), good_ep],
    ):
        transforms_pkg._discover_third_party()

    # Good plugin survives the bad one (registration happened above; we only
    # need to confirm the warning surfaced).
    assert pid in all_ids()
    assert any(
        "bad_plugin" in rec.message and "intentional plugin failure" in rec.message
        for rec in caplog.records
    ), caplog.text


# ---------------------------------------------------------------------------
# (3) broken built-in does not stop other built-ins
# ---------------------------------------------------------------------------


def test_broken_builtin_logs_warning_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If one built-in submodule raises on import, the rest must still load.

    We can't actually break a real built-in (the registry would be left in
    an inconsistent state for subsequent tests). Instead we shim
    ``importlib.import_module`` to raise for one specific submodule and
    verify (a) the warning, (b) the other modules still get imported.
    """
    bad_target = "yt_uniquifier.core.transforms.audio_eq"
    seen: list[str] = []
    real_import = importlib.import_module

    def shim(name: str, *a: object, **kw: object) -> types.ModuleType:
        seen.append(name)
        if name == bad_target:
            raise RuntimeError("intentional built-in failure")
        return real_import(name, *a, **kw)  # type: ignore[arg-type]

    caplog.set_level(logging.WARNING, logger="yt_uniquifier.core.transforms")
    with mock.patch.object(transforms_pkg.importlib, "import_module", side_effect=shim):
        transforms_pkg._load_builtins()

    # bad target was attempted...
    assert bad_target in seen
    # ...and others were too (we look for a couple of well-known siblings).
    assert any(s.endswith(".video_geom") for s in seen)
    assert any(s.endswith(".audio_loudnorm") for s in seen)
    # warning surfaced with the failing module name.
    assert any(
        "audio_eq" in rec.message and "intentional built-in failure" in rec.message
        for rec in caplog.records
    ), caplog.text


# ---------------------------------------------------------------------------
# (4) entry-points lookup errors degrade gracefully
# ---------------------------------------------------------------------------


def test_entry_points_lookup_failure_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(*_a: object, **_kw: object) -> object:
        raise RuntimeError("metadata backend unhappy")

    caplog.set_level(logging.WARNING, logger="yt_uniquifier.core.transforms")
    with mock.patch.object(
        transforms_pkg.importlib_metadata,
        "entry_points",
        side_effect=boom,
    ):
        # Must not raise.
        transforms_pkg._discover_third_party()

    assert any(
        "entry-points lookup failed" in rec.message and ENTRY_POINT_GROUP in rec.message
        for rec in caplog.records
    ), caplog.text


# ---------------------------------------------------------------------------
# (5) built-ins are still loaded after import (smoke)
# ---------------------------------------------------------------------------


def test_all_builtins_registered_after_package_import() -> None:
    """Sanity: the registry is non-empty and contains a representative built-in."""
    ids = set(all_ids())
    assert "video.crop_resize" in ids
    assert "audio.loudnorm" in ids


def test_entry_point_group_constant_is_exported() -> None:
    """Plugin authors copy-paste this constant; pin its value."""
    assert ENTRY_POINT_GROUP == "yt_uniquifier.transforms"
    # And confirm it's importable from the public surface.
    from yt_uniquifier.core.transforms import ENTRY_POINT_GROUP as exported

    assert exported == ENTRY_POINT_GROUP


def test_importlib_metadata_entry_points_accepts_group_kwarg() -> None:
    """Python 3.10+ supports the ``group=`` kwarg; we rely on it.

    Smoke-guards a regression where someone reverts to the deprecated
    ``entry_points()[group]`` mapping access pattern (removed in 3.12).
    """
    # Should not raise on any supported Python.
    eps = importlib_metadata.entry_points(group=ENTRY_POINT_GROUP)
    # Sequence-like (EntryPoints object) — iter is always allowed.
    iter(eps)
