"""v0.9.0 cross-cutting smoke — one sanity check per shipped feature.

Each round of v0.9.0 has its own deep test module; this file
verifies the *integration surface* — that the features cohabit,
that defaults remain backward-compatible, and that opt-in
features stay opt-in.

Scope:
  * R1 marketplace — bootstrap catalog is valid, lookup +
    schema-validate path doesn't trip on the in-tree YAMLs.
  * R2 Whisper subtitles — registry has video.subtitles, builder
    snapshot is stable, preflight blocks on a missing SRT and
    accepts a present one.
  * R3 telemetry — default config is silent, opt-in roundtrips
    one event through iter_events.
  * R4 web — build_app(...) succeeds with default config and
    /healthz returns 200. Skipped when [web] extra absent.
  * R5 i18n — install ru_RU on a fresh QApplication and a
    known key flips to Russian. Skipped without [gui] extra.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# R1 — marketplace bootstrap
# ---------------------------------------------------------------------------


def test_r1_bootstrap_catalog_is_valid_and_https_only() -> None:
    from yt_uniquifier.core import profile_marketplace as pm

    raw = pm.BOOTSTRAP_CATALOG_PATH.read_bytes()
    catalog = pm._parse_catalog(raw, source=str(pm.BOOTSTRAP_CATALOG_PATH))
    assert catalog.entries, "bootstrap catalog ships with at least one entry"
    for entry in catalog.entries:
        assert entry.url.startswith("https://"), (
            f"bootstrap entry {entry.id} must use HTTPS; got {entry.url!r}"
        )
        assert len(entry.sha256) == 64


def test_r1_find_entry_and_list_entries_round_trip() -> None:
    from yt_uniquifier.core.profile_marketplace import (
        fetch_catalog,
        find_entry,
        list_entries,
    )

    # Use the on-disk cache or bootstrap — no network in tests.
    catalog = fetch_catalog()
    ids = [e.id for e in list_entries(catalog)]
    assert "cid_aware" in ids
    entry = find_entry(catalog, "cid_aware")
    assert entry.id == "cid_aware"


# ---------------------------------------------------------------------------
# R2 — Whisper subtitle transform
# ---------------------------------------------------------------------------


def test_r2_subtitles_transform_registered() -> None:
    from yt_uniquifier.core.transforms import get
    spec = get("video.subtitles")
    assert spec.kind == "video"


def test_r2_preflight_blocks_missing_srt_and_accepts_present(
    tmp_path: Path,
) -> None:
    from yt_uniquifier.core.models import (
        AudioStream,
        EncoderCandidate,
        HDRInfo,
        Plan,
        Profile,
        SourceMeta,
        TransformConfig,
        VideoStream,
    )
    from yt_uniquifier.core.pipeline import compute_plan_hash
    from yt_uniquifier.core.preflight import has_fail, preflight

    src_file = tmp_path / "in.mp4"
    src_file.touch()
    source = SourceMeta(
        path=src_file, container="mp4", duration_sec=10, size_bytes=10,
        video=[VideoStream(
            index=0, codec="h264", width=64, height=64, fps=24.0,
            duration_sec=10, pix_fmt="yuv420p", bit_rate=100_000,
            color=HDRInfo(is_hdr=False, transfer="bt709",
                          primaries="bt709", space="bt709"),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
        subtitle=[],
    )
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264",
                           works=True)

    def _plan_with(subpath: str) -> Plan:
        profile = Profile(
            name="t",
            transforms=[TransformConfig(
                id="video.subtitles",
                enabled=True,
                params={"subtitle_path": subpath},
            )],
        )
        return Plan(source=source, profile=profile, encoder=enc,
                    plan_hash=compute_plan_hash(source, profile, enc))

    bad = _plan_with(str(tmp_path / "absent.srt"))
    assert has_fail(preflight(source, bad, enc))

    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    good = _plan_with(str(srt))
    codes = {f.code for f in preflight(source, good, enc)}
    assert "subtitles.path.not_found" not in codes


# ---------------------------------------------------------------------------
# R3 — telemetry
# ---------------------------------------------------------------------------


def test_r3_disabled_telemetry_is_silent(tmp_path: Path) -> None:
    from yt_uniquifier.core import telemetry

    cfg = telemetry.TelemetryConfig(enabled=False, events_dir=tmp_path)
    telemetry.record({"kind": "ignored"}, cfg)
    assert telemetry.event_count(tmp_path) == 0


def test_r3_enabled_telemetry_round_trips_event(tmp_path: Path) -> None:
    from yt_uniquifier.core import telemetry

    cfg = telemetry.TelemetryConfig(enabled=True, events_dir=tmp_path)
    telemetry.record({"kind": "smoke", "status": "completed"}, cfg)
    events = list(telemetry.iter_events(tmp_path))
    assert len(events) == 1
    assert events[0]["kind"] == "smoke"
    assert events[0]["status"] == "completed"
    assert events[0]["schema_version"] == telemetry.SCHEMA_VERSION


# ---------------------------------------------------------------------------
# R4 — web (skipped without [web] extra)
# ---------------------------------------------------------------------------


def test_r4_web_app_builds_and_healthz_responds(tmp_path: Path) -> None:
    if importlib.util.find_spec("fastapi") is None:
        pytest.skip("fastapi not installed; [web] extra absent")
    from fastapi.testclient import TestClient

    from yt_uniquifier.web.app import WebConfig, build_app

    work = tmp_path / "w"
    out = tmp_path / "o"
    for d in (work, out):
        d.mkdir()
    client = TestClient(build_app(WebConfig(work_dir=work, output_dir=out)))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


# ---------------------------------------------------------------------------
# R5 — i18n
# ---------------------------------------------------------------------------


def test_r5_install_translator_translates_known_key() -> None:
    if importlib.util.find_spec("PyQt6") is None:
        pytest.skip("PyQt6 not installed; [gui] extra absent")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtWidgets import QApplication

    from yt_uniquifier.gui.i18n import (
        SOURCE_LOCALE,
        install_translator,
    )

    app = QApplication.instance() or QApplication([])
    install_translator(app, "ru_RU")
    try:
        translated = QCoreApplication.translate("smoke", "&Run")
        assert translated == "&Запустить"
    finally:
        install_translator(app, SOURCE_LOCALE)


# ---------------------------------------------------------------------------
# Marketplace catalog SHAs match the in-tree shipped profiles
# ---------------------------------------------------------------------------


def test_bootstrap_catalog_shas_match_in_tree_profiles() -> None:
    """Each bootstrap entry points at a real in-tree YAML; the
    declared SHA must equal the on-disk hash. Catches the case
    where someone edits a profile but forgets to update the
    catalog hash — install would then 502 with a SHA mismatch
    for every fresh user.
    """
    from yt_uniquifier.core.profile_marketplace import (
        fetch_catalog,
        list_entries,
    )
    from yt_uniquifier.gui.paths import profiles_dir

    catalog = fetch_catalog()
    bundled = profiles_dir()
    mismatches: list[str] = []
    for entry in list_entries(catalog):
        # The bootstrap URL has the form
        # https://raw.githubusercontent.com/.../profiles/<id>.yaml
        # Map id → bundled profile and hash that.
        local = bundled / f"{entry.id}.yaml"
        if not local.exists():
            continue
        actual = hashlib.sha256(local.read_bytes()).hexdigest()
        if actual.lower() != entry.sha256.lower():
            mismatches.append(
                f"{entry.id}: catalog={entry.sha256[:12]}… "
                f"local={actual[:12]}…",
            )
    assert not mismatches, (
        "bootstrap catalog SHAs drifted from in-tree profile bytes:\n  "
        + "\n  ".join(mismatches)
    )
