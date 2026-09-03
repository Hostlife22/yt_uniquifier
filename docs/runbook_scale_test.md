# Runbook: Scale validation and long-form recovery

Referenced by `specs/10-scale-validation.md`. This procedure qualifies the existing
segment/checkpoint/audio/concat pipeline; it does not introduce a second pipeline.

## Scope and evidence levels

Use only media you own or are licensed to process. Run each declared production
combination separately: container, SDR/HDR mode, encoder, channel layout and host.

- `VERIFIED`: retained command, manifest, logs and QA/benchmark JSON exist for that
  exact combination.
- `LIMITED`: a synthetic fixture passed, but the natural licensed corpus did not.
- `NOT VERIFIED`: no retained run exists. Do not infer support from encoder discovery.

The 2026-09-03 Intel Mac baseline is retained in `BENCHMARKS.md`: synthetic 1/2/3 h
SDR libx264 runs preserved all `7,200/14,400/21,600` frames. A 1 h interrupted run
reused the first two segments without changing their SHA-256 or mtime. Natural
long-form footage, NFS, NVENC/QSV/AMF and hardware-HDR recovery remain `NOT VERIFIED`.

## Prerequisites

- Python environment from `make dev` and `.venv/bin/yt-uniq`.
- `ffmpeg` and `ffprobe` on `PATH`; libvmaf is required only for the chaos-equivalence
  assertion.
- A fixed source checksum and enough free space. Start with `--dry-run`; do not rely on
  a fixed `3× source` estimate for high-bitrate intermediates.
- A dedicated work directory and output path per case. Never share a work directory
  between live jobs.

Record before every run:

```bash
shasum -a 256 "$SOURCE"
ffmpeg -version | head -n 1
.venv/bin/yt-uniq run "$SOURCE" \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out "$OUTPUT" --encoder libx264 --work-dir "$WORK" --dry-run
```

## Baseline and parallel qualification

```bash
# Sequential baseline. Keep artifacts so resume identity can be inspected.
.venv/bin/yt-uniq run "$SOURCE" \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out "$CASE_DIR/sequential.mp4" \
  --encoder libx264 --workers 1 --segment-sec 600 \
  --keep-segments --work-dir "$CASE_DIR/sequential.work"

# Parallel run of the same declared combination.
.venv/bin/yt-uniq run "$SOURCE" \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out "$CASE_DIR/parallel.mp4" \
  --encoder libx264 --workers 4 --segment-sec 600 \
  --keep-segments --work-dir "$CASE_DIR/parallel.work"
```

If the watermark guard triggers for authorized content, inspect the finding first and
then add `--accept-watermark-risk` as the explicit operator attestation.

## Crash and resume

The repository chaos test starts the whole CLI in a separate process group, sends
SIGKILL to the group at deterministic pseudo-random offsets, resumes the same work
directory, and compares the result with a clean baseline:

```bash
YT_UNIQ_CHAOS_ROUNDS=3 \
  .venv/bin/pytest tests/chaos/test_random_sigkill.py -q
```

For a long manual case, capture `state.json` and a manifest of completed segment
SHA-256/mtime values immediately before interruption. Terminate the entire CLI/FFmpeg
process group, not only the parent Python process. Resume with the exact original
command, output path and work directory. Do not pass `--new-variant` during recovery.

After resume, every segment that was durably `done` and still matches its recorded
SHA-256 must retain its SHA-256 and mtime. An `in_progress`, missing, zero-byte or
digest-mismatched segment must be reprocessed.

## Deterministic fault-injection gates

Run before a release candidate:

```bash
.venv/bin/pytest \
  tests/unit/test_orchestrator_checkpoint_lifecycle.py \
  tests/unit/test_checkpoint.py \
  tests/unit/test_main_audio_atomic.py \
  tests/unit/test_concat_segments_workdir.py \
  tests/integration/test_resume_truncated.py \
  tests/integration/test_resume_partial_cleanup.py -q
```

These tests cover rejected concurrent ownership, checkpoint initialization failure,
checkpoint `fsync`/disk-full behavior, partial audio cleanup, concat failure, final
replace failure, corrupt segments and partial cleanup. They assert that an existing
published output/state/audio artifact is not overwritten by a failed attempt.

## Validation and acceptance

Run the registered seam diagnostic against the retained checkpoint boundaries:

```bash
.venv/bin/python tools/seam_test.py "$OUTPUT" \
  --source "$SOURCE" \
  --work-dir "$WORK" \
  --frames 8 --search-frames 2 --threshold 0.005
```

The tool compares decoded source/output windows, resets local PTS and searches only
the configured bounded frame offset. A missing metric is a failure, not a silent
skip. Geometry/retiming transforms can still require the plan-aware transformed
reference proposed in RFC #12; retain raw output and do not relax the threshold to
hide an unregistered pair.

For every output retain `state.json`, FFmpeg logs, source/output SHA-256, QA JSON and a
benchmark result. Acceptance is correctness-first:

| Gate | Pass criterion |
|---|---|
| Decode | Primary video and every selected audio stream decode to EOF. |
| Timeline | First video PTS, duration, stream count/metadata and A/V delta satisfy the final media contract. |
| Seams | Frame count and monotonic timestamps match the expected cadence; inspect source/output around every planned boundary. |
| Resume | Valid completed segment hashes and mtimes are unchanged; damaged/incomplete artifacts are reprocessed. |
| Publication | Failed audio/concat/final replace leaves the previous valid artifact intact and no `.part` file. |
| Quality | VMAF/SSIM/PSNR are interpreted only when source/output are spatially and temporally registered; otherwise mark them not applicable. |
| Resources | Peak process-tree RSS, temporary disk high-water mark, wall time and encode time are retained. |

Bit-identical final files are not a universal acceptance rule: encoder thread
scheduling and container metadata can change bytes. For a fixed deterministic case,
require exact reuse of already-completed segment artifacts and validate the final
media contract plus registered quality metrics.

## Remaining mandatory matrix

- Owned/licensed natural content at 1 h, 2 h and 3 h+.
- Stereo, 5.1 and multiple audio tracks; MP4, MKV and MOV.
- SDR, HDR10 and HLG for every advertised encoder.
- APFS/local disk plus any actually deployed network filesystem.
- Power-loss/reboot and low-space tests on a disposable volume.

Never simulate a full disk on a developer's main volume. Use a disposable image,
container or dedicated CI worker and retain the recovery evidence.
