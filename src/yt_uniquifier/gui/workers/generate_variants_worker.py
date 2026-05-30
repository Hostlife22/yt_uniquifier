"""Generate N variants of a source for the v0.4.1 validation harness."""

from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.gui.workers.base import WorkerBase


class GenerateVariantsWorker(WorkerBase):
    """Iterate run_full N times → write manifest.json with per-variant KPIs."""

    variant_done = pyqtSignal(dict)              # VariantRecord-shape dict

    def __init__(
        self,
        source: Path,
        profile: Profile,
        out_dir: Path,
        n: int,
        *,
        encoder_override: str | None = None,
    ) -> None:
        super().__init__()
        self.source = source
        self.profile = profile
        self.out_dir = out_dir
        self.n = n
        self.encoder_override = encoder_override

    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, object]] = []
        # Count successes separately from the loop index so progress
        # reflects work actually completed. Tying progress to `i` over-
        # reports when variants fail mid-run.
        completed = 0
        for i in range(1, self.n + 1):
            if self.cancel_token.is_cancelled():
                self.log.emit(f"cancelled after {completed}/{self.n}")
                break
            try:
                plan = build_plan(self.source, self.profile, self.encoder_override)
                out = self.out_dir / f"variant_{i:03d}.mp4"
                work = self.out_dir / "work" / f"variant_{i:03d}"
                run_full(plan, RunOptions(
                    work_dir=work, output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                ), cancel_token=self.cancel_token)
                qa_path = out.with_suffix(".mp4.qa.json")
                qa: dict[str, object] = (
                    json.loads(qa_path.read_text()) if qa_path.exists() else {}
                )
                chunks_raw = qa.get("chunk_similarities") or []
                chunks: list[dict[str, float]] = (
                    chunks_raw if isinstance(chunks_raw, list) else []
                )
                worst = max(
                    (
                        float(c.get("combined", 0.0))
                        for c in chunks if isinstance(c, dict)
                    ),
                    default=None,
                )
                rec = {
                    "variant_id": f"variant_{i:03d}",
                    "output_path": str(out),
                    "qa_json_path": str(qa_path),
                    "run_seed": plan.run_seed,
                    "plan_hash": plan.plan_hash,
                    "cid_predict_self": qa.get("cid_predict_self"),
                    "audio_fp_hamming_per_frame": qa.get("audio_fp_hamming_per_frame"),
                    "phash_worst_chunk": worst,
                    "vmaf_mean": qa.get("vmaf_mean"),
                }
                records.append(rec)
                completed += 1
                self.variant_done.emit(rec)
                self.progress.emit(completed / self.n, f"{completed} / {self.n}")
            except Exception as exc:
                self.log.emit(f"variant {i}: FAILED — {type(exc).__name__}: {exc}")

        manifest = {
            "source": str(self.source),
            "profile": self.profile.name,
            "encoder": self.encoder_override,
            "n_variants": len(records),
            "variants": records,
        }
        (self.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        self.finished_ok.emit(manifest)
