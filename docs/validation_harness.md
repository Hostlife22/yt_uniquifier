# Real-CID validation harness

`qa.json` predictions are **predictions**. The only authoritative test of
whether a profile actually breaks YouTube Content ID is to upload
variants and observe the Studio Copyright tab. This page walks the full
loop.

You'll need:
- a YouTube channel you own
- a source video you own the rights to (or licensed material you have
  the right to re-upload)
- access to YouTube Studio (`studio.youtube.com`)

## The loop

```
generate N variants ──► upload each as Unlisted ──► wait 5–10 min
                                                          │
                                                          ▼
                  record outcomes ◄──── check Studio Copyright tab
                          │
                          ▼
                ingest into correlation report
                          │
                          ▼
              decide: is qa.json predictive?
```

## Step 1 — generate variants

```bash
python tools/generate_variants.py /Users/admin/movies/master.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out-dir /tmp/cid_test_2026-06-01 \
  --n 10 \
  --encoder libx264
```

Produces:

```
/tmp/cid_test_2026-06-01/
├── variant_001.mp4              (~1.2 GB per file at 1080p/2h)
├── variant_001.mp4.qa.json
├── variant_001.mp4.qa.html
├── variant_002.mp4
├── variant_002.mp4.qa.json
├── ...
├── variant_010.mp4
├── work/                        (scratch — delete after)
└── manifest.json                ◄── all 10 variants' KPIs in one place
```

`manifest.json` looks like:

```json
{
  "source": "/Users/admin/movies/master.mp4",
  "profile": "src/yt_uniquifier/profiles/cid_aware.yaml",
  "encoder": "libx264",
  "n_variants": 10,
  "variants": [
    {
      "variant_id": "variant_001",
      "output_path": "/tmp/cid_test_2026-06-01/variant_001.mp4",
      "qa_json_path": "...qa.json",
      "run_seed": 2783492018,
      "plan_hash": "a8f3b2c1d4e5f607",
      "cid_predict_self": 0.18,
      "audio_fp_hamming_per_frame": 17.2,
      "phash_worst_chunk": 0.74,
      "vmaf_mean": 87.1
    },
    ...
  ]
}
```

## Step 2 — upload protocol

1. Open YouTube Studio → Create → Upload videos.
2. Drag-drop all 10 `variant_*.mp4` at once (Studio uploads in parallel).
3. For **each** variant set:
   - **Title**: `cid_test_2026-06-01_001` (date + index — generic, won't
     attract clicks). Nobody should see these.
   - **Visibility**: **Unlisted** ◄── critical
   - "Made for kids": No
   - Monetization: off
   - Do NOT click "I declare original content" — leave the default.
4. Publish.
5. Note each video's URL — the 11-char ID after `?v=` is what you'll
   record (e.g. `dQw4w9WgXcQ`).

**Use a throwaway channel** if uploading owned content to your main
account creates a paper trail you don't want.

## Step 3 — observe outcomes

Wait 5–10 minutes. CID usually scans 2-hour content within ~3 minutes;
budget a buffer.

In Studio → **Content** tab, the **Restrictions** column shows:

| What you see | `match_status` value |
|---|---|
| Empty / dash | `no_match` ✅ |
| 🟡 "Copyright" → click → "Eligibility" | `match` |
| 🔴 "Removed" | `match` + auto-takedown |
| Empty but "Processing" persists past 1h | `pending` (record, recheck later) |
| Visible error in Studio | `error` (excluded from correlation) |

If `match`: click into the entry. Studio shows:
- **Matched against**: name of reference work (will be your own previous
  upload if testing self-match).
- **Claim type**: `monetize` / `block` / `track`.

## Step 4 — record outcomes in CSV

Open `tools/validation_log.csv`. Columns:

| Column | Source | Required |
|---|---|---|
| `variant_id` | manifest.json | yes |
| `source_basename` | source filename (basename only) | yes |
| `profile` | profile filename stem | yes |
| `output_path` | manifest.json | yes |
| `run_seed` | manifest.json | yes |
| `plan_hash` | manifest.json | yes |
| `cid_predict_self` | manifest.json (qa.json) | yes |
| `audio_fp_hamming_per_frame` | manifest.json (qa.json) | yes |
| `phash_worst_chunk` | manifest.json (qa.json) | yes |
| `vmaf_mean` | manifest.json (qa.json) | yes |
| `upload_date` | hand-recorded (`YYYY-MM-DD`) | yes |
| `youtube_video_id` | URL after `?v=` (11 chars) | yes |
| `match_status` | one of `no_match` / `match` / `pending` / `removed` / `error` | yes |
| `matched_against` | Studio text (or empty / `self`) | only if `match` |
| `claim_type` | `monetize` / `block` / `track` | only if `match` |
| `notes` | free text | optional |

Example row:

```csv
variant_001,master.mp4,cid_aware,/tmp/cid_test/variant_001.mp4,2783492018,a8f3b2c1d4e5f607,0.18,17.2,0.74,87.1,2026-06-01,dQw4w9WgXcQ,no_match,,,first batch after v0.4.0
```

Delete the seeded example row after your first real entry.

## Step 5 — analyze

```bash
python tools/validation_correlate.py tools/validation_log.csv
```

With ≥ 5 samples you get a Spearman correlation report per predictor:

```
total rows: 10, valid: 10
outcome distribution: {'no_match': 7, 'match': 3}

Spearman correlation with match_status (1.0 = perfect predictor):
  cid_predict_self                     ρ=+0.842   STRONGLY predictive
  phash_worst_chunk                    ρ=+0.756   STRONGLY predictive
  audio_fp_hamming_per_frame           ρ=+0.689   WEAKLY predictive

no_match rate: 70%  (7 / 10 samples)
→ stable; v0.4.2 + v0.4.3 productive for remaining ~40 %.
```

## Interpretation guide

### `cid_predict_self` Spearman ρ

| ρ range | Verdict |
|---|---|
| ≥ 0.7 | **strongly predictive** — calibrate loop works as designed; trust the predictor |
| 0.3 – 0.7 | **weakly predictive** — use as a tie-breaker, not a hard threshold |
| 0.1 – 0.3 | **uncorrelated** — predictor doesn't actually predict real CID. v0.5 research candidate |
| < 0.1 | **anti-correlated** — something broken; we're optimizing in the wrong direction |

### Real-CID no-match rate

| % no_match | Verdict |
|---|---|
| ≥ 80 % | public-OSS frontier reached — focus on docs/UX/packaging |
| 60 – 80 % | stable; Spec 19/20 productive on the remaining ~30 % |
| 40 – 60 % | works half the time; Spec 19 + neural FP research warranted |
| < 40 % | current stack insufficient; v0.5+ neural attack mode required |

## Privacy / TOS reminders

- **Never** automate the upload step — bot-uploads risk TOS strikes.
- **Use a throwaway channel** for testing if you don't want the paper
  trail on your main account.
- The validation harness records **your own content** uploaded to **your
  own channel**. Don't test by uploading someone else's video to see if
  you can defeat their CID claim — that's exactly what the tool's
  "What it is NOT" disclaimer covers.
- `validation_log.csv` is local-only; it's not auto-shared anywhere.

## Quick reference

```bash
# Generate 10 variants
python tools/generate_variants.py source.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out-dir /tmp/cid_test --n 10

# After manual upload + recording outcomes:
python tools/validation_correlate.py tools/validation_log.csv
```
