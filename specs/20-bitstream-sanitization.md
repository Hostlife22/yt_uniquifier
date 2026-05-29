# Spec 20 — Bitstream sanitization (v0.4.3)

> **Phase 20 (v0.4.3)** · 0.5 day · **Deps:** v0.4.0

## Context

The v0.3.3 audit flagged a residual file-level signature: H.264 / HEVC
bitstreams produced by different encoders carry distinct microsigntures
that ML-based detectors can use to fingerprint the *encoder family* even
when content-level metrics look fine. Concrete examples:

- **NVENC**: slice header patterns differ from libx264 (CABAC table choices,
  reference-picture list order). Public encoder-detection tools
  (`videosig`, `mediainfo --detailed`, academic encoder-detection papers)
  can identify NVENC output reliably.
- **libx264 vs libx265**: codec choice is detectable from container even
  before bitstream analysis.
- **VideoToolbox / QSV / AMF**: each leaves its own footprint.

For users who explicitly want their output to be **indistinguishable from
a generic ffmpeg/libx264 YouTube upload** (the modal YouTube creator
upload signature), this matters. For everyone else, it's overhead.

The fix is opt-in: an extra pass that re-encodes the v0.4.0 output through
libx264 with stock parameters, stripping any encoder-specific bitstream
artefacts. The audio passes through with `copy` — it's already AAC.

This is the last v0.4 phase because it's the most narrowly useful:
v0.4.0/1/2 strengthen the predictor and validate against reality;
v0.4.3 adds a tool for users with a very specific "look like everyone
else" goal.

## Goal

After v0.4.3:

- New CLI flag `--sanitize-bitstream` on `yt-uniq run`.
- When set, after the regular pipeline produces `output.mp4`, an extra
  ffmpeg pass re-encodes through libx264 at CRF 20 (close to the input
  CRF 18 quality), audio stream-copied. The intermediate file is
  overwritten.
- ~3 VMAF point drop is acceptable; documented as the trade-off.
- For libx264-source runs the sanitization is a no-op (skipped) — the
  output is already libx264.
- For NVENC/QSV/AMF/VideoToolbox-source runs, the second pass actually
  fires.
- Tag: `v0.4.3`.

## Scope

**In:**

- `--sanitize-bitstream` flag on `cli/cmd_run.py`.
- `core/sanitizer.py` with `sanitize_bitstream(input_path, output_path)`
  helper.
- Integration into `orchestrator.run_full` as a post-concat step.
- Skip if the encoder was already libx264 (avoid double-encoding for no
  reason).
- Smoke test on a fixture.

**Not in:**

- Sanitization for HEVC / 10-bit pipelines — v0.4.3 ships H.264-only;
  HDR keep-hdr runs explicitly refuse the flag with a clear error.
- Configurable target codec — libx264 only. The point of sanitization is
  to look like the modal upload; libx264 IS the modal upload.
- Automatic VMAF re-measurement after sanitization — the QA report
  already runs on the final output, which is the sanitized output.
- Variance in re-encode params per run — sanitization output should look
  consistent (that's the whole point); we use fixed CRF 20, preset
  medium.

## Workitem 1 — `core/sanitizer.py`

**File:** `src/yt_uniquifier/core/sanitizer.py` (new)

```python
"""Second-pass libx264 re-encode to strip encoder-family bitstream signatures.

After the main pipeline produces output.mp4 (potentially via NVENC, QSV,
AMF, or VideoToolbox), this module re-encodes through libx264 with stock
parameters. Audio passes through with stream copy.

The goal is NOT additional perceptual divergence — that's the job of the
transform pipeline. The goal is to make the *file-level encoder
signature* match the modal "creator uploads from a laptop with stock
ffmpeg" pattern.

This is opt-in via `yt-uniq run --sanitize-bitstream`. For users who don't
care about encoder fingerprinting, it adds wall time + ~3 VMAF points of
quality drop for no benefit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import EncoderCandidate
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

# CRF 20 is one notch below the source pipeline's CRF 18, giving us ~3
# VMAF points of quality drop in exchange for a clean libx264 bitstream.
SANITIZE_CRF = 20
SANITIZE_PRESET = "medium"


def needs_sanitization(encoder: EncoderCandidate) -> bool:
    """True if the produced bitstream came from a non-libx264 encoder."""
    return encoder.vendor not in ("x264",)


def sanitize_bitstream(input_path: Path, output_path: Path) -> None:
    """Re-encode input → output via libx264 stock; audio stream-copied.

    Both paths can be the same file — we write to a `.sanitized.mp4` temp
    next to output_path, then atomic-rename.
    """
    if not input_path.exists():
        raise PipelineError(f"sanitize: input not found: {input_path}")

    tmp = output_path.with_suffix(".sanitized.mp4")
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", SANITIZE_PRESET,
        "-crf", str(SANITIZE_CRF),
        "-pix_fmt", "yuv420p",  # 8-bit only; HEVC 10-bit refused upstream
        "-c:a", "copy",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-map", "0:s?",
        "-c:s", "copy",
        "-map_chapters", "0",
        "-map_metadata", "-1",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=3600)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"sanitize_bitstream failed: {exc.stderr.strip()[-500:]}"
        ) from exc

    # Atomic replace.
    tmp.replace(output_path)


def reject_for_hdr(plan_keep_hdr: bool, encoder: EncoderCandidate) -> None:
    """Raise if sanitization would break an HDR keep-hdr / 10-bit run.

    libx264 has no 10-bit profile; we'd corrupt the HDR output.
    """
    if plan_keep_hdr:
        raise PipelineError(
            "sanitize-bitstream requires SDR/8-bit output; profile has "
            "keep_hdr=true. Drop --sanitize-bitstream or use video.tonemap_sdr "
            "in the profile to produce SDR first."
        )
    if encoder.codec == "hevc":
        raise PipelineError(
            f"sanitize-bitstream re-encodes via libx264 (h264). The current "
            f"encoder is {encoder.name} (hevc). Re-encoding HEVC to H.264 "
            f"may not be the intent; remove --sanitize-bitstream or set "
            f"profile.target_codec=h264."
        )
```

## Workitem 2 — CLI integration

**File:** `src/yt_uniquifier/cli/cmd_run.py`

Add a new option:

```python
@app.command()
def run(
    # ...existing options...
    sanitize_bitstream: bool = typer.Option(
        False, "--sanitize-bitstream",
        help=(
            "After the main pipeline, re-encode the output via libx264 "
            "to strip encoder-family bitstream signatures (NVENC / QSV / "
            "AMF / VideoToolbox → libx264). Adds wall time + ~3 VMAF "
            "points of quality drop. SDR / 8-bit only — incompatible "
            "with keep_hdr=true profiles."
        ),
    ),
    # ...
) -> None:
    # ...existing run logic...
    # Pass through RunOptions; orchestrator handles the actual work.
    options = RunOptions(
        # ...
        sanitize_bitstream=sanitize_bitstream,
    )
```

## Workitem 3 — RunOptions field

**File:** `src/yt_uniquifier/core/orchestrator.py`

Add the field to `RunOptions`:

```python
@dataclass(frozen=True)
class RunOptions:
    # ...existing fields...
    sanitize_bitstream: bool = False
```

## Workitem 4 — Orchestrator post-concat hook

**File:** `src/yt_uniquifier/core/orchestrator.py::run_full`

After concat completes and before the auto-QA pass, optionally sanitize:

```python
def run_full(plan, options, on_event=..., cancel_token=...):
    # ...existing video segment processing, main audio, concat...

    # Bitstream sanitization (post-concat, pre-QA).
    if options.sanitize_bitstream:
        from yt_uniquifier.core.sanitizer import (
            needs_sanitization, reject_for_hdr, sanitize_bitstream,
        )
        reject_for_hdr(plan.profile.keep_hdr, plan.encoder)
        if needs_sanitization(plan.encoder):
            on_event(RunEvent(kind="log", payload={
                "phase": "sanitize",
                "message": f"re-encoding {plan.encoder.vendor} output via libx264",
            }))
            sanitize_bitstream(options.output, options.output)
        else:
            on_event(RunEvent(kind="log", payload={
                "phase": "sanitize",
                "message": "encoder is already libx264 — skipping (no-op)",
            }))

    # QA report runs on the (possibly sanitized) final output.
    # ...existing QA logic...
```

## Workitem 5 — Tests

**File:** `tests/unit/test_sanitizer.py`

```python
def test_needs_sanitization_for_nvenc():
    enc = EncoderCandidate(name="h264_nvenc", vendor="nvenc", codec="h264", works=True)
    assert needs_sanitization(enc)

def test_no_sanitization_for_x264():
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    assert not needs_sanitization(enc)

def test_reject_for_keep_hdr():
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    with pytest.raises(PipelineError, match="keep_hdr"):
        reject_for_hdr(plan_keep_hdr=True, encoder=enc)

def test_reject_for_hevc():
    enc = EncoderCandidate(name="hevc_nvenc", vendor="nvenc", codec="hevc", works=True)
    with pytest.raises(PipelineError, match="hevc"):
        reject_for_hdr(plan_keep_hdr=False, encoder=enc)
```

**File:** `tests/integration/test_sanitize_real_ffmpeg.py`

```python
@needs_ffmpeg
@pytest.mark.integration
def test_sanitize_produces_libx264_output(tiny_clip: Path, tmp_path: Path):
    """Run a non-x264 encoder, then sanitize, verify output is x264."""
    # Generate via libx264 first (so the test runs on any CI without NVENC).
    pre = tmp_path / "pre.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(tiny_clip), "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(pre),
    ], check=True)

    out = tmp_path / "sanitized.mp4"
    from yt_uniquifier.core.sanitizer import sanitize_bitstream
    sanitize_bitstream(pre, out)
    assert out.exists()
    # Verify it's still h264 + AAC.
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert "codec_name=h264" in res.stdout
    assert "codec_name=aac" in res.stdout
```

## Acceptance

```bash
# 1. Flag is present in --help.
yt-uniq run --help | grep -q "sanitize-bitstream"

# 2. SDR run with --sanitize-bitstream succeeds.
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out_sanitized.mp4 --encoder libx264 --sanitize-bitstream
ffprobe -v error -show_streams out_sanitized.mp4 | grep "codec_name=h264"

# 3. HDR profile + flag is rejected with a clear error.
yt-uniq run tests/fixtures/720.mp4 \
  --profile src/yt_uniquifier/profiles/medium_hdr.yaml \
  --out should_fail.mp4 --encoder libx265 --sanitize-bitstream 2>&1 \
  | grep -q "keep_hdr=true"
# Expected: non-zero exit + the explanatory error

# 4. libx264 → libx264 sanitize is a logged no-op.
yt-uniq run tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out out_noop.mp4 --encoder libx264 --sanitize-bitstream 2>&1 \
  | grep -q "no-op"

# 5. Tests + lint.
pytest -q
ruff check . && mypy src/yt_uniquifier
```

## Tests

| Уровень | Файл | Цель |
|---|---|---|
| Unit | tests/unit/test_sanitizer.py | 4 tests — needs_sanitization for NVENC, no-op for x264, reject HDR, reject HEVC |
| Integration | tests/integration/test_sanitize_real_ffmpeg.py | 1 test — real ffmpeg pre-encode + sanitize, verify h264 + aac output |

Total: 5 new tests.

## Risks

| Риск | Митигация |
|---|---|
| ~3 VMAF point drop unacceptable for some users | Documented as the trade-off; flag is opt-in; the regular pipeline output (without --sanitize-bitstream) is unchanged |
| Re-encode breaks chapters / multi-audio mapping | We explicitly `-map 0:a? -map 0:s? -map_chapters 0` — verified by the integration test |
| HEVC source + flag silently re-encodes to H.264 (loss of quality and intent) | `reject_for_hdr` raises with a clear error; user must explicitly drop the flag or change profile.target_codec |
| Sanitization doubles wall time on long inputs | Documented as the trade-off; not the default. On a 2h source the second pass adds ~30–60 min at libx264 medium preset, CRF 20 |
| Sanitizing a sanitized file (double-sanitize on resume) | Sanitization is a final post-concat step, not segmented; resume of the same plan_hash will redo it but produces the same output (libx264 is deterministic) |
| Encoder fingerprint not actually removable — libx264 itself is a signature | TRUE — libx264 IS a signature. The point is to make output look like *modal* user uploads, which are libx264-via-ffmpeg. Users wanting "no encoder signature at all" don't have a solution; that's the nature of compression |

## Hand-off

After v0.4.3:

- Users who explicitly want libx264-bitstream output can opt in via
  `--sanitize-bitstream`. Default behaviour unchanged.
- Output produced via NVENC + sanitize is bitwise indistinguishable
  (at the slice-header / SEI level) from a generic libx264 upload.
- HDR / HEVC paths are explicitly protected from accidental
  sanitization.
- Documentation updated with the trade-off table.

Tag: `v0.4.3`. End of the v0.4 roadmap.

## Effort

| Item | Time |
|---|---|
| 1. `core/sanitizer.py` (functions + reject_for_hdr) | 1 hour |
| 2. CLI flag + RunOptions field | 30 min |
| 3. Orchestrator hook | 30 min |
| 4. 5 tests (4 unit + 1 integration) | 1 hour |
| 5. Docs update (architecture.md + youtube_targets.md note) | 30 min |
| 6. Lint, type-check, commit, tag | 30 min |
| **Total** | **~4 hours / 0.5 day** |
