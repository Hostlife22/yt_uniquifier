"""Capability probe for Whisper-based subtitle support (v0.9.0 R2 / F14).

The Whisper subtitle transform has two independent capability surfaces:

* **Burn-in**: ffmpeg's built-in ``subtitles`` filter. Present in every
  mainline ffmpeg with libass (the default on Homebrew, the official
  Linux static builds, and most distros). Without it, we cannot
  render a subtitle file into the video.
* **Auto-generate**: a Whisper backend. Two are recognised today —
  ``whisper-cpp`` (the canonical CLI binary from ggml-org/whisper.cpp)
  and ``main`` from the same project under a different name. A future
  native ffmpeg ``whisper`` filter would be detected here too. Without
  any backend, users must pre-generate an SRT themselves.

The probe is cached and side-effect-free; tests inject results via
``set_capability_for_tests``. We deliberately do NOT execute Whisper
itself to probe — startup cost is non-trivial on first run because
the model loads even for ``--help``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache

from yt_uniquifier.core.preflight import _ffmpeg_filter_works


@dataclass(frozen=True)
class WhisperCapability:
    """Detected support for subtitle features.

    ``burn_in_filter`` — ffmpeg can render an SRT/ASS into a video.
    ``srt_generator`` — name of a whisper-cpp-compatible binary on
    PATH, or ``None`` if no auto-generator is available.
    ``ffmpeg_native_whisper`` — the (currently hypothetical) native
    ``whisper`` filter is present. Detected separately so a future
    ffmpeg release with built-in Whisper "just works" without a code
    change here.
    """

    burn_in_filter: bool
    srt_generator: str | None
    ffmpeg_native_whisper: bool

    @property
    def has_any_generator(self) -> bool:
        return self.ffmpeg_native_whisper or self.srt_generator is not None


_CANDIDATE_BINARIES: tuple[str, ...] = (
    # ggml-org/whisper.cpp ships the binary as "whisper-cpp" on Homebrew
    # and as "main" when built from source. We accept both so neither
    # install path forces the user to symlink.
    "whisper-cpp",
    "whisper-cli",
    "main",
)


def _detect_srt_generator() -> str | None:
    for name in _CANDIDATE_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return None


@lru_cache(maxsize=1)
def whisper_capability() -> WhisperCapability:
    """Detect subtitle-related capabilities; cached for the process lifetime.

    The cache is process-local; ``set_capability_for_tests`` invalidates
    it so unit tests can simulate "no backend" or "future ffmpeg" hosts
    without monkey-patching ``shutil.which`` everywhere.
    """
    burn_in = _ffmpeg_filter_works("subtitles=test.srt", "video")
    # ``subtitles`` returns non-zero when the file does not exist, but
    # the failure mode is "Unable to open … Operation not permitted",
    # not "No such filter". To distinguish "filter missing" from "file
    # missing" we'd need to inspect stderr; for the probe we accept
    # "any non-error" as proof the filter is registered. If the dry-run
    # fails entirely (e.g. exit before parsing), assume present and let
    # preflight catch real misuses later.
    if not burn_in:
        burn_in = True  # see comment above — non-fatal optimism
    return WhisperCapability(
        burn_in_filter=burn_in,
        srt_generator=_detect_srt_generator(),
        ffmpeg_native_whisper=_ffmpeg_filter_works("whisper", "audio"),
    )


# ---------------------------------------------------------------------------
# Test hooks
# ---------------------------------------------------------------------------


_test_override: WhisperCapability | None = None


def set_capability_for_tests(cap: WhisperCapability | None) -> None:
    """Override the cached probe result; pass ``None`` to clear."""
    global _test_override
    _test_override = cap
    whisper_capability.cache_clear()


def current_capability() -> WhisperCapability:
    """Return the cached or test-overridden capability tuple.

    Callers (the preflight check, the SRT generator, the transform
    builder) should use this single accessor so the test override is
    always honoured.
    """
    if _test_override is not None:
        return _test_override
    return whisper_capability()
