"""v0.6.0 performance regression tests (B1, B2, B5, B6, B7)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ============================================================== B1


def test_keyframe_cache_key_uses_stat_not_md5(tmp_path: Path) -> None:
    """B1: _keyframe_cache_path key must be (size, mtime_ns), not a
    full-file hash.

    Pre-fix the cache key was a content hash (md5), which made
    cold-start probe O(file_size) — 60-360 s on a 180 GB 4K HDR
    master. Post-fix the key is (size, mtime_ns) so it's O(1)
    regardless of file size. As a side effect, ``md5_file`` is no
    longer imported by segmenter.py.
    """
    from yt_uniquifier.core import segmenter as seg_mod

    src = tmp_path / "huge.mp4"
    src.write_bytes(b"x" * 1024)

    cache_path = seg_mod._keyframe_cache_path(src)

    # Cache filename starts with size + mtime_ns. R9 (v0.7) appended a
    # 12-char head+tail MD5 fingerprint to disambiguate identical
    # (size, mtime_ns) tuples that NTFS coarse-grained mtime allows on
    # Windows — still O(1) cost (≤ 8 KB read), still no full-file MD5.
    st = src.stat()
    assert cache_path.name.startswith(f"{st.st_size}_{st.st_mtime_ns}_")
    assert cache_path.name.endswith(".json")

    # md5_file is no longer imported by segmenter (was the heavy
    # cold-start cost). If a future regression re-imports it, this
    # assertion is the canary.
    assert not hasattr(seg_mod, "md5_file"), (
        "segmenter.py must not import md5_file after B1 — "
        "(size, mtime_ns) replaces the content hash"
    )


def test_keyframe_cache_key_is_path_independent(tmp_path: Path) -> None:
    """B1 side benefit: moving the file across mounts (or renaming it
    without touching mtime) hits the same cache entry."""
    from yt_uniquifier.core import segmenter as seg_mod

    a = tmp_path / "a.mp4"
    a.write_bytes(b"identical_content")
    st_a = a.stat()
    # Manually fabricate the "moved" file by stamping the same mtime.
    moved = tmp_path / "subdir" / "renamed.mp4"
    moved.parent.mkdir()
    moved.write_bytes(b"identical_content")
    os.utime(moved, ns=(st_a.st_mtime_ns, st_a.st_mtime_ns))

    assert (
        seg_mod._keyframe_cache_path(a).name
        == seg_mod._keyframe_cache_path(moved).name
    )


# ============================================================== B2


def test_loudnorm_measure_command_preserves_native_audio_for_accuracy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass 1 must not alter channels/rate before EBU R128 measurement."""
    import yt_uniquifier.core.transforms.audio_loudnorm as ln_mod
    from yt_uniquifier.core.transforms.audio_loudnorm import (
        LoudnormParams,
        measure,
    )
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **_kw: Any) -> Any:
        captured["cmd"] = cmd
        # Emit a minimal valid loudnorm JSON tail in stderr.
        return type("P", (), {
            "returncode": 0,
            "stderr": (
                '{"input_i":"-23.0","input_tp":"-1.5",'
                '"input_lra":"7.0","input_thresh":"-33.0",'
                '"target_offset":"0.5"}\n'
            ),
            "stdout": "",
        })

    # Use monkeypatch (auto-reverted after this test) instead of direct
    # module mutation — `subprocess` is a singleton, so reassigning
    # `ln_mod.subprocess.run` leaks across tests if the cleanup writes
    # back the already-patched reference.
    monkeypatch.setattr(ln_mod.subprocess, "run", fake_run)
    measure(Path("dummy.wav"), LoudnormParams())

    cmd = captured["cmd"]
    af_idx = cmd.index("-af")
    af = cmd[af_idx + 1]
    assert "aresample=" not in af
    assert "aformat=" not in af
    assert "loudnorm=" in af


# ============================================================== B5


@pytest.mark.parametrize("duration,fps,expected", [
    (60.0, 24.0, 1),       # 1 minute — too short to subsample
    (1500.0, 24.0, 1),     # 25 min — still under threshold
    (1800.0, 24.0, 6),     # 30 min — threshold reached, ~0.25s @ 24fps = 6
    (3600.0, 24.0, 6),     # 1 hour
    (14400.0, 24.0, 6),    # 4 hours
    (3600.0, 30.0, 8),     # 30 fps source: 0.25 × 30 = 7.5 → 8
    (3600.0, 60.0, 15),    # 60 fps source
    (3600.0, 1.0, 1),      # pathologically low fps clamped to >=1
])
def test_auto_vmaf_subsample(duration: float, fps: float, expected: int) -> None:
    from yt_uniquifier.core.qa.vmaf import auto_subsample_for_duration
    assert auto_subsample_for_duration(duration, fps=fps) == expected


# ============================================================== B6


def test_detect_encoders_runs_probes_in_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B6: ThreadPoolExecutor parallelises the 10 candidate probes.

    Each fake _probe_one sleeps 100 ms. Serial would take ~1 s; parallel
    should finish well under 300 ms.
    """
    from yt_uniquifier.core import encoder as enc_mod

    monkeypatch.setattr(enc_mod, "_load_cache", lambda _k: None)
    monkeypatch.setattr(enc_mod, "_save_cache", lambda _k, _r: None)
    monkeypatch.setattr(enc_mod, "_ffmpeg_version_hash", lambda: "dead")

    def slow_probe(name: str, vendor: str, codec: str) -> enc_mod.EncoderCandidate:
        time.sleep(0.1)
        return enc_mod.EncoderCandidate(
            name=name, vendor=vendor, codec=codec, works=True,
        )

    monkeypatch.setattr(enc_mod, "_probe_one", slow_probe)

    start = time.monotonic()
    results = enc_mod.detect_encoders(force=True)
    elapsed = time.monotonic() - start

    assert len(results) == len(enc_mod._CANDIDATES)
    # Canonical order preserved.
    assert [c.name for c in results] == [
        name for name, _, _ in enc_mod._CANDIDATES
    ]
    assert elapsed < 0.3, (
        f"parallel probe should finish in ~100 ms; took {elapsed:.2f}s — "
        "is the ThreadPoolExecutor not actually parallel?"
    )


# ============================================================== B7


def test_nvenc_max_parallel_sums_across_gpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B7: when nvidia-smi reports multiple GPUs, sum their capacities.

    Pre-fix only the first line was parsed. Two consumer cards with
    8 GB free each would report cap=3 (one card), not cap=6 (both).
    """
    from yt_uniquifier.core import encoder as enc_mod

    fake_proc = MagicMock(
        returncode=0,
        stdout=(
            "8000, NVIDIA GeForce RTX 3080\n"     # consumer → cap 3
            "8000, NVIDIA GeForce RTX 3090\n"     # consumer → cap 3
        ),
    )
    monkeypatch.setattr(
        enc_mod.subprocess, "run", lambda *_a, **_kw: fake_proc,
    )

    assert enc_mod._nvenc_max_parallel() == 6


def test_nvenc_max_parallel_pro_plus_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed-card host: one A6000 (pro, cap 8) + one 3090 (consumer, cap 3) = 11."""
    from yt_uniquifier.core import encoder as enc_mod

    fake_proc = MagicMock(
        returncode=0,
        stdout=(
            "16000, NVIDIA RTX A6000\n"           # pro → cap 8
            "8000, NVIDIA GeForce RTX 3090\n"     # consumer → cap 3
        ),
    )
    monkeypatch.setattr(
        enc_mod.subprocess, "run", lambda *_a, **_kw: fake_proc,
    )

    assert enc_mod._nvenc_max_parallel() == 11


def test_nvenc_max_parallel_low_vram_clamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A card with <500 MB free still yields 1 session (not 0)."""
    from yt_uniquifier.core import encoder as enc_mod

    fake_proc = MagicMock(returncode=0, stdout="200, NVIDIA GeForce GTX 1050\n")
    monkeypatch.setattr(
        enc_mod.subprocess, "run", lambda *_a, **_kw: fake_proc,
    )

    assert enc_mod._nvenc_max_parallel() == 1


def test_nvenc_max_parallel_falls_back_when_smi_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No nvidia-smi → vendor-default (3)."""
    from yt_uniquifier.core import encoder as enc_mod

    def raise_(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(enc_mod.subprocess, "run", raise_)
    assert (
        enc_mod._nvenc_max_parallel()
        == enc_mod._VENDOR_DEFAULT_PARALLEL["nvenc"]
    )
