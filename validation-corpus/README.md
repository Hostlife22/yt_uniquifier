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
