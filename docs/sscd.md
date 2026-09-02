# SSCD copy-detection QA

> Added in v0.8.0 (R4 — metric; R6 — calibrate-by-SSCD). See
> `specs/v0.8-plan.md`.

SSCD (Self-Supervised Copy Detection) is the embedding model Meta released
alongside the [VSC2022](https://ai.meta.com/research/publications/the-2022-video-similarity-challenge/)
dataset and used to deduplicate the LLaMA training corpus. Its strength is
robustness to crop, colour jitter, frame-rate retiming and re-encode. Here it is
used as an internal regression/self-collision diagnostic for owned or licensed
content. It does not predict or validate a third-party rights-detection system.

In `yt-uniquifier` SSCD is **opt-in**: the model is not bundled, torch is
not a hard dependency, and the metric runs only when you ask for it.

## Install

```bash
pip install 'yt-uniquifier[ml]'
```

This pulls a current Torch/torchvision pair. On first use the
~94 MB `sscd_disc_mixup` TorchScript checkpoint is fetched from the
official Meta CDN to `~/.cache/yt_uniquifier/models/` and verified by
SHA-256. A mismatching cached file is deleted and re-downloaded — the
hash is pinned in `core/qa/sscd.py::_MODEL_SHA256` so a CDN swap fails
loudly rather than silently using unknown weights.

Only the official TorchScript artifact is supported. Meta's upstream
project does not publish an ONNX checkpoint; a custom backend must be
provided explicitly through `model_loader`. The upstream SSCD project
is published under the MIT license; review that license before
redistributing the checkpoint.

Intel macOS is a constrained exception: PyPI offers only Torch 2.2.2 for that
architecture, and current vulnerability databases report advisories against that
line. The project therefore qualifies this combination only for the built-in SSCD
checkpoint whose exact SHA-256 is pinned above. Do not load untrusted Torch models
on Intel macOS; use Apple Silicon, Linux, Windows, or an injected non-Torch backend
when a fully patched ML runtime is required.

If `[ml]` is not installed, every public SSCD entry-point raises
`PipelineError` with the install hint above. The rest of the tool —
ffmpeg pipeline, chromaprint QA, calibration — stays usable.

## In the QA report

```bash
yt-uniq qa source.mp4 output.mp4 --sscd
# or, for finer per-frame resolution:
yt-uniq qa source.mp4 output.mp4 --sscd --sscd-frames 64
```

The console emits the banded headline (`high` / `caution` / `clean`) and
the HTML report (`<out>.qa.html`) renders a colour-coded per-frame
heatmap alongside the existing VMAF + chromaprint blocks. The JSON
sidecar gains three flat fields:

* `sscd_mean` — average cosine between matched frame pairs
* `sscd_min` — least-similar pair (useful for spotting a single outlier)
* `sscd_per_frame` — array of cosines aligned 1:1 with the source frame grid

### Threshold bands

Bands are picked from the SSCD paper's threshold-vs-precision curves
on the DISC21 evaluation set:

| Mean similarity | Band      | Reading                                    |
|----------------:|:----------|:-------------------------------------------|
| ≥ 0.85          | `high`    | High internal source/output similarity       |
| 0.65 – 0.85     | `caution` | Mixed result; inspect quality and alignment  |
| < 0.65          | `clean`   | Low similarity; inspect possible quality loss |

These legacy band names are diagnostic labels, not pass/fail goals. A lower score
can mean destructive transforms, temporal misalignment or a measurement failure;
it must be read alongside VMAF/SSIM, audio and media-contract results.

## In calibration

```bash
yt-uniq calibrate input.mp4 \
  --base profiles/cid_aware.yaml \
  --out tuned.yaml \
  --metric sscd \
  --target 0.8
```

`--metric sscd` swaps the v0.5 chromaprint predictor for an SSCD-driven
evaluator. SSCD's clamped mean cosine is passed directly to calibration:
higher means more similar, and convergence requires `mean_similarity <= target`
while the independent quality floor also passes. SSCD and Chromaprint targets are
not interchangeable and must be calibrated separately on an authorized corpus.

The chromaprint default is unchanged: omitting `--metric` runs the v0.7
loop verbatim, including the `fpcalc` runtime requirement.

GUI: the Calibrate screen has a **Metric** dropdown next to the
test-clip duration spinner.

## Determinism

Same input bytes + same model file + `torch.set_grad_enabled(False)` +
fixed frame grid → bit-identical embeddings, bit-identical cosines. Two
back-to-back `compute_sscd` calls return equal `SSCDResult` tuples.
This matters for resume: an SSCD-calibrated profile re-runs to the same
score on the same source, so a profile tuned today still converges on
the same clip in CI tomorrow.

## Public API

```python
from yt_uniquifier.core.qa.sscd import compute_sscd, sscd_band, SSCDResult

result: SSCDResult = compute_sscd(
    source=Path("in.mp4"),
    output=Path("out.mp4"),
    frame_count=32,            # default — 32 uniform samples
    cancel_token=token,        # optional, honoured between phases
    model_loader=None,         # test-only injection seam
)
print(result.mean_similarity, sscd_band(result.mean_similarity))
```

`model_loader` exists so unit tests can hand in a stub network without
the multi-hundred-MB torch wheel installed (see
`tests/unit/test_sscd_offline.py`).

## Cancellation

`compute_sscd` is a 5–10 s CPU-bound call at the default frame_count.
The `cancel_token` parameter is checked between each phase
(`model_load`, `extract_source`, `extract_output`, `embed`, `cosine`),
so a click on Cancel returns within a fraction of a second instead of
waiting for the full embed pass to finish.

`calibrate(metric="sscd")` forwards the same token into every iteration,
matching the v0.5.5 A6 behaviour for the chromaprint path.

## Architecture notes

* **Lazy import**: `import torch` lives inside `compute_sscd`, not at
  module top. Importing `yt_uniquifier.core.qa.sscd` is free.
* **Uniform timeline sampling**: midpoint seeks cover the complete source
  timeline without decoding every preceding frame of a multi-hour file.
  Samples are resized to 288×288 and receive the upstream ImageNet
  mean/std normalization before inference.
* **Pair-wise cosine**: the model already emits L2-normalised vectors;
  the explicit `clamp([-1, 1])` is a safety net against float drift.
* **Plugin layer untouched**: SSCD lives entirely in `core/qa/`, so a
  third-party transform plugin never has to know it exists.

See also:

* [`docs/qa_report.md`](qa_report.md) — JSON/HTML schema for the QA artifact
* [`docs/calibrate.md`](calibrate.md) — bisection loop semantics
* [`docs/profiles.md`](profiles.md) — profile schema (no SSCD fields; metric is per-run)
