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

For one (input, output) pair the report evaluates three independent axes:
media correctness, perceptual quality and diagnostic similarity. Optional
metric backends degrade gracefully, but a missing/corrupt media stream is a
correctness failure and produces `INVALID`, never a normal metric verdict.

> **New since v0.8.0**: optional SSCD semantic-similarity scores
> (`yt-uniq qa --sscd`,
> requires `[ml]` extra) and target-VMAF bounded-retry events for
> profiles that opt into `target_vmaf`. See the SSCD + target-VMAF
> subsections below.

### File-level

| Field | Source | Meaning |
|---|---|---|
| `input_md5`, `output_md5` | streaming md5 (4 MB chunks) | exact byte identity (different for any successful run — only matches on `--new-variant=false` resume of the exact same plan) |
| `input_size_bytes`, `output_size_bytes` | filesystem | sanity check on bitrate budget |
| `input_duration_sec`, `output_duration_sec` | ffprobe | should match within ±0.5 s; `duration_match` is the bool |

Before hashes or sampled metrics, QA probes both files. Automatic run/batch/GUI
reports also receive the existing `Plan` and reuse the final media contract for
stream topology, timestamp and HDR/SDR checks. Standalone QA applies conservative
pair checks. The candidate's primary video and all audio streams are then decoded
to EOF with FFmpeg `-xerror -err_detect explode`, so a corrupt unsampled tail becomes
`correctness: full output decode failed` in `notes[]` and the status is `INVALID`.

Independently of report generation, `run_full` performs that complete decode as a
mandatory final publication gate for CLI, GUI, web and distributed workers. This also
applies to `--no-qa`. Automatic post-run reports reuse the successful gate instead of
decoding the same output twice; standalone `yt-uniq qa` performs its own decode.

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

All three fingerprint fields are derived from one fingerprint extraction per file,
instead of six `fpcalc` processes per report. For sources longer than 600 seconds,
five 120-second windows cover the start, middle and tail and are concatenated before
fingerprinting; `notes[]` records that stratified coverage was used.

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

### Plan-registered metrics (v1.5, RFC #12)

Raw source/output metrics above intentionally retain their historical meaning. When
automatic run/batch QA has the exact completed `Plan`, it additionally replays the
existing video filter graph with the restored run seed and per-segment seeds into a
temporary lossless FFV1 reference. The registered VMAF/SSIM comparison resets both
timelines locally before scoring, so an intentional crop, mirror, retiming or
deterministic frame drop is part of the reference rather than misclassified as encode
damage.

| Field | Range | Engineering meaning |
|---|---:|---|
| `vmaf_registered_mean` | 0..100 or null | Encode/generational quality against the transformed reference |
| `ssim_registered_mean` | -1..1 or null | Structural quality against the transformed reference |
| `sscd_registered_mean` | -1..1 or null | Bounded monotonic representation similarity; output frames cannot be reused |
| `audio_fp_registered_hamming_per_frame` | 0..32 or null | Ordered Chromaprint distance after bounded global offset and linear drift alignment |
| `registration.reference_mode` | `plan_transformed` | Declares that the reference came from exact Plan replay |
| `registration.plan_hash`, `run_seed` | provenance | Binds the report to the completed plan and stochastic realization |
| `registration.video`, `.audio` | object or null | Offset/drift, compared samples, coverage, confidence and availability note |

These fields remain diagnostics: the verdict continues to use correctness and the
unchanged raw-quality policy until a licensed natural-content corpus establishes
registered thresholds. Low-overlap audio/SSCD alignment is rejected instead of
returning a deceptively good value. Registered VMAF fails closed for preserved HDR
instead of interpreting PQ/HLG code values with an SDR model; HDR→SDR output can be
scored after the explicit tonemap because both registered inputs are then SDR.

For example, the extended 4K natural-content experiment produced raw VMAF 3.73
and plan-registered VMAF 93.81 in the same cell. The first includes the difference
from the original scene/timeline; the second compares encoding against the
transformed reference. Neither number alone authorizes a production release.
The corpus runner records measurement completeness separately from QA verdict
and human acceptance; its `measured` status is not a quality pass.

Explicit public correctness/loudness objects and independent opt-in thresholds
are proposed in `specs/28-qa-correctness-loudness-rfc.md`, not yet accepted or
implemented. Existing report/CLI contracts remain unchanged.

Reference generation is cancellable and guarded by both free space and
`YT_UNIQ_REGISTERED_REFERENCE_MAX_BYTES` (40 GiB by default). If the conservative
FFV1 estimate exceeds that budget, registered video metrics become unavailable with
an explicit `notes[]` entry; raw QA continues. Provision more temporary space and set
the variable deliberately for long-form runs. SSCD reference embeddings are cached
under `YT_UNIQ_QA_CACHE_DIR` or the per-user QA cache using source content, canonical
profile, plan/seed, FFmpeg/tool/model version, sampling grid and encoded-reference
digest; corrupt entries are rebuilt atomically.

### SSCD semantic similarity (v0.8.0 R4, opt-in)

Populated only when `yt-uniq qa --sscd` is passed. Requires the `[ml]` extra (torch +
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

### Notes + assessment

| Field | Meaning |
|---|---|
| `notes[]` | warnings for unavailable backends (e.g. "fpcalc not in PATH") |
| `duration_match` | bool — input/output durations within ±0.5 s |

The CLI and HTML show independent axes:

- **Correctness** — `VALID`, `INVALID` or `NOT_VERIFIED`. Topology, duration, first video PTS,
  declared color/HDR contract and complete decode take precedence.
- **Quality** — `PASS`, `WARNING`, `FAIL` or `UNAVAILABLE`, based on VMAF/SSIM.
- **Visual similarity** — `LOW`, `MODERATE`, `HIGH` or `UNAVAILABLE`, based on
  sampled pHash and explicitly diagnostic.

The overall output status is `INVALID` on a correctness failure, otherwise
green/yellow/red follows quality evidence only. Similarity never compensates for bad
quality and never turns a correct, high-quality output into a failure. The existing
`QAReport` JSON schema remains additive-compatible; legacy `notes[]` remain present,
and v1.6.0 adds structured evidence under [RFC #21](https://github.com/Hostlife22/yt_uniquifier/issues/21).

## Explicit evidence and optional gates (v1.6.0)

- `correctness`: `status` (`passed`, `failed`, `not_verified`), `scope`
  (`plan_contract` or `pair_contract`), `failure_codes`, `full_decode_status`, `note`.
  Passing means only the declared contract and full decode were checked. It does
  **not** certify internal lip-sync, audible transients or visual quality. Without a
  Plan, only the conservative pair contract is available. Legacy `duration_match`
  still compares source/output duration; accepted speed changes use the Plan's
  expected duration for structured correctness and verdict instead.
- `loudness`: measurement `status`, `streams` and `note`. Each output audio stream
  reports its absolute `stream_index`, `integrated_lufs`, `true_peak_dbtp`, `method`,
  measurement `status` and `note`. No downmix or normalization is applied to the
  measured signal. Negative-infinite silence/sub-gating measurements become null;
  invalid NaN/+infinite results are not marked verified. `passed` means the scan
  completed, **not** that a loudness target, clipping/phase or listening test passed.
  Default: `not_verified` (scan not requested); use `--loudness` for a full scan of
  every output track. This can take substantial time on long films.
- `quality_policy`: optional `domain` (`raw` or `registered`), `min_vmaf` (0–100)
  and `min_ssim` (-1–1). Null means the old heuristic bands remain active. When
  minimums are explicitly requested, those minimums in the selected domain drive
  the quality axis; **all** requested metrics must be available, finite and pass.
  Registered gates also require usable video-registration evidence, not merely a
  numeric value. They do not establish a universally calibrated quality target.

Example only — these are operator-selected thresholds, not production defaults:

```bash
yt-uniq qa source.mkv output.mp4 --plan-json plan.json --quality-domain registered \
  --min-vmaf 90 --min-ssim 0.98 --loudness
```

Explicit gate failure or unverified correctness writes JSON/HTML and exits **2**.
Invalid combinations (such as `--fast-qa --min-vmaf 90`, `--no-ssim --min-ssim .9`,
or registered gates without a Plan) fail before expensive metrics. Raw VMAF gates
reject HDR pairs: `phone_model=0` does not make an SDR VMAF model HDR-valid.
Registered VMAF remains unavailable for preserved HDR. For authorized HDR→SDR,
compare within the plan-transformed SDR domain, then perform human visual review.

No new thresholds or loudness scans are enabled by default; old CLI invocations
retain their exit behavior. Valid VMAF 0/0.5 is now retained, so the old heuristic
verdict may correctly become worse instead of silently falling back to SSIM.
Old JSON loads with nullable evidence fields unset. Consumers using their own
strict schemas must update them for the new optional fields.

`RunSummary.decode_evidence` is process-local and checked against file identity
(device/inode/size/mtime). Run/batch/GUI reuse it only for matching output; stale
tokens force a fresh full decode. It is not cryptographic proof and must never be
loaded from untrusted JSON or stored as a resume cache. Output changes during QA
produce `output.changed_during_qa`, not a successful result.

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

Standalone QA never guesses transforms, seeds or segmentation. To request registered
metrics, serialize the exact completed `RunSummary.plan` and supply the same segment
target that produced the output:

```python
Path("completed-plan.json").write_text(summary.plan.model_dump_json(indent=2))
```

```bash
yt-uniq qa master.mp4 candidate.mp4 \
  --plan-json completed-plan.json \
  --registration-segment-sec 600
```

Without `--plan-json`, all registered fields remain null and raw metrics are unchanged.

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
