# Spec 18 — Real-CID validation harness (v0.4.1)

> **Phase 18 (v0.4.1)** · 0.5 day · **Deps:** v0.4.0

## Context

The v0.3.3 audit named this as the **single highest-leverage gap** in the
roadmap. Every metric in `qa.json` — `cid_predict_self`,
`audio_fp_hamming_per_frame`, pHash worst chunk — is a prediction from our
own heuristic against our own model. `yt-uniq calibrate` bisects against
the same heuristic. It is mathematically possible for all of these numbers
to look great while real YouTube Content ID matches every variant.

We cannot automate this. YouTube provides no public CID API. The only
authoritative test is: upload an unlisted variant to your own channel,
wait ~5–10 minutes for the CID scan to complete, check YouTube Studio's
Copyright tab, record the outcome.

This phase builds the **manual workflow** for that loop:

1. A CLI helper that generates N variants of one source, with a
   reproducible manifest.
2. A documented step-by-step protocol for the upload/observe/record cycle.
3. A `tools/validation_log.csv` schema so multiple runs accumulate into a
   queryable record.
4. A small script that ingests the CSV and computes correlation between
   `cid_predict_self` and real outcomes — so after enough samples we can
   tell whether our predictor is actually predictive.

No production code changes. Pure tooling and docs. Once even **5 samples**
are logged, we'll know more about whether v0.4.0 actually works than all
previous releases combined.

## Goal

After v0.4.1:

- `tools/generate_variants.py` produces N variants of a source with a
  manifest tying each output filename to the run_seed, plan_hash, and
  `qa.json` location.
- `tools/validation_log.csv` schema documents how to record real-upload
  outcomes alongside the manifest.
- `docs/validation_harness.md` walks a user through the full loop, from
  generate → upload → observe → record → ingest.
- `tools/validation_correlate.py` reads the CSV and produces a Spearman
  correlation report between `cid_predict_self` and real `match_status`.
- Tag: `v0.4.1`.

## Scope

**In:**

- `tools/generate_variants.py` (new) — generate N variants from one source
  + write `manifest.json`.
- `tools/validation_log.csv` — file with header row, sample row.
- `tools/validation_correlate.py` (new) — ingest CSV, print correlation
  report.
- `docs/validation_harness.md` (new) — manual workflow walkthrough.
- README cross-link.

**Not in:**

- Any change to production code paths.
- Any change to profiles, transforms, QA report.
- Automated YouTube upload via `youtube-dl` reverse / browser automation —
  TOS risk, fragility, and the actual CID flag visibility is in YouTube
  Studio UI, not in any API we can scrape.
- A "Linear regression model that predicts CID from qa.json features" —
  needs N ≥ 30 samples before any modeling is justifiable; v0.5+
  candidate.

## Workitem 1 — `tools/generate_variants.py`

**File:** `tools/generate_variants.py` (new)

```python
#!/usr/bin/env python3
"""Generate N reproducible variants of one source for manual CID upload tests.

Each variant rolls a fresh run_seed (per_run / divergent strategy semantics).
Output dir contains:
  variant_001.mp4
  variant_001.mp4.qa.json
  variant_002.mp4
  variant_002.mp4.qa.json
  ...
  manifest.json  — per-variant: filename, run_seed, plan_hash, predicted
                                cid_self_match, audio_fp_hamming, phash_worst

Usage:
  python tools/generate_variants.py /path/to/master.mp4 \\
    --profile src/yt_uniquifier/profiles/cid_aware.yaml \\
    --out-dir /tmp/cid_test \\
    --n 10 \\
    --encoder libx264
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile
from yt_uniquifier.core.qa.report import build_report, write_json


@dataclass
class VariantRecord:
    variant_id: str
    output_path: str
    qa_json_path: str
    run_seed: int
    plan_hash: str
    cid_predict_self: float | None
    audio_fp_hamming_per_frame: float | None
    phash_worst_chunk: float | None
    vmaf_mean: float | None


def generate_one(
    source: Path, profile_path: Path, out_dir: Path, idx: int,
    encoder: str | None,
) -> VariantRecord:
    profile = load_profile(profile_path)
    plan = build_plan(source, profile, encoder_override=encoder)
    output = out_dir / f"variant_{idx:03d}.mp4"
    work_dir = out_dir / "work" / f"variant_{idx:03d}"

    run_full(plan, RunOptions(
        work_dir=work_dir, output=output,
        target_segment_sec=600.0,
        enforce_preflight=True,  # we want real failures to surface
    ))

    # QA report is auto-produced by run_full; load it.
    qa_path = output.with_suffix(".mp4.qa.json")
    qa = json.loads(qa_path.read_text())

    chunks = qa.get("chunk_similarities") or []
    worst = max((c.get("combined", 0.0) for c in chunks), default=None)

    return VariantRecord(
        variant_id=f"variant_{idx:03d}",
        output_path=str(output),
        qa_json_path=str(qa_path),
        run_seed=plan.run_seed,
        plan_hash=plan.plan_hash,
        cid_predict_self=qa.get("cid_predict_self"),
        audio_fp_hamming_per_frame=qa.get("audio_fp_hamming_per_frame"),
        phash_worst_chunk=worst,
        vmaf_mean=qa.get("vmaf_mean"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--encoder", type=str, default=None)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    records: list[VariantRecord] = []
    for i in range(1, args.n + 1):
        print(f"[{i}/{args.n}] generating variant_{i:03d}…", file=sys.stderr)
        rec = generate_one(args.source, args.profile, args.out_dir, i, args.encoder)
        records.append(rec)
        print(
            f"  seed={rec.run_seed} cid_predict={rec.cid_predict_self} "
            f"audio_hamming={rec.audio_fp_hamming_per_frame} "
            f"phash_worst={rec.phash_worst_chunk} vmaf={rec.vmaf_mean}",
            file=sys.stderr,
        )

    manifest = {
        "source": str(args.source),
        "profile": str(args.profile),
        "encoder": args.encoder,
        "n_variants": args.n,
        "variants": [asdict(r) for r in records],
    }
    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"manifest: {manifest_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Run-mode invariants:**

- Uses `per_run` / `divergent` seed strategy from the profile — no override.
- Forces `enforce_preflight=True` so failed preflights aren't silently
  suppressed (we want to know if a missing ffmpeg filter would have caused
  a real failure).
- Each variant gets its own `work_dir` subdir so resume / state.json don't
  cross-contaminate.

## Workitem 2 — `tools/validation_log.csv` schema

**File:** `tools/validation_log.csv` (new — header row + 1 example)

```csv
variant_id,source_basename,profile,output_path,run_seed,plan_hash,cid_predict_self,audio_fp_hamming_per_frame,phash_worst_chunk,vmaf_mean,upload_date,youtube_video_id,match_status,matched_against,claim_type,notes
variant_001,master.mp4,cid_aware,/tmp/cid_test/variant_001.mp4,2783492018,abcd1234,0.18,17.2,0.74,87.1,2026-06-01,EXAMPLE_VIDEO_ID,no_match,,,first run after v0.4.0
```

**Column definitions** (documented in `docs/validation_harness.md`):

| Column | Source | Required |
|---|---|---|
| `variant_id` | generate_variants.py | yes |
| `source_basename` | manifest | yes |
| `profile` | manifest | yes |
| `output_path` | manifest | yes |
| `run_seed` | manifest | yes |
| `plan_hash` | manifest | yes |
| `cid_predict_self` | manifest (qa.json) | yes |
| `audio_fp_hamming_per_frame` | manifest (qa.json) | yes |
| `phash_worst_chunk` | manifest (qa.json) | yes |
| `vmaf_mean` | manifest (qa.json) | yes |
| `upload_date` | hand-recorded (YYYY-MM-DD) | yes |
| `youtube_video_id` | from upload URL (11-char ID after `v=`) | yes |
| `match_status` | one of: `no_match`, `match`, `pending`, `removed`, `error` | yes |
| `matched_against` | from Studio (rights-holder name or "self") | only if `match=match` |
| `claim_type` | one of: `monetize`, `block`, `track`, "" | only if `match=match` |
| `notes` | free text | optional |

## Workitem 3 — `tools/validation_correlate.py`

**File:** `tools/validation_correlate.py` (new)

```python
#!/usr/bin/env python3
"""Read validation_log.csv and report whether qa.json predictors actually
predict real CID outcomes.

Spearman rank correlation between cid_predict_self and an ordinal
match_status encoding. With ≥ 10 samples, prints a small report:
  - sample count per outcome bucket
  - correlation coefficient (and bootstrap CI if N ≥ 20)
  - separator threshold (cid_predict value above which match becomes
    more likely than not)
  - regression suggestion: "current predictor is anti-correlated /
    uncorrelated / weakly predictive / strongly predictive"

Pure stdlib + manual rank computation — no scipy / pandas dependency, so
the tool runs anywhere yt-uniq is installed.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

# Ordinal encoding for match_status: bigger = "more matched".
STATUS_RANK = {
    "no_match": 0, "pending": 1, "match": 2, "removed": 3, "error": -1,
}


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation. Returns 0 if input is degenerate."""
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0

    def ranks(values: list[float]) -> list[float]:
        sorted_idx = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        for rank, i in enumerate(sorted_idx):
            result[i] = float(rank)
        return result

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den_x = (sum((rx[i] - mx) ** 2 for i in range(n))) ** 0.5
    den_y = (sum((ry[i] - my) ** 2 for i in range(n))) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, help="validation_log.csv")
    args = ap.parse_args()

    with args.log.open() as f:
        rows = list(csv.DictReader(f))

    valid = [
        r for r in rows
        if r["match_status"] in STATUS_RANK and r["match_status"] != "error"
        and r["cid_predict_self"]
    ]
    print(f"total rows: {len(rows)}, valid: {len(valid)}")

    counts: dict[str, int] = {}
    for r in valid:
        counts[r["match_status"]] = counts.get(r["match_status"], 0) + 1
    print(f"outcome distribution: {counts}")

    if len(valid) < 5:
        print("\nNot enough samples for correlation (need ≥ 5). Keep uploading.")
        return 0

    predictors = {
        "cid_predict_self": [float(r["cid_predict_self"]) for r in valid],
        "phash_worst_chunk": [float(r["phash_worst_chunk"]) for r in valid],
        "audio_fp_hamming_per_frame": [
            -float(r["audio_fp_hamming_per_frame"]) for r in valid
        ],  # invert: lower Hamming = more match-likely
    }
    outcome_ranks = [STATUS_RANK[r["match_status"]] for r in valid]

    print("\nSpearman correlation with match_status (1.0 = perfect predictor):")
    for name, xs in predictors.items():
        rho = spearman(xs, outcome_ranks)
        verdict = (
            "STRONGLY predictive"  if abs(rho) >= 0.7  else
            "WEAKLY predictive"    if abs(rho) >= 0.3  else
            "uncorrelated"         if abs(rho) < 0.1   else
            "weakly anti-correlated" if rho < 0       else
            "weakly correlated"
        )
        print(f"  {name:35s}  ρ={rho:+.3f}   {verdict}")

    no_match = [r for r in valid if r["match_status"] == "no_match"]
    matched = [r for r in valid if r["match_status"] == "match"]
    if no_match and matched:
        print(
            f"\nno_match rate: {len(no_match)/len(valid):.0%}  "
            f"({len(no_match)} / {len(valid)} samples)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Workitem 4 — `docs/validation_harness.md`

**File:** `docs/validation_harness.md` (new)

```markdown
# Real-CID validation harness

`qa.json` predictions are **predictions**. The only authoritative test of
whether a profile actually breaks YouTube Content ID is to upload variants
and observe the Studio Copyright tab. This page walks the full loop.

You'll need: a YouTube channel you own, a source video you own the rights
to (or licensed material you have the right to re-upload), and access to
YouTube Studio.

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

(... [continues with step-by-step walkthrough — see file for full content])
```

The full content of `docs/validation_harness.md` covers:

1. **Setup** — generate variants with `tools/generate_variants.py`
2. **Upload protocol** — Studio settings (Unlisted, no monetization, no
   `made for kids`, no commercial use flags), naming convention, batch
   upload via Studio's Upload page (drag N files).
3. **Observation** — after ~5 min check **YouTube Studio → Content →
   Copyright** column. Each variant shows:
   - empty / dash → no_match
   - "Eligibility" flag → match (click to see which reference)
   - "Removed" → match + automatic takedown
4. **Recording** — append a row to `tools/validation_log.csv` per
   variant. Columns documented in this spec.
5. **Correlation** — `python tools/validation_correlate.py
   tools/validation_log.csv` after every batch of ≥ 5 new samples.
6. **Interpretation guide:**
   - If `cid_predict_self` is **strongly predictive** (ρ ≥ 0.7): our
     model is right; calibrate is meaningful.
   - If **weakly predictive** (0.3–0.7): use cid_predict as a tie-breaker
     among candidates, not a hard threshold.
   - If **uncorrelated** (ρ < 0.3): our predictor isn't actually
     predicting CID. Go back to research; v0.5 candidates.
   - If **anti-correlated** (ρ < 0): something is badly wrong — we're
     optimizing for the opposite of what works.
7. **Privacy / TOS reminders** — never automate the upload step (TOS
   risk); use a throwaway channel for testing if uploading owned content
   to your real account creates a paper trail you don't want.

## Workitem 5 — README cross-link

**File:** `README.md` — add to the "Project docs" list:

```markdown
- [Real-CID validation harness](./docs/validation_harness.md) — manual
  upload-observe-record loop to validate predictor accuracy
```

## Acceptance

```bash
# 1. generate_variants.py runs end-to-end on a short fixture.
python tools/generate_variants.py tests/fixtures/results/source_30s.mp4 \
  --profile src/yt_uniquifier/profiles/cid_aware.yaml \
  --out-dir /tmp/v041_smoke --n 3 --encoder libx264
test -f /tmp/v041_smoke/variant_001.mp4
test -f /tmp/v041_smoke/manifest.json
python -c "
import json
m = json.load(open('/tmp/v041_smoke/manifest.json'))
assert len(m['variants']) == 3
assert all(v['run_seed'] != m['variants'][0]['run_seed']
           for v in m['variants'][1:])  # divergent strategy → distinct seeds
print('manifest OK')
"

# 2. validation_log.csv has header + example row.
head -1 tools/validation_log.csv | grep -q "variant_id,source_basename"
wc -l tools/validation_log.csv   # at least 2 lines (header + example)

# 3. validation_correlate.py runs on the example.
python tools/validation_correlate.py tools/validation_log.csv
# Expected: "Not enough samples for correlation (need ≥ 5)" — the example
# row is alone; this is fine.

# 4. docs/validation_harness.md is reachable from README.
grep -q "validation_harness.md" README.md

# 5. No production code touched.
git diff --name-only v0.4.0..HEAD | grep -v '^tools/\|^docs/\|^README\|^specs/'
# Expected: empty (no src/ or tests/ changes)
```

## Tests

No new unit tests — this is tooling and docs. Acceptance checks above
exercise the helpers end-to-end.

| Уровень | Файл | Цель |
|---|---|---|
| Manual | `tools/generate_variants.py` | 3-variant smoke run via acceptance |
| Manual | `tools/validation_correlate.py` | runs on the seeded example CSV |

## Risks

| Риск | Митигация |
|---|---|
| User uploads variants and they're processed by another rights-holder's match (e.g. background music in their own footage) | Doc explicitly warns: "make sure the source has no third-party music / clips. Use original camera footage or licensed material for which YOU are the claimant." |
| YouTube changes Studio UI / removes the Copyright column | Doc says "as of 2026-XX; check Studio docs for current path"; the matching itself is invariant to UI changes |
| Account flagged for uploading many variants of same source | Doc recommends: throwaway channel; spread uploads over days, not all at once; use distinct titles + thumbnails per variant |
| Confirmation bias when recording outcomes | CSV schema requires `youtube_video_id` so we can re-check later; correlation script is deterministic |
| User stops at N=2 and "concludes" the predictor works/doesn't | `validation_correlate.py` refuses to print correlation below N=5 and prints a "STRONGLY/WEAKLY/uncorrelated" verdict only at thresholds well above noise |

## Hand-off

After v0.4.1:

- A repeatable manual loop exists for closing the predictor-vs-reality gap.
- After the first 5–10 logged samples, we'll know whether `cid_predict_self`
  has any actual predictive power. That answer drives whether v0.4.2
  (audio divergence) is worth shipping or whether v0.4.0 already hits the
  empirical KPI.
- `tools/validation_log.csv` becomes a long-lived dataset; future v0.5+
  ML work can train predictors against it once N ≥ 30.

Tag: `v0.4.1`.

## Effort

| Item | Time |
|---|---|
| 1. `tools/generate_variants.py` | 1.5 hours |
| 2. `tools/validation_log.csv` schema + example | 15 min |
| 3. `tools/validation_correlate.py` | 1.5 hours |
| 4. `docs/validation_harness.md` | 1 hour |
| 5. README cross-link + acceptance smoke | 15 min |
| **Total** | **~4 hours / 0.5 day** |
