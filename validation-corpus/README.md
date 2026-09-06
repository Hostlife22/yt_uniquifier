# Natural-content validation corpus

This directory is a local-only launch point for benchmarks on media you own or are
licensed to process. Media and results are ignored by Git. Rights evidence should
stay in your controlled records; the manifest stores only an internal reference.

For a reproducible open-content smoke corpus, `open-sources.yaml` pins the upstream
page, license, size, and SHA-256 for each asset. Fetch or re-verify it with:

```bash
.venv/bin/python tools/fetch_open_validation_corpus.py
.venv/bin/python tools/fetch_open_validation_corpus.py --verify-only
```

The Meridian MP4 is labelled P3/PQ by its publisher but has neither readable color
tags nor a 10-bit pixel format. It must not be treated as a native HDR10 contract
fixture. Any tagged HDR10/HLG derivative must record its exact conversion command.

1. Copy `manifest.example.yaml` to `manifest.local.yaml`.
2. Put source files under `media/` and replace every placeholder rights reference.
3. Validate without media while preparing the manifest:

   ```bash
   .venv/bin/python tools/natural_corpus.py validate \
     validation-corpus/manifest.example.yaml --allow-missing-media
   ```

4. Validate real files and run the complete named current/proposed matrix with
   one command:

   ```bash
   make production-benchmark
   ```

   Override paths when needed with
   `CORPUS_MANIFEST=/path/manifest.yaml CORPUS_RESULTS=/path/results`.

Add `--with-sscd` only when the `[ml]` extra and approved model weights are present.
Each cell retains its exact Plan, benchmark/QA JSON, HTML and logs. Aggregate
`summary.json`, `summary.csv`, and `summary.html` report VMAF, SSIM, PSNR, integrated
LUFS, true peak, source/output size, encode time, and peak process-tree RAM. The
source section records source audio/size baselines; the comparison section reports
candidate-minus-current deltas. Registered RFC #12 metrics are included when the
exact Plan can be replayed. Raw similarity fields remain diagnostics, not evidence
about any external rights-management system.

The runner performs full-file metric passes. A 1–3 hour corpus can therefore take
substantially longer than the encode itself; keep each rights reference auditable and
do not substitute unlicensed media merely to shorten the run.

## Extended measurements and listening review

`manifest.extended.example.yaml` is the reproducible eight-cell extended matrix:
4K/5.1, derived PQ/HLG current-versus-tonemap-only, and continuous 176/180-minute
historical films. Fetch the pinned sources and prepare derivatives using
`DERIVATIONS.md`, then run:

```bash
.venv/bin/python tools/natural_corpus.py validate validation-corpus/manifest.extended.example.yaml
.venv/bin/python tools/natural_corpus.py run validation-corpus/manifest.extended.example.yaml \
  --results validation-corpus/results/extended-reproduction --decode-timelines
```

Completion means **measured**, not production-approved. Inspect `qa_verdict`,
missing measurements, delivery peaks and human-review status independently.
The matrix is intentionally slow; full-film QA performs multiple full decodes.

Use `--decode-timelines` on `tools/natural_corpus.py run` to include full decoded
frame counts, native-rate audio sample counts, non-increasing/missing PTS and
audio-minus-video endpoint deltas. Counts include what the decoder emits (including
codec padding); endpoint agreement is **not** proof of internal lip sync. Missing
frame durations remain unknown, never inferred from an average FPS.

Every encode cell samples logical sizes of its dedicated work/output directories
once per second. `disk_peak_logical_bytes` is a sampled lower bound, not allocated
blocks or device I/O, and excludes QA references created in the OS temporary
directory. `wall_sec`/`rss_peak_kb` describe encoding; `qa_wall_sec` and
`qa_rss_peak_kb` separately describe the QA process tree. Do not compare timing from
simultaneous qualification jobs with an isolated performance baseline.

Prepare lossless excerpts without gain matching (source channels retained):

```bash
.venv/bin/python tools/listening_review.py \
  --source validation-corpus/media/natural-4k-surround-30s.mkv \
  --current validation-corpus/results/extended-4k-surround/natural-4k-surround__current/output.mp4 \
  --proposed validation-corpus/results/extended-4k-surround/natural-4k-surround__proposed/output.mp4 \
  --out validation-corpus/results/listening-surround \
  --start 2 --duration 20 --rights-reference open-sources.yaml#tears-of-steel-surround
```

`review.json` records float-WAV hashes, LUFS, true peak, per-channel full-scale
samples/nonfinite values and pairwise zero-lag correlation. Silence is null, not
NaN. Correlation is only a diagnostic: normal surround decorrelation does not mean
phase damage. Listen on a correctly mapped 5.1 system, compare dialogue/music,
check clicks and tonal transitions, and record a human verdict separately.
The tool refuses to overwrite an existing review directory's clips.

`profiles/tonemap-review.yaml` is a benchmark-only candidate using the existing
Hable tonemap without added crop/noise/color jitter. It is **not** a new shipped
profile or an approved replacement. Compare source/current/proposed on the same
display; native HDR grading and final visual approval still require suitable
HDR hardware and a human reviewer.

The retained September 6 review includes three side-by-side SDR PNG previews at
5/15/25 seconds under `results/extended-tonemap-review/hdr-dark-skin-highlight-review/`.
Panel order is source through the existing default Hable SDR proxy, current,
proposed. These downscaled previews reveal gross damage, not native HDR mastering
accuracy or temporal motion quality; compare original-resolution videos too.
