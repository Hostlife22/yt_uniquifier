"""v1.2.0 Task 23 — plugin manifest, capability gating, and audit-hook
sandbox tests.

Strategy: we don't ship a real PyPI plugin in the test tree.  Instead
we construct synthetic ``importlib.metadata`` Distribution + EntryPoint
objects (the same shape ``read_manifest`` consumes in production) and
drive the manifest gate / sandbox through them.

Coverage:

* manifest read happy path
* manifest missing → PluginViolation at discovery
* manifest unparseable / schema mismatch → PluginViolation
* capability mismatch → register() raises PluginViolation
* YT_UNIQ_NO_PLUGINS env var skips discovery
* YT_UNIQ_PLUGINS_ALLOWLIST filters discovery
* audit-hook sandbox catches os.unlink inside in_plugin_code()
* audit hook is a no-op outside the contextvar window
* drop_disabled_plugins removes registered plugin specs
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from yt_uniquifier.core import plugin_sandbox, plugins
from yt_uniquifier.core.plugins import (
    MANIFEST_FILENAME,
    PluginManifest,
    PluginViolation,
    drop_disabled_plugins,
    list_entry_points,
    read_manifest,
)
from yt_uniquifier.core.transforms import base as transform_base
from yt_uniquifier.core.transforms.base import (
    FilterChain,
    LabelAllocator,
    TransformSpec,
    register,
)

# ---------------------------------------------------------------------------
# Synthetic distribution / entry point helpers
# ---------------------------------------------------------------------------


class _FakeFile:
    """Stand-in for ``importlib.metadata.PackagePath`` — has ``.name`` and
    ``.read_text()``, which are the only attributes ``read_manifest``
    consumes."""

    def __init__(self, name: str, content: str | None) -> None:
        self.name = name
        self._content = content

    def read_text(
        self, encoding: str | None = None, errors: str | None = None,
    ) -> str:
        if self._content is None:
            raise OSError("simulated unreadable file")
        return self._content


def _make_entry_point(
    *, name: str, value: str, manifest_content: str | None,
    distribution_name: str = "fake-pkg",
    has_manifest_file: bool = True,
) -> Any:
    """Return a stand-in EntryPoint object usable by read_manifest()."""
    files: list[Any] = []
    if has_manifest_file:
        files.append(_FakeFile(MANIFEST_FILENAME, manifest_content))
    metadata = {"Name": distribution_name}
    dist = SimpleNamespace(
        files=files,
        metadata=metadata,
    )
    ep = SimpleNamespace(name=name, value=value, dist=dist)
    return ep


# ---------------------------------------------------------------------------
# Manifest read happy-path / failure modes
# ---------------------------------------------------------------------------


def test_manifest_read_happy_path() -> None:
    ep = _make_entry_point(
        name="pingpong",
        value="my_pingpong",
        manifest_content=(
            '[plugin]\n'
            'name = "pingpong"\n'
            'version = "0.1.0"\n'
            'capabilities = ["video_transform"]\n'
        ),
    )
    manifest = read_manifest(ep)
    assert manifest.name == "pingpong"
    assert manifest.version == "0.1.0"
    assert manifest.capabilities == ("video_transform",)


def test_manifest_missing_file_rejected() -> None:
    ep = _make_entry_point(
        name="pingpong", value="my_pingpong",
        manifest_content=None, has_manifest_file=False,
    )
    with pytest.raises(PluginViolation, match=MANIFEST_FILENAME):
        read_manifest(ep)


def test_manifest_invalid_toml_rejected() -> None:
    ep = _make_entry_point(
        name="pp", value="m",
        manifest_content="[plugin\nname = oops",  # malformed
    )
    with pytest.raises(PluginViolation, match="not valid TOML"):
        read_manifest(ep)


def test_manifest_schema_violation_rejected() -> None:
    # `capabilities` must be a list of the known Literal values.
    ep = _make_entry_point(
        name="pp", value="m",
        manifest_content=(
            '[plugin]\nname="pp"\nversion="1"\n'
            'capabilities = ["video_transform", "arbitrary_capability_bad"]\n'
        ),
    )
    with pytest.raises(PluginViolation, match="failed validation"):
        read_manifest(ep)


def test_manifest_extra_field_rejected() -> None:
    ep = _make_entry_point(
        name="pp", value="m",
        manifest_content=(
            '[plugin]\nname="pp"\nversion="1"\nrogue_field = true\n'
        ),
    )
    with pytest.raises(PluginViolation, match="failed validation"):
        read_manifest(ep)


# ---------------------------------------------------------------------------
# Capability gating in register()
# ---------------------------------------------------------------------------


class _NoOpParams(BaseModel):
    pass


def _noop_video_builder(
    _params: _NoOpParams,
    alloc: LabelAllocator,
    in_label: str,
    *,
    rng: object | None = None,
) -> FilterChain:
    return FilterChain(in_label=in_label, out_label=alloc.next("v"), filter_str="null")


def _noop_audio_builder(
    _params: _NoOpParams,
    alloc: LabelAllocator,
    in_label: str,
    *,
    rng: object | None = None,
) -> FilterChain:
    return FilterChain(in_label=in_label, out_label=alloc.next("a"), filter_str="anull")


@pytest.fixture
def _clean_registry() -> None:
    # Snapshot the registry so a misbehaving test doesn't leak ids.
    snapshot = dict(transform_base._REGISTRY)
    plugin_snapshot = set(transform_base._PLUGIN_SPEC_IDS)
    name_map_snapshot = dict(plugins._PLUGIN_NAME_TO_SPEC_IDS)
    yield None
    transform_base._REGISTRY.clear()
    transform_base._REGISTRY.update(snapshot)
    transform_base._PLUGIN_SPEC_IDS.clear()
    transform_base._PLUGIN_SPEC_IDS.update(plugin_snapshot)
    plugins._PLUGIN_NAME_TO_SPEC_IDS.clear()
    plugins._PLUGIN_NAME_TO_SPEC_IDS.update(name_map_snapshot)


def test_register_passes_for_builtin_without_manifest(_clean_registry: None) -> None:
    """No active manifest = built-in or test registration; capability
    gate is bypassed entirely."""
    spec = TransformSpec(
        id="video.test_builtin_pass_v23",
        kind="video", schema=_NoOpParams, build=_noop_video_builder,
    )
    register(spec)
    assert "video.test_builtin_pass_v23" in transform_base._REGISTRY
    assert "video.test_builtin_pass_v23" not in transform_base._PLUGIN_SPEC_IDS


def test_register_rejects_capability_mismatch(_clean_registry: None) -> None:
    """A plugin declaring only video_transform must not be able to
    register an audio transform."""
    manifest = PluginManifest(
        name="video-only-plugin", version="1.0",
        capabilities=("video_transform",),
    )
    token = plugins.set_active_manifest(manifest)
    try:
        bad_spec = TransformSpec(
            id="audio.test_cap_mismatch_v23",
            kind="audio", schema=_NoOpParams, build=_noop_audio_builder,
        )
        with pytest.raises(PluginViolation, match="cannot register a 'audio'"):
            register(bad_spec)
    finally:
        plugins.reset_active_manifest(token)


def test_register_passes_for_matching_capability(_clean_registry: None) -> None:
    manifest = PluginManifest(
        name="audio-plugin", version="1.0",
        capabilities=("audio_transform",),
    )
    token = plugins.set_active_manifest(manifest)
    try:
        spec = TransformSpec(
            id="audio.test_cap_ok_v23",
            kind="audio", schema=_NoOpParams, build=_noop_audio_builder,
        )
        register(spec)
    finally:
        plugins.reset_active_manifest(token)
    # The spec must be tracked as a plugin spec so call_build wraps it.
    assert "audio.test_cap_ok_v23" in transform_base._PLUGIN_SPEC_IDS
    assert "audio.test_cap_ok_v23" in plugins.get_plugin_spec_ids("audio-plugin")


# ---------------------------------------------------------------------------
# Environment-variable gating
# ---------------------------------------------------------------------------


def test_no_plugins_env_var_skips_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YT_UNIQ_NO_PLUGINS", "1")
    # Even if there were real plugins installed, list_entry_points must
    # return an empty tuple under the env var.
    assert list_entry_points("yt_uniquifier.transforms") == ()


def test_allowlist_env_var_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """YT_UNIQ_PLUGINS_ALLOWLIST keeps only the listed entry-point
    names.  We stub importlib_metadata.entry_points to return three
    synthetic EPs and assert the filter."""
    monkeypatch.delenv("YT_UNIQ_NO_PLUGINS", raising=False)
    monkeypatch.setenv("YT_UNIQ_PLUGINS_ALLOWLIST", "alpha, gamma")
    fakes = [
        SimpleNamespace(name="alpha", value="m1"),
        SimpleNamespace(name="beta", value="m2"),
        SimpleNamespace(name="gamma", value="m3"),
    ]
    monkeypatch.setattr(
        plugins.importlib_metadata, "entry_points",
        lambda group: fakes if group == "yt_uniquifier.transforms" else [],
    )
    result = list_entry_points("yt_uniquifier.transforms")
    names = {ep.name for ep in result}
    assert names == {"alpha", "gamma"}


# ---------------------------------------------------------------------------
# Sandbox: audit hook catches denylisted syscalls inside plugin code
# ---------------------------------------------------------------------------


def test_sandbox_blocks_os_unlink_in_plugin_code(tmp_path: Path) -> None:
    """A plugin builder attempting os.unlink while wrapped in
    in_plugin_code() must raise PluginViolation via the audit hook."""
    plugin_sandbox.install_sandbox()
    # ensure not globally disabled by another test
    assert not plugin_sandbox.is_sandbox_disabled()
    victim = tmp_path / "victim.txt"
    victim.write_text("alive")
    with pytest.raises(PluginViolation, match="denylisted"), plugin_sandbox.in_plugin_code():
        os.unlink(victim)
    # The file MUST still exist — the audit hook fires BEFORE the
    # syscall lands.
    assert victim.exists()


def test_sandbox_inactive_outside_in_plugin_code(tmp_path: Path) -> None:
    """Same op outside in_plugin_code() must NOT raise — built-in code
    paths legitimately delete tmp files all the time."""
    plugin_sandbox.install_sandbox()
    assert not plugin_sandbox.is_sandbox_disabled()
    victim = tmp_path / "v.txt"
    victim.write_text("x")
    os.unlink(victim)
    assert not victim.exists()


# ---------------------------------------------------------------------------
# drop_disabled_plugins post-load filter
# ---------------------------------------------------------------------------


def test_drop_disabled_plugins_removes_specs(_clean_registry: None) -> None:
    manifest = PluginManifest(
        name="rogue-plugin", version="1.0",
        capabilities=("video_transform",),
    )
    token = plugins.set_active_manifest(manifest)
    try:
        spec = TransformSpec(
            id="video.test_drop_v23",
            kind="video", schema=_NoOpParams, build=_noop_video_builder,
        )
        register(spec)
    finally:
        plugins.reset_active_manifest(token)
    assert "video.test_drop_v23" in transform_base._REGISTRY
    dropped = drop_disabled_plugins(no_plugins=True)
    assert "rogue-plugin" in dropped
    assert "video.test_drop_v23" not in transform_base._REGISTRY
    assert "video.test_drop_v23" not in transform_base._PLUGIN_SPEC_IDS


def test_drop_disabled_plugins_honours_allowlist(_clean_registry: None) -> None:
    for plugin_name, spec_id in [
        ("good-plugin", "video.test_drop_good_v23"),
        ("bad-plugin", "video.test_drop_bad_v23"),
    ]:
        manifest = PluginManifest(
            name=plugin_name, version="1.0", capabilities=("video_transform",),
        )
        token = plugins.set_active_manifest(manifest)
        try:
            register(TransformSpec(
                id=spec_id, kind="video", schema=_NoOpParams,
                build=_noop_video_builder,
            ))
        finally:
            plugins.reset_active_manifest(token)
    dropped = drop_disabled_plugins(allowlist=frozenset({"good-plugin"}))
    assert dropped == ("bad-plugin",)
    assert "video.test_drop_good_v23" in transform_base._REGISTRY
    assert "video.test_drop_bad_v23" not in transform_base._REGISTRY
