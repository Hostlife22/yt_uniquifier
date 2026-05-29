"""QaWorker — wraps build_report + render_html + write_json."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from yt_uniquifier.gui.workers.qa_worker import QaWorker


def test_qa_worker_failed_on_build_error(tmp_path: Path) -> None:
    in_p = tmp_path / "in.mp4"
    in_p.touch()
    out_p = tmp_path / "out.mp4"
    out_p.touch()

    errors: list[str] = []
    with patch(
        "yt_uniquifier.gui.workers.qa_worker.build_report",
        side_effect=RuntimeError("boom"),
    ):
        worker = QaWorker(in_p, out_p)
        worker.failed.connect(errors.append)
        worker.run()
    assert errors and "boom" in errors[0]


def test_qa_worker_qa_ready_signal(tmp_path: Path) -> None:
    in_p = tmp_path / "in.mp4"
    in_p.touch()
    out_p = tmp_path / "out.mp4"
    out_p.touch()

    ready: list[tuple[str, str]] = []

    class _FakeReport:
        def model_dump(self, *_a: object, **_kw: object) -> dict:
            return {}

    with (
        patch(
            "yt_uniquifier.gui.workers.qa_worker.build_report",
            return_value=_FakeReport(),
        ),
        patch("yt_uniquifier.gui.workers.qa_worker.write_json"),
        patch("yt_uniquifier.gui.workers.qa_worker.render_html"),
    ):
        worker = QaWorker(in_p, out_p)
        worker.qa_ready.connect(lambda j, h: ready.append((j, h)))
        worker.run()
    assert len(ready) == 1
    qa_json, qa_html = ready[0]
    assert qa_json.endswith(".qa.json")
    assert qa_html.endswith(".qa.html")
