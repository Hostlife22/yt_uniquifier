#!/usr/bin/env python3
"""Generate N reproducible variants of one source for manual CID upload tests.

Each variant rolls a fresh run_seed (per_run / divergent strategy semantics).

Usage:
  python tools/generate_variants.py /path/to/master.mp4 \\
    --profile src/yt_uniquifier/profiles/cid_aware.yaml \\
    --out-dir /tmp/cid_test \\
    --n 10 \\
    --encoder libx264

Output:
  <out-dir>/variant_001.mp4
  <out-dir>/variant_001.mp4.qa.json
  <out-dir>/variant_001.mp4.qa.html
  ...
  <out-dir>/manifest.json  — full record + per-variant predicted KPIs

The manifest is then used as Step-1 input for the validation_log.csv
workflow — copy its `variants` array into the CSV, then fill in the
real upload outcomes manually after uploading each variant.

See docs/validation_harness.md for the full loop.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.core.profile_loader import load_profile


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
        enforce_preflight=True,
    ))

    qa_path = output.with_suffix(".mp4.qa.json")
    qa: dict = {}
    if qa_path.exists():
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
            f"  seed={rec.run_seed} "
            f"cid_predict={rec.cid_predict_self} "
            f"audio_hamming={rec.audio_fp_hamming_per_frame} "
            f"phash_worst={rec.phash_worst_chunk} "
            f"vmaf={rec.vmaf_mean}",
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
    print(f"\nmanifest: {manifest_path}", file=sys.stderr)
    print(
        "\nnext steps:\n"
        "  1. Upload each variant_XXX.mp4 as Unlisted on YouTube.\n"
        "  2. Wait 5-10 min, check Studio → Content → Copyright tab.\n"
        "  3. Append outcomes to tools/validation_log.csv.\n"
        "  4. python tools/validation_correlate.py tools/validation_log.csv\n"
        "See docs/validation_harness.md for full details.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
