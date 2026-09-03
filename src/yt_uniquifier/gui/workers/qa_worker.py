"""Standalone QA computation on an (input, output) pair."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from yt_uniquifier.core.models import Plan
from yt_uniquifier.core.qa.report import build_report, render_html, write_json
from yt_uniquifier.gui.workers.base import WorkerBase


class QaWorker(WorkerBase):
    """Compute QA report; emit paths to written .qa.json + .qa.html."""

    qa_ready = pyqtSignal(str, str)              # qa_json_path, qa_html_path

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        *,
        plan: Plan | None = None,
        run_vmaf: bool = True,
        run_ssim: bool = True,
        run_audio_fp: bool = True,
        predict_cid: bool = True,
        vs_corpus: bool = False,
        samples: int = 120,
    ) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.plan = plan
        self.run_vmaf = run_vmaf
        self.run_ssim = run_ssim
        self.run_audio_fp = run_audio_fp
        self.predict_cid = predict_cid
        self.vs_corpus = vs_corpus
        self.samples = samples

    def run(self) -> None:
        try:
            corpus = None
            if self.vs_corpus:
                from yt_uniquifier.core.qa.corpus import Corpus
                corpus = Corpus()
            report = build_report(
                self.input_path, self.output_path,
                plan=self.plan,
                samples=self.samples,
                run_vmaf=self.run_vmaf,
                run_ssim=self.run_ssim,
                run_audio_fp=self.run_audio_fp,
                predict_cid=self.predict_cid,
                vs_corpus=corpus,
                progress=lambda phase, frac: self.progress.emit(
                    frac, f"{phase} {int(frac * 100)}%",
                ),
                # A6 (v0.5.5): honour the worker's cancel token at each
                # QA phase boundary (md5 / phash / audio_fp / vmaf / ssim).
                cancel_token=self.cancel_token,
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return

        qa_json = self.output_path.with_suffix(self.output_path.suffix + ".qa.json")
        qa_html = self.output_path.with_suffix(self.output_path.suffix + ".qa.html")
        try:
            write_json(report, qa_json)
            render_html(report, self.plan, qa_html)
        except Exception as exc:
            self.failed.emit(f"render: {exc}")
            return

        self.qa_ready.emit(str(qa_json), str(qa_html))
        self.finished_ok.emit({"qa_json": str(qa_json), "qa_html": str(qa_html)})
