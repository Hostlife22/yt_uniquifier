"""Unit coverage for the GUI workers not previously tested directly.

Each worker is invoked synchronously via .run(). Signal contracts:
  - success path emits the worker's primary payload signal once
  - failure path emits `failed` with a message
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# QT_QPA_PLATFORM is set in conftest.py before any PyQt import.


# ---- probe_worker ----------------------------------------------------------


def test_probe_worker_failed_on_missing_file(tmp_path: Path) -> None:
    from yt_uniquifier.gui.workers.probe_worker import ProbeWorker

    worker = ProbeWorker(tmp_path / "does-not-exist.mp4")
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed and failed[0]


def test_probe_worker_emits_probed_on_success() -> None:
    from yt_uniquifier.core.models import SourceMeta
    from yt_uniquifier.gui.workers.probe_worker import ProbeWorker

    fake = SourceMeta(
        path=Path("/x.mp4"),
        container="mp4",
        duration_sec=2.0,
        size_bytes=1024,
        video=[], audio=[],
    )
    with patch(
        "yt_uniquifier.gui.workers.probe_worker.probe", return_value=fake,
    ):
        worker = ProbeWorker(Path("/x.mp4"))
        received: list[SourceMeta] = []
        worker.probed.connect(received.append)
        worker.run()
    assert received and received[0].duration_sec == 2.0


# ---- preflight_worker ------------------------------------------------------


def test_preflight_worker_failed_on_bad_profile(tmp_path: Path) -> None:
    from yt_uniquifier.gui.workers.preflight_worker import PreflightWorker

    worker = PreflightWorker(
        tmp_path / "missing.mp4",
        tmp_path / "no-such-profile.yaml",
        None,
    )
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed


# ---- corpus_list_worker ----------------------------------------------------


def test_corpus_list_worker_listed_calls_corpus(tmp_path: Path) -> None:
    """CorpusListWorker delegates to the Corpus object passed in __init__."""
    from yt_uniquifier.gui.workers.corpus_list_worker import CorpusListWorker

    corpus = MagicMock()
    corpus.list_all = MagicMock(return_value=[])
    worker = CorpusListWorker(corpus)
    listed: list[object] = []
    worker.listed.connect(listed.append)
    worker.run()
    assert listed and listed[0] == []


def test_corpus_list_worker_failed_on_exception() -> None:
    from yt_uniquifier.gui.workers.corpus_list_worker import CorpusListWorker

    corpus = MagicMock()
    corpus.list_all = MagicMock(side_effect=RuntimeError("boom"))
    worker = CorpusListWorker(corpus)
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()
    assert failed and "boom" in failed[0]


# ---- queue_io_worker -------------------------------------------------------


def test_queue_io_worker_init_op(tmp_path: Path) -> None:
    """`init` op creates a queue at the root."""
    from yt_uniquifier.gui.workers.queue_io_worker import QueueIoWorker

    with patch(
        "yt_uniquifier.gui.workers.queue_io_worker.init_queue",
    ) as init_mock:
        worker = QueueIoWorker(tmp_path, "init")
        done: list[tuple[int, str]] = []
        worker.done.connect(lambda n, msg: done.append((n, msg)))
        worker.run()
    init_mock.assert_called_once()
    assert done


def test_queue_io_worker_failed_on_exception(tmp_path: Path) -> None:
    from yt_uniquifier.gui.workers.queue_io_worker import QueueIoWorker

    with patch(
        "yt_uniquifier.gui.workers.queue_io_worker.init_queue",
        side_effect=OSError("permission"),
    ):
        worker = QueueIoWorker(tmp_path, "init")
        failed: list[str] = []
        worker.failed.connect(failed.append)
        worker.run()
    assert failed and "permission" in failed[0]


# ---- encoder_detect_worker -------------------------------------------------


def test_encoder_detect_worker_emits_detected() -> None:
    from yt_uniquifier.gui.workers.encoder_detect_worker import (
        EncoderDetectWorker,
    )

    fake_cands = [{"name": "libx264", "vendor": "cpu"}]
    with patch(
        "yt_uniquifier.gui.workers.encoder_detect_worker.detect_encoders",
        return_value=fake_cands,
    ):
        worker = EncoderDetectWorker()
        out: list[object] = []
        worker.detected.connect(out.append)
        worker.run()
    assert out and out[0] == fake_cands


def test_encoder_detect_worker_failed_on_exception() -> None:
    from yt_uniquifier.gui.workers.encoder_detect_worker import (
        EncoderDetectWorker,
    )

    with patch(
        "yt_uniquifier.gui.workers.encoder_detect_worker.detect_encoders",
        side_effect=RuntimeError("ffmpeg missing"),
    ):
        worker = EncoderDetectWorker()
        failed: list[str] = []
        worker.failed.connect(failed.append)
        worker.run()
    assert failed and "ffmpeg missing" in failed[0]


# ---- correlate_worker ------------------------------------------------------


def test_correlate_worker_emits_correlated(tmp_path: Path) -> None:
    """CorrelateWorker runs a subprocess against the given script + csv."""
    from yt_uniquifier.gui.workers.correlate_worker import CorrelateWorker

    script = tmp_path / "correlate.py"
    script.write_text("print('phash↔CID correlation: 0.42')")
    csv = tmp_path / "validation_log.csv"
    csv.write_text("src,delta,vmaf\n")

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "phash↔CID correlation: 0.42"
    fake_result.stderr = ""
    with patch(
        "yt_uniquifier.gui.workers.correlate_worker.subprocess.run",
        return_value=fake_result,
    ):
        worker = CorrelateWorker(script, csv)
        out: list[str] = []
        worker.correlated.connect(out.append)
        worker.run()
    assert out and "correlation" in out[0]


def test_correlate_worker_failed_on_subprocess_error(tmp_path: Path) -> None:
    from yt_uniquifier.gui.workers.correlate_worker import CorrelateWorker

    script = tmp_path / "correlate.py"
    script.write_text("import sys; sys.exit(2)")
    csv = tmp_path / "bad.csv"
    csv.write_text("")

    import subprocess as _sp
    with patch(
        "yt_uniquifier.gui.workers.correlate_worker.subprocess.run",
        side_effect=_sp.CalledProcessError(
            returncode=2, cmd=[], output="", stderr="script error",
        ),
    ):
        worker = CorrelateWorker(script, csv)
        failed: list[str] = []
        worker.failed.connect(failed.append)
        worker.run()
    assert failed and "FAILED" in failed[0]
