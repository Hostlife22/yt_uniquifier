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

> **New since v0.8.0**: optional SSCD semantic-similarity scores
> (`yt-uniq run --metric sscd` or `yt-uniq qa --metric sscd`,
> requires `[ml]` extra) and target-VMAF bounded-retry events for
> profiles that opt into `target_vmaf`. See the SSCD + target-VMAF
> subsections below.

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
| `audio_fp_similarity` | chromaprint Jaccard over uint32 sub-fingerprints | 0..1 | **strict set-equality** check; see warning below |
| **`audio_fp_hamming_per_frame`** | chromaprint XOR + popcount, paired frames, mean | 0..32 bits | internal bit-level distance diagnostic |
| **`audio_fp_match_confidence`** | `1 - hamming_per_frame / 32` | 0..1 | legacy normalized similarity heuristic; not calibrated confidence |

> **About `audio_fp_similarity`.** This is Jaccard
> (`|A ∩ B| / |A ∪ B|`) over the 32-bit chromaprint sub-fingerprint
> *sets*. Chromaprint deliberately flips bits across the entire 32-bit
> code on small acoustic changes (≈1 dB loudnorm shift alone) so two
> 32-bit codes are exact-equal only when the audio is byte-identical.
> In practice **this field reads 0.0 for every yt-uniq output**, even
> on the softest profile — the audio is perfectly recognisable, the
> codes simply don't survive bit-exact match. Don't read it as "audio
> destroyed". The metric that reflects perceived similarity is
> `audio_fp_match_confidence` (Hamming-based, normalised). The
> `_similarity` field is retained for schema compatibility with
> downstream tools that already key on it.

The Hamming fields were introduced in v0.3.3. Their legacy names are retained for
schema compatibility, but they are not a rights-system KPI or probability. Interpret
them only relative to a pinned source/corpus and tool version:

| `hamming_per_frame` (bits) | Interpretation |
|---|---|
| lower value | paired Chromaprint codes are closer for this experiment |
| middle value | result needs listening and timeline/alignment checks |
| higher value | paired codes differ more; this says nothing about audibility or an external system |

### SSCD semantic similarity (v0.8.0 R4, opt-in)

Populated only when `yt-uniq run --metric sscd` (or `yt-uniq qa
--metric sscd`) is passed. Requires the `[ml]` extra (torch +
transformers). The first run downloads ~200 MB of model weights to
`~/.cache/yt_uniquifier/models/`; subsequent runs use the cache.
Full background in [`docs/sscd.md`](./sscd.md).

| Field | Source | Range | Meaning |
|---|---|---|---|
| `sscd.mean_similarity` | mean cosine similarity over N-frame embedding pairs | 0..1 | aggregate semantic match |
| `sscd.min_similarity`  | min cosine similarity (weakest paired frame) | 0..1 | worst case — the chunk most likely to fail human review |
| `sscd.per_frame[]`     | `{frame_idx, similarity}` for each sampled pair | 0..1 | drives the SSCD heatmap in the HTML report |
| `sscd.band`            | derived bucket: `high` ≥0.85, `medium` 0.65-0.85, `low` <0.65 | enum | colour-coded in HTML |

Unlike pHash (pixel-level), SSCD reflects what a content-aware human
or model would see: a recoloured + cropped + slightly-noisy clip can
score 0.92 on SSCD while pHash similarity drops below 0.50.

### Target-VMAF retry events (v0.8.0 R5)

Profiles that set `target_vmaf` trigger an in-flight retry when a segment lands below
the target. These are live `RunEvent` records consumed by CLI/GUI callers; they are
not currently embedded in `QAReport` JSON:

| Field | Meaning |
|---|---|
| `target_vmaf.payload.segment` | which segment was scored |
| `target_vmaf.payload.attempt` | zero-based encode attempt (`0` is the initial encode) |
| `target_vmaf.payload.vmaf` | VMAF actually measured, or null if scoring failed |
| `target_vmaf.payload.crf` | software-equivalent quality hint for the attempt |
| `target_vmaf.payload.target` | configured target |
| `target_vmaf_failed.payload.best_attempt` | zero-based attempt retained on disk |

`target_vmaf_max_retries` caps the loop. After exhaustion, the segmenter keeps the
highest-scoring encoded candidate and emits `target_vmaf_failed`.

The feedback scorer currently uses a plain source slice. Preflight rejects
`target_vmaf` with geometry, retiming, mirroring, overlays, subtitles or tonemapping
because that pair is not registered and CRF retries cannot make the score converge.
Use the loop only on a registered encode-quality path.

### Legacy self-similarity heuristic (v0.2+)

| Field | Source | Meaning |
|---|---|---|
| `cid_predict_self` | weighted (visual + audio) Jaccard over 4-second chunks | 0..1; internal self-similarity heuristic (legacy field name) |
| `weakest_chunk_sec` | argmax over `chunk_similarities[].combined` | (start_sec, end_sec) of the chunk most similar to source |
| `chunk_similarities[]` | per 4-sec chunk: `{start_sec, end_sec, visual, audio, combined}` | drives the HTML heatmap |
| `corpus_matches[]` | comparison against `yt-uniq corpus` entries | `{id, path, visual, audio, combined}` for files above threshold |

`cid_predict_self` is neither a probability nor a predictor of YouTube Content ID.
It is a project-specific convex combination useful for regression comparisons and
self-collision diagnostics on owned or licensed derivatives. Do not use it as a
production pass/fail gate without corpus-specific validation.

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

This is a legacy UI heuristic, not a production acceptance verdict: it mixes
similarity and quality and uses raw, potentially unregistered metrics. The mandatory
media contract remains authoritative; Phase 5 will separate the banner into
correctness, registered quality and diagnostic similarity states.

## Acceptance targets

The project does not publish universal thresholds for pHash, Chromaprint, SSCD or
the legacy `cid_predict_self` field. They vary with sampling, alignment and content,
and they do not measure perceptual quality. Production acceptance starts with media
correctness, then uses registered VMAF/SSIM/PSNR and audio LUFS/true peak against a
licensed natural-content corpus. Similarity fields remain separate diagnostics.

## Reading the heatmap

The HTML report renders one cell per 4-sec chunk, coloured by
`combined = α·visual + (1-α)·audio`:

```
[█][█][░][▒][█][▒][░][░][█][▒]  ← time →
 green = lower heuristic similarity     red = higher heuristic similarity
```

The legacy **weakest chunk** field points to the highest-similarity sample. Common
diagnostic patterns:

- A single high-similarity chunk near the start often corresponds to a logo/title
  card and should be checked for correct temporal pairing.
- A continuous band can indicate static content or a sampling/alignment issue.
- Low similarity does not mean good quality; inspect registered quality metrics and
  the rendered media independently.

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

# Internal audio fingerprint diagnostic:
hp = qa.get("audio_fp_hamming_per_frame")
if hp is not None:
    print(f"Audio FP distance: {hp:.1f} bits/frame")

# Worst chunk:
worst = max(c["combined"] for c in qa["chunk_similarities"])
print(f"worst chunk combined similarity: {worst:.3f}")
```

The Pydantic model is `yt_uniquifier.core.models.QAReport` if you'd
rather work with typed objects.
