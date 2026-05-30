# Runbook: Scale validation test

Referenced by `specs/10-scale-validation.md`.

This runbook describes how to validate the segmenter + resume + concat
pipeline against multi-hour inputs without spending CI budget.

## Goal

Verify, end-to-end, that:

1. `plan_segments` produces keyframe-aligned cuts on a long source.
2. `process_video_segments_parallel` honours `workers` and the encoder
   `max_parallel` cap.
3. `CheckpointStore` survives a crash-and-resume cycle and the resumed
   run reproduces identical output byte-for-byte (for deterministic
   `seed_strategy="fixed"`) or identical-per-segment for
   `seed_strategy="divergent"`.
4. `concat_segments` mux of N stream-copy segments + the separately
   processed main audio plays back without seam artifacts.

## Prerequisites

- ffmpeg ≥ 5 with `libx264`, `libfdk_aac` or `aac`, `libvmaf`.
- A source clip 30–120 minutes long. The CI repo intentionally does
  not check in fixtures of this size; provide your own.
- Disk: ~3× source size free for `work_dir` (segments + main_audio).

## Steps

```bash
# 1) Smoke check on a 60 s clip (sanity).
make test-integration

# 2) Long-run, sequential.
yt-uniq run \
  --input ~/Movies/source_60min.mp4 \
  --output /tmp/scale_seq.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --workers 1 \
  --keep-segments \
  --work-dir /tmp/scale_seq.work

# 3) Long-run, parallel.
yt-uniq run \
  --input ~/Movies/source_60min.mp4 \
  --output /tmp/scale_par.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --workers 4 \
  --keep-segments \
  --work-dir /tmp/scale_par.work

# 4) Resume from mid-encode crash.
yt-uniq run --input ... --output /tmp/scale_resume.mp4 \
  --work-dir /tmp/scale_resume.work &
PID=$!
sleep 90 && kill -9 "$PID"      # simulate crash
yt-uniq run --input ... --output /tmp/scale_resume.mp4 \
  --work-dir /tmp/scale_resume.work   # should resume from last 'done' segment
```

## Acceptance

| Step | Pass criteria |
|---|---|
| 2 | Output plays back; `yt-uniq qa` reports VMAF mean ≥ 88; no seam glitches at segment boundaries. |
| 3 | Same VMAF / artifact criteria as (2). Wall-time ≥ 2× faster than (2) on a 4-core CPU. |
| 4 | Resumed run completes without re-encoding 'done' segments (check `state.json` segment statuses). Output bit-identical to a single uninterrupted run for `seed_strategy="fixed"`. |

## Out-of-scope

- Distributed / multi-host runs — see `docs/distributed.md`.
- HDR pipeline — see `docs/architecture.md` HDR section + `specs/06-hdr-pipeline.md`.

## Owner

Whoever last touched `core/segmenter.py` or `core/checkpoint.py`.
