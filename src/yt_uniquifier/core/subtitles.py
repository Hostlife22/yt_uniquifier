"""Whisper-cpp SRT generator (v0.9.0 R2 / F14).

A thin, cacheable wrapper around the whisper-cpp CLI. Produces an SRT
sidecar next to (or wherever the caller wants) the source video so the
``video.subtitles`` transform can burn it in deterministically.

Why a CLI helper rather than an in-pipeline auto-generate step:

* whisper-cpp loads a multi-hundred-MB model on every invocation; doing
  so per encode is wasteful and breaks the per-segment retry / parallel
  worker contract.
* SRT timestamps can be eyeballed and edited; pre-generating gives the
  user a chance to correct mistakes before burn-in.
* The result is path-keyed in the user's filesystem, which means the
  ``Profile`` is reproducible across machines that share the SRT (e.g.
  distributed batch + shared NFS).

Caching is by ``(source_size, source_mtime_ns, model_basename,
language)`` — same trick as the v0.6 B1 keyframe cache. Re-running
``generate_srt`` on an unchanged source returns the cached SRT instantly.

Audio is extracted via ffmpeg at 16 kHz mono PCM (Whisper's native
input format) into a tempfile, then deleted after the subprocess
returns. We never pipe raw audio through Python.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yt_uniquifier.core.errors import YtUniquifierError
from yt_uniquifier.core.transforms._whisper_probe import current_capability
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

_log = logging.getLogger(__name__)


class SubtitleGenerationError(YtUniquifierError):
    """Whisper-cpp subprocess failed or output was unusable."""


@dataclass(frozen=True)
class SrtResult:
    """Where the SRT landed + which backend produced it."""

    path: Path
    backend: str
    from_cache: bool


_AUDIO_SAMPLE_RATE = 16000  # whisper-cpp default; resampling is implicit
_DEFAULT_MAX_LEN = 42  # chars per line; matches SubtitleBurnParams default
_AUDIO_EXTRACT_TIMEOUT_SEC = 600
_WHISPER_TIMEOUT_SEC = 3600


def _cache_key(source: Path, model_path: Path, language: str | None) -> str:
    stat = source.stat()
    parts = [
        str(stat.st_size),
        str(stat.st_mtime_ns),
        model_path.name,
        language or "auto",
    ]
    return "__".join(parts)


def _default_dest(source: Path) -> Path:
    return source.with_suffix(".srt")


def _extract_audio_to_wav(source: Path, dest: Path) -> None:
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-ar", str(_AUDIO_SAMPLE_RATE),
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(dest),
    ]
    try:
        subprocess.run(  # noqa: S603 — argv list, no shell
            cmd, check=True, capture_output=True, timeout=_AUDIO_EXTRACT_TIMEOUT_SEC,
        )
    except subprocess.CalledProcessError as exc:
        raise SubtitleGenerationError(
            f"ffmpeg audio extract failed (exit {exc.returncode}): "
            f"{(exc.stderr or b'').decode('utf-8', 'replace').strip()[-200:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SubtitleGenerationError(
            f"ffmpeg audio extract timed out after {_AUDIO_EXTRACT_TIMEOUT_SEC}s"
        ) from exc


def _run_whispercpp(
    binary: str,
    wav_path: Path,
    model_path: Path,
    language: str | None,
    out_stem: Path,
    max_len: int,
) -> None:
    cmd = [
        binary,
        "-m", str(model_path),
        "-f", str(wav_path),
        "-osrt",
        "-of", str(out_stem),
        "-ml", str(max_len),
    ]
    if language:
        cmd.extend(["-l", language])
    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            cmd, check=False, capture_output=True, timeout=_WHISPER_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubtitleGenerationError(
            f"whisper-cpp timed out after {_WHISPER_TIMEOUT_SEC}s"
        ) from exc
    if proc.returncode != 0:
        raise SubtitleGenerationError(
            f"whisper-cpp failed (exit {proc.returncode}): "
            f"{(proc.stderr or b'').decode('utf-8', 'replace').strip()[-200:]}"
        )


def generate_srt(
    source: Path,
    model_path: Path,
    *,
    language: str | None = None,
    dest: Path | None = None,
    max_chars_per_line: int = _DEFAULT_MAX_LEN,
    cache_dir: Path | None = None,
    force: bool = False,
) -> SrtResult:
    """Produce an SRT for ``source`` using whisper-cpp.

    ``model_path`` points at a ggml model file (e.g.
    ``ggml-base.bin``). Cached under ``cache_dir`` so repeated calls on
    an unchanged source return instantly.

    Raises ``SubtitleGenerationError`` when no whisper backend is on
    PATH, when the model is missing, or when the subprocess fails.
    """
    if not source.exists():
        raise SubtitleGenerationError(f"source file not found: {source}")
    if not model_path.exists():
        raise SubtitleGenerationError(f"whisper model not found: {model_path}")

    cap = current_capability()
    backend = cap.srt_generator
    if backend is None:
        raise SubtitleGenerationError(
            "no whisper backend on PATH. Install whisper.cpp "
            "(brew install whisper-cpp on macOS, or build from "
            "https://github.com/ggml-org/whisper.cpp) and retry."
        )

    dest_path = (dest or _default_dest(source)).resolve()
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    cache_root = cache_dir or (Path.home() / ".cache" / "yt_uniquifier" / "subtitles")
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_marker = cache_root / f"{_cache_key(source, model_path, language)}.srt"

    if not force and cache_marker.exists() and dest_path.exists():
        # Bit-for-bit identical inputs already produced this SRT.
        return SrtResult(path=dest_path, backend=backend, from_cache=True)

    with tempfile.TemporaryDirectory(prefix="yt-uniq-whisper-") as tmpdir:
        tmp = Path(tmpdir)
        wav_path = tmp / "audio.wav"
        out_stem = tmp / "out"
        _extract_audio_to_wav(source, wav_path)
        _run_whispercpp(
            backend, wav_path, model_path, language, out_stem, max_chars_per_line,
        )
        produced = out_stem.with_suffix(".srt")
        if not produced.exists():
            raise SubtitleGenerationError(
                f"whisper-cpp completed but no SRT at {produced}. "
                "Check the binary version (need -osrt support)."
            )
        # Atomic move into final location, then cache marker.
        shutil.copyfile(produced, dest_path)
    try:
        shutil.copyfile(dest_path, cache_marker)
    except OSError as exc:  # cache is best-effort; never fail the call
        _log.warning("could not write subtitle cache marker %s: %s",
                     cache_marker, exc)
    return SrtResult(path=dest_path, backend=backend, from_cache=False)


def find_default_model(search_dirs: list[Path] | None = None) -> Path | None:
    """Best-effort lookup of a ggml whisper model on the local filesystem.

    Used by the CLI when ``--model`` is not specified. Searches the
    typical whisper.cpp install locations (Homebrew shared, user
    ~/.cache/whisper, the binary's neighbouring ``models/`` dir) and
    returns the first ``ggml-*.bin`` found, preferring base/small over
    tiny.
    """
    candidates: list[Path] = []
    home = Path.home()
    defaults = [
        home / "models",
        home / ".cache" / "whisper",
        home / "Library" / "Application Support" / "whisper.cpp" / "models",
        Path("/opt/homebrew/share/whisper-cpp"),
        Path("/usr/local/share/whisper-cpp"),
    ]
    for d in (search_dirs or []) + defaults:
        if d.exists() and d.is_dir():
            candidates.extend(sorted(d.glob("ggml-*.bin")))
    # Preference order: small, base, medium, large, tiny — pick the
    # first that's actually present.
    for marker in ("ggml-small", "ggml-base", "ggml-medium",
                   "ggml-large", "ggml-tiny"):
        for c in candidates:
            if c.name.startswith(marker):
                return c
    return candidates[0] if candidates else None


def is_subtitle_extension_supported(path: Path) -> bool:
    """ffmpeg's ``subtitles`` filter supports SRT/ASS/SSA/SBV/VTT."""
    return path.suffix.lower() in {".srt", ".ass", ".ssa", ".sbv", ".vtt"}


# Re-export for backward-compat with the os.path callers we used to
# have inline. Tests can monkey-patch this to avoid actually running
# the subprocesses.
__all__ = [
    "SrtResult",
    "SubtitleGenerationError",
    "find_default_model",
    "generate_srt",
    "is_subtitle_extension_supported",
]
