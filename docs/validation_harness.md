# Authorized platform validation harness

This workflow validates an authorized derivative after local processing and a
platform transcode. It is for video you own or are licensed to process. It does not
optimize against YouTube Content ID or any other rights-management system.

## What the validation can answer

- Did the local output preserve the declared streams, duration, cadence, color and
  HDR policy?
- Did the platform accept and finish processing the upload?
- Did its transcode introduce visible banding, ringing, motion problems, clipping,
  loudness changes or A/V drift?
- Are internal pHash, audio fingerprint and SSCD diagnostics reproducible for a
  pinned source, profile, seed and toolchain?

It cannot prove ownership, licensing, fair use, perceptual quality, or future behavior
of a platform's private systems. A claim or restriction is a rights/compliance signal
to resolve, not a parameter-optimization target.

## Preconditions

- Keep evidence that you own the source or have a licence permitting this processing
  and upload.
- Use a non-public visibility setting while validating.
- Record the source checksum, profile, seed, encoder, FFmpeg version, application
  commit and output checksum.
- Pass local correctness checks before uploading. Never use platform playback to hide
  a local decode, timeline, stream-topology or HDR failure.

## Step 1 — produce one controlled candidate

Start with the smallest transform set required by the editorial/delivery goal:

```bash
yt-uniq preflight master.mp4 --profile soft
yt-uniq run master.mp4 --profile soft --out candidate.mp4 --encoder libx264
yt-uniq qa master.mp4 candidate.mp4 --vs-corpus
```

Inspect the final media with `ffprobe`, decode every audio/video stream, and review the
generated QA JSON/HTML. Raw source/output VMAF is not an encode-quality score when a
transform changes geometry or time; use a registered reference or record the result
as `NOT VERIFIED`.

## Step 2 — record the local baseline

Store at least:

| Field | Purpose |
|---|---|
| source/output SHA-256 | binds evidence to exact files |
| application commit and profile JSON | reproducibility |
| encoder, FFmpeg and OS/hardware | capability provenance |
| stream topology and per-stream start/end | catches loss and A/V drift |
| decoded frame/audio-sample counts | catches truncation and duplication |
| color/HDR metadata | catches transfer/primaries/range loss |
| LUFS and true peak | audio acceptance |
| registered VMAF/SSIM/PSNR, when valid | visual regression decision |
| wall time, peak RAM, temporary disk and output size | operational budget |

pHash, Chromaprint, SSCD and the legacy `cid_predict_self` field may be retained as
separate internal diagnostics. They must not override a failed correctness or quality
gate.

## Step 3 — manual platform check

Upload only the authorized candidate through the normal platform UI and keep it
unlisted/private during validation. After processing completes, check representative
dark, bright, gradient, text, skin-tone and high-motion sections plus the beginning,
middle and end of long-form media. Listen on headphones and speakers when audio
effects are enabled.

Record:

- upload and processing completion times;
- resolutions/codecs offered by playback;
- visible artifacts and subjective severity;
- A/V sync at internal flash/impulse or dialogue cues;
- loudness/clipping observations;
- any platform processing error;
- any rights claim/restriction, handled through the platform's supported resolution
  process and your ownership/licence evidence.

Do not generate stronger variants in response to a rights claim. That would turn a
quality validation into detection-evasion optimization and is outside project scope.

## Step 4 — compare results

Compare `source → local candidate → platform playback` by content class. Accept a
profile only when correctness passes and its registered quality/resource distributions
meet the thresholds approved in `BENCHMARKS.md`. Keep subjective review notes next to
machine metrics; a lower similarity score is not a quality improvement.

The historical `tools/generate_variants.py` and `tools/validation_correlate.py`
utilities remain for backwards compatibility. Their legacy no-match correlation is
not a production acceptance criterion and should not be used to tune external-system
behavior.

## Release evidence

Retain the exact commands, JSON reports, environment manifest and aggregate results.
If a platform transcode cannot be downloaded or measured reproducibly, mark those
metrics `NOT VERIFIED` rather than inferring them from browser playback.
