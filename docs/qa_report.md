# QA report

Every `yt-uniq run` (unless `--no-qa`) emits two artefacts next to the
output file:

```
output.mp4
output.mp4.qa.json    # machine-readable
output.mp4.qa.html    # human-readable, with heatmaps + verdict banner
```

`yt-uniq qa <input> <output>` produces the same pair for an existing
input/output pair without re-encoding.

## What's measured

For one (input, output) pair the report aggregates seven independent
metric families. Each one is optional and degrades gracefully if its
backing binary is missing — the QA report just notes which ones it
couldn't compute.

### File-level

| Field | Source | Meaning |
|---|---|---|
| `input_md5`, `output_md5` | streaming md5 (4 MB chunks) | exact byte identity (different for any successful run — only matches on `--new-variant=false` resume of the exact same plan) |
| `input_size_bytes`, `output_size_bytes` | filesystem | sanity check on bitrate budget |
| `input_duration_sec`, `output_duration_sec` | ffprobe | should match within ±0.5 s; `duration_match` is the bool |

### Visual similarity

| Field | Source | Range | Meaning |
|---|---|---|---|
| `phash_samples` | — | typically 120 | how many frames were sampled |
| `phash_distance_min`, `_mean`, `_max` | imagehash.phash on `samples` frames | 0..64 bits | Hamming distance between paired frames |
| `phash_similarity` | `1 - mean_distance / 64` | 0..1 | aggregate; higher = closer to source |
| `vmaf_mean` | ffmpeg `libvmaf` | 0..100 | perceptual quality vs source; null if libvmaf missing |
| `ssim_mean` | ffmpeg `ssim` | 0..1 | structural similarity index; null if disabled |

### Audio similarity

| Field | Source | Range | Meaning |
|---|---|---|---|
| `audio_fp_similarity` | chromaprint Jaccard over uint32 sub-fingerprints | 0..1 | quick "do the fingerprint sets overlap?" check |
| **`audio_fp_hamming_per_frame`** | chromaprint XOR + popcount, paired frames, mean | 0..32 bits | bit-level distance; **the canonical CID-divergence audio KPI** |
| **`audio_fp_match_confidence`** | `1 - hamming_per_frame / 32` | 0..1 | normalized; lower = better for divergence |

The two Hamming fields are the **explicit CID-divergence audio KPI**
introduced in v0.3.3 (Spec 16). Heuristic interpretation per chromaprint
literature:

| `hamming_per_frame` (bits) | Interpretation |
|---|---|
| ≤ 5  | high-confidence match — CID will hit |
| 6–14 | match |
| 15–25 | uncertain |
| ≥ 26 | no match |
| ≥ 30 | high-confidence non-match |

### Content-ID prediction (v0.2+)

| Field | Source | Meaning |
|---|---|---|
| `cid_predict_self` | weighted (visual + audio) Jaccard over 4-second chunks | 0..1; predicted self-match probability |
| `weakest_chunk_sec` | argmax over `chunk_similarities[].combined` | (start_sec, end_sec) of the chunk most similar to source |
| `chunk_similarities[]` | per 4-sec chunk: `{start_sec, end_sec, visual, audio, combined}` | drives the HTML heatmap |
| `corpus_matches[]` | comparison against `yt-uniq corpus` entries | `{id, path, visual, audio, combined}` for files above threshold |

`cid_predict_self` is a **predictor**, not a guarantee. Real Content ID is
a black box; we model it as the convex combination of the chunk-level
visual and audio similarities, weighted to match published behaviour.

### Notes + verdict

| Field | Meaning |
|---|---|
| `notes[]` | warnings for unavailable backends (e.g. "fpcalc not in PATH") |
| `duration_match` | bool — input/output durations within ±0.5 s |

The HTML report shows a banner colour-coded **green / yellow / red** based
on a rule of thumb:

- **green**: phash similarity in (0.50, 0.85] and VMAF ≥ 85 and SSIM ≥ 0.90
- **yellow**: phash in (0.85, 0.97] or VMAF in (75, 85) or SSIM < 0.90
- **red**: phash > 0.97 (barely unique) or phash < 0.50 (unrecognisable)
       or VMAF < 75

## KPI targets

These are the **targets** post-v0.3.3 for owned-content CID divergence
(profile `cid_aware`). Actual values vary by source — synthetic test
patterns score very differently from natural footage.

| KPI | Target | Notes |
|---|---|---|
| `phash_similarity` (mean) | `< 0.75` | lower is more visually distinct |
| `phash_similarity` (worst 4-sec chunk) | `< 0.80` | matters more than the mean — CID locks on the closest chunk |
| `vmaf_mean` | `≥ 85` | trade-off floor; below this is noticeably degraded |
| `ssim_mean` | `≥ 0.90` | optional sanity check |
| `audio_fp_hamming_per_frame` | `≥ 15 bits` | "uncertain match" zone or better |
| `audio_fp_match_confidence` | `≤ 0.55` | mirror of the above |
| `cid_predict_self` | `< 0.20` | predicted self-match probability |

`cid_aggressive` typically pushes pHash down to ~0.60 and Hamming up to
~22 bits, at the cost of VMAF ≈ 78–82 and audible noise overlay.

## Reading the heatmap

The HTML report renders one cell per 4-sec chunk, coloured by
`combined = α·visual + (1-α)·audio`:

```
[█][█][░][▒][█][▒][░][░][█][▒]  ← time →
 green = unique     red = similar to source
```

The **weakest chunk** (red-most) is the one most at risk of matching.
Common patterns:

- A single red chunk near the start → likely a logo / title card where
  random transforms barely move things. Calibrate or trim the leading
  sequence.
- A red **band** in the middle → static talking-head section; bump up
  `video.crop_resize.max_strength` and `video.temporal_jitter.blackout_prob`.
- All cells in the green zone → done; profile is well-calibrated for this
  source.

## Standalone QA (no encode)

```bash
yt-uniq qa /path/to/master.mp4 /path/to/candidate.mp4 --vs-corpus
# writes candidate.mp4.qa.json + candidate.mp4.qa.html
```

`--vs-corpus` adds the `corpus_matches` section so you can verify the
candidate isn't too similar to a previously-uploaded variant.

## Fast QA

For batch workflows where you don't need VMAF (slow):

```bash
yt-uniq run … --fast-qa
# - skips VMAF (the slowest stage)
# - halves the phash sample count
```

JSON shape is identical, with `vmaf_mean = null` and `phash_samples ≈ 60`.

## Programmatic access

```python
import json
from pathlib import Path

qa = json.loads(Path("out.mp4.qa.json").read_text())

# Audio CID divergence KPI:
hp = qa.get("audio_fp_hamming_per_frame")
if hp is not None and hp < 15:
    print(f"Audio FP too close: {hp:.1f} bits/frame (want ≥ 15)")

# Worst chunk:
worst = max(c["combined"] for c in qa["chunk_similarities"])
print(f"worst chunk combined similarity: {worst:.3f}")
```

The Pydantic model is `yt_uniquifier.core.models.QAReport` if you'd
rather work with typed objects.
