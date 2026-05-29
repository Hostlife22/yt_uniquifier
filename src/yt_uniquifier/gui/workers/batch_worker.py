"""Iterate orchestrator.run_full over a directory of files."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full
from yt_uniquifier.gui.workers.base import WorkerBase


class BatchWorker(WorkerBase):
    """Process every matched file in a directory.

    Per-file signals so the GUI table can update independently of
    overall progress. `continue_on_error` decides whether to stop or
    skip-and-continue when a single file fails.
    """

    file_started = pyqtSignal(str)              # source path
    file_done = pyqtSignal(str, str)            # source path, output path
    file_failed = pyqtSignal(str, str)          # source path, error message

    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        profile: Profile,
        encoder_override: str | None,
        *,
        glob_pattern: str = "*.mp4",
        continue_on_error: bool = True,
        work_dir_root: Path | None = None,
        run_qa: bool = False,        # batch QA = expensive; off by default
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.profile = profile
        self.encoder_override = encoder_override
        self.glob_pattern = glob_pattern
        self.continue_on_error = continue_on_error
        self.work_dir_root = work_dir_root or (
            Path.home() / ".cache" / "yt_uniquifier" / "batch"
        )
        self.run_qa = run_qa

    def run(self) -> None:
        files = sorted(self.input_dir.glob(self.glob_pattern))
        if not files:
            self.failed.emit(f"no files matched {self.glob_pattern} in {self.input_dir}")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, str]] = []
        total = len(files)
        for i, src in enumerate(files):
            if self.cancel_token.is_cancelled():
                self.log.emit(f"cancelled after {i}/{total}")
                break

            out = self.output_dir / f"{src.stem}.uniq.mp4"
            self.file_started.emit(str(src))
            try:
                plan = build_plan(src, self.profile, self.encoder_override)
                opts = RunOptions(
                    work_dir=self.work_dir_root / plan.plan_hash,
                    output=out,
                    target_segment_sec=600.0,
                    enforce_preflight=True,
                )
                run_full(plan, opts, cancel_token=self.cancel_token)
                self.file_done.emit(str(src), str(out))
                results.append({
                    "path": str(src), "status": "done", "output": str(out),
                })
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.file_failed.emit(str(src), msg)
                results.append({"path": str(src), "status": "failed", "error": msg})
                if not self.continue_on_error:
                    self.failed.emit(f"stopped at {src.name}: {msg}")
                    return

            self.progress.emit((i + 1) / total, f"{i + 1} / {total} files")

        self.finished_ok.emit({
            "files_processed": len(results), "results": results,
        })
