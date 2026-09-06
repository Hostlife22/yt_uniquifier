"""Real CLI/web/queue-worker jobs share one local resource registry."""

from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import needs_ffmpeg


def _frontend(root_text: str, kind: str, ready: Any, start: Any, results: Any) -> None:
    root = Path(root_text)
    os.environ["YT_UNIQ_RESOURCE_LOCK_DIR"] = str(root / "registry")
    source, profile = root / "source.mp4", root / "profile.yaml"
    try:
        from typer.testing import CliRunner

        from yt_uniquifier.cli.app import app
        from yt_uniquifier.core import encoder
        from yt_uniquifier.core.queue.leasing import FileQueue, init_queue

        encoder.CACHE_PATH = root / f"{kind}-encoders.json"
        if kind == "web":
            from fastapi.testclient import TestClient

            from yt_uniquifier.web.app import WebConfig, build_app
        ready.put(kind)
        if not start.wait(30):
            raise TimeoutError("frontend start barrier")
        if kind == "web":
            config = WebConfig(
                work_dir=root / "web-work", output_dir=root / "web-output",
                profile_dir=root, input_root=root,
            )
            with TestClient(build_app(config)) as client:
                response = client.post("/api/run", json={
                    "input_path": str(source), "profile_path": str(profile),
                    "output_name": "output.mp4", "encoder_override": "libx264",
                })
                assert response.status_code == 200, response.text
                run_id = response.json()["run_id"]
                deadline = time.monotonic() + 100
                while time.monotonic() < deadline:
                    status = client.get(f"/api/run/{run_id}/status").json()
                    if status["status"] in {"completed", "failed", "cancelled"}:
                        assert status["status"] == "completed", status
                        break
                    time.sleep(0.05)
                else:
                    client.post(f"/api/run/{run_id}/cancel")
                    raise TimeoutError("web qualification run")
        else:
            common = ["--profile", str(profile), "--encoder", "libx264", "--workers", "1",
                      "--work-dir", str(root / f"{kind}-work")]
            if kind == "cli":
                args = ["run", str(source), "--out", str(root / "cli.mp4"),
                        "--no-qa", "--no-progress", *common]
            else:
                queue_dir = root / "queue"
                init_queue(queue_dir)
                queue = FileQueue(queue_dir)
                queued_source = root / "worker.mp4"
                shutil.copyfile(source, queued_source)
                queue.add(queued_source)
                args = ["worker", str(queue_dir), "--out-dir", str(root / "worker-output"),
                        "--stop-after-empty", "--poll-sec", "0.1", *common]
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, f"{result.output}\n{result.exception}"
            if kind == "worker":
                assert queue.stats()["done"] == 1, queue.stats()
        results.put((kind, "passed"))
    except BaseException:
        results.put((kind, traceback.format_exc()))


@needs_ffmpeg
@pytest.mark.integration
def test_actual_frontends_share_registry_and_publish_valid_outputs(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    psutil = pytest.importorskip("psutil")
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=24:duration=8", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=8", "-c:v", "libx264",
        "-preset", "ultrafast", "-c:a", "aac", str(tmp_path / "source.mp4"),
    ], check=True, timeout=30)
    (tmp_path / "profile.yaml").write_text(
        "name: concurrent-frontends\nskip_watermark_check: true\ntransforms: []\n",
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(
        target=_frontend, args=(str(tmp_path), kind, ready, start, results),
    ) for kind in ("cli", "web", "worker")]
    observed: set[int] = set()
    try:
        for process in processes:
            process.start()
        assert {ready.get(timeout=30) for _ in processes} == {"cli", "web", "worker"}
        start.set()
        deadline = time.monotonic() + 110
        while any(process.is_alive() for process in processes) and time.monotonic() < deadline:
            for lock in (tmp_path / "registry").glob("*/reservations/*.lock"):
                # A reservation can be released during observation.
                with contextlib.suppress(OSError, ValueError, KeyError):
                    observed.add(int(json.loads(lock.read_text())["pid"]))
            time.sleep(0.02)
        outcomes = [results.get(timeout=5) for _ in processes]
        failures = [f"{kind}:\n{status}" for kind, status in outcomes if status != "passed"]
        if failures:
            pytest.fail("\n\n".join(failures))
        assert observed == {process.pid for process in processes}
    finally:
        for process in processes:
            process.join(timeout=1)
            if process.is_alive():
                descendants = psutil.Process(process.pid).children(recursive=True)
                for descendant in descendants:
                    with contextlib.suppress(psutil.NoSuchProcess):
                        descendant.kill()
                process.kill()
                process.join(timeout=5)
        ready.close()
        results.close()
    assert not list((tmp_path / "registry").glob("*/reservations/*.lock"))
    for output in (
        tmp_path / "cli.mp4", tmp_path / "web-output/output.mp4",
        tmp_path / "worker-output/worker.uniq.mp4",
    ):
        assert output.is_file()
        subprocess.run([
            "ffmpeg", "-v", "error", "-xerror", "-i", str(output),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
        ], check=True, timeout=30)
