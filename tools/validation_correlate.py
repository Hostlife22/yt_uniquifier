#!/usr/bin/env python3
"""Compute Spearman correlation between qa.json predictors and real CID outcomes.

Reads tools/validation_log.csv (produced by manual workflow from
docs/validation_harness.md) and prints a report telling you whether the
predictors we ship in `qa.json` actually predict real YouTube CID matches.

Pure stdlib (no scipy/pandas) so this tool runs anywhere yt-uniquifier
is installed. Manual rank-based Spearman implementation.

Usage:
  python tools/validation_correlate.py tools/validation_log.csv

Output (≥5 samples):
  total rows: 12, valid: 10
  outcome distribution: {'no_match': 7, 'match': 3}

  Spearman correlation with match_status (1.0 = perfect predictor):
    cid_predict_self            ρ=+0.842  STRONGLY predictive
    phash_worst_chunk           ρ=+0.756  STRONGLY predictive
    audio_fp_hamming_per_frame  ρ=+0.689  WEAKLY predictive

  no_match rate: 70%  (7 / 10 samples)
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

# Ordinal encoding: bigger = "more matched".
STATUS_RANK = {
    "no_match": 0,
    "pending": 1,
    "match": 2,
    "removed": 3,
    "error": -1,  # excluded
}

MIN_SAMPLES_FOR_CORRELATION = 5


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
    den_x = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    den_y = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def verdict_for(rho: float) -> str:
    a = abs(rho)
    if a >= 0.7:
        return "STRONGLY predictive"
    if a >= 0.3:
        return "WEAKLY predictive"
    if a < 0.1:
        return "uncorrelated"
    if rho < 0:
        return "weakly anti-correlated"
    return "weakly correlated"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", type=Path, help="validation_log.csv path")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"error: {args.log} not found", file=sys.stderr)
        return 1

    with args.log.open() as f:
        rows = list(csv.DictReader(f))

    # Filter to rows with meaningful match status + numeric predictors.
    valid: list[dict] = []
    for r in rows:
        if r.get("match_status") not in STATUS_RANK:
            continue
        if r["match_status"] == "error":
            continue
        if not r.get("cid_predict_self"):
            continue
        # Skip the seeded example row.
        if r.get("youtube_video_id") == "EXAMPLE_VIDEO_ID":
            continue
        valid.append(r)

    print(f"total rows: {len(rows)}, valid: {len(valid)}")

    counts: dict[str, int] = {}
    for r in valid:
        counts[r["match_status"]] = counts.get(r["match_status"], 0) + 1
    print(f"outcome distribution: {counts}")

    if len(valid) < MIN_SAMPLES_FOR_CORRELATION:
        print(
            f"\nNot enough samples for correlation "
            f"(need ≥{MIN_SAMPLES_FOR_CORRELATION}, have {len(valid)}). "
            "Keep uploading variants and recording outcomes."
        )
        return 0

    # Build the predictor → list-of-values dict.
    predictors: dict[str, list[float]] = {}
    for name in ("cid_predict_self", "phash_worst_chunk"):
        predictors[name] = [
            float(r[name]) for r in valid if r.get(name)
        ]
    # Hamming is inverted: lower bits = more match-like.
    hamming_vals = [
        -float(r["audio_fp_hamming_per_frame"]) for r in valid
        if r.get("audio_fp_hamming_per_frame")
    ]
    if len(hamming_vals) == len(valid):
        predictors["audio_fp_hamming_per_frame"] = hamming_vals

    outcome_ranks = [STATUS_RANK[r["match_status"]] for r in valid]

    print("\nSpearman correlation with match_status (1.0 = perfect predictor):")
    for name, xs in predictors.items():
        if len(xs) != len(outcome_ranks):
            print(f"  {name:35s}  (skipped — missing values)")
            continue
        rho = spearman(xs, outcome_ranks)
        print(f"  {name:35s}  ρ={rho:+.3f}   {verdict_for(rho)}")

    no_match = sum(1 for r in valid if r["match_status"] == "no_match")
    print(
        f"\nno_match rate: {no_match / len(valid):.0%}  "
        f"({no_match} / {len(valid)} samples)"
    )

    if len(valid) >= 10:
        if no_match / len(valid) >= 0.8:
            print(
                "→ pipeline reaches the public-OSS frontier on own content. "
                "Focus on docs/UX next."
            )
        elif no_match / len(valid) >= 0.6:
            print(
                "→ stable; v0.4.2 + v0.4.3 productive for remaining ~40 %."
            )
        elif no_match / len(valid) >= 0.4:
            print(
                "→ works half the time; Spec 19 + neural FP research warranted."
            )
        else:
            print(
                "→ current stack insufficient; v0.5+ neural attack mode needed."
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
