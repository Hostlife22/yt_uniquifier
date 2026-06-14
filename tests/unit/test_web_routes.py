"""FastAPI route tests for the headless web UI (v0.9.0 R4 / F13).

Skip-gated on the ``[web]`` extra so a default ``[dev]`` install
still has a green ``make test-unit``. Exercises the route handlers
in-process via ``starlette.testclient.TestClient`` — no socket,
no real ``run_full`` (the orchestrator is monkeypatched for the
run-lifecycle test so we don't fork ffmpeg).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Skip the entire module on hosts without the [web] extra installed.
if importlib.util.find_spec("fastapi") is None:  # pragma: no cover
    pytest.skip("fastapi not installed; skip web route tests",
                allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from yt_uniquifier.web.app import WebConfig, build_app  # noqa: E402


@pytest.fixture()
def web_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    work = tmp_path / "work"
    output = tmp_path / "output"
    profiles = tmp_path / "profiles"
    for d in (work, output, profiles):
        d.mkdir()
    return work, output, profiles


@pytest.fixture()
def client(web_dirs: tuple[Path, Path, Path]) -> TestClient:
    work, output, profiles = web_dirs
    config = WebConfig(
        work_dir=work,
        output_dir=output,
        profile_dir=profiles,
    )
    return TestClient(build_app(config))


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------


def test_healthz_returns_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_index_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "yt-uniquifier" in r.text
    assert "/static/app.js" in r.text


# ---------------------------------------------------------------------------
# Auth gating (only when both creds present)
# ---------------------------------------------------------------------------


def test_auth_disabled_when_creds_absent(client: TestClient) -> None:
    r = client.get("/api/profiles/local")
    assert r.status_code == 200


def test_auth_enforced_when_creds_present(
    web_dirs: tuple[Path, Path, Path]
) -> None:
    work, output, profiles = web_dirs
    config = WebConfig(
        work_dir=work, output_dir=output, profile_dir=profiles,
        basic_auth_user="alice", basic_auth_pass="hunter2",
    )
    client = TestClient(build_app(config))
    # No creds → 401.
    r = client.get("/api/profiles/local")
    assert r.status_code == 401
    # Wrong creds → 401.
    r = client.get("/api/profiles/local", auth=("alice", "wrong"))
    assert r.status_code == 401
    # Right creds → 200.
    r = client.get("/api/profiles/local", auth=("alice", "hunter2"))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def test_list_local_profiles_finds_per_user_yaml(
    client: TestClient, web_dirs: tuple[Path, Path, Path]
) -> None:
    _, _, profiles = web_dirs
    (profiles / "custom.yaml").write_text(
        "name: custom\ntransforms: []\n", encoding="utf-8",
    )
    r = client.get("/api/profiles/local")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert "custom" in names


def test_list_community_uses_bootstrap_when_offline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a network outage so fetch_catalog falls back to bootstrap.
    from yt_uniquifier.core import profile_marketplace as pm

    def boom(*_a: object, **_kw: object) -> object:
        raise OSError("offline")

    monkeypatch.setattr(pm.urllib.request, "urlopen", boom)
    pm.purge_cache()
    r = client.get("/api/profiles/community")
    assert r.status_code == 200
    entries = r.json()
    assert any(e["id"] == "cid_aware" for e in entries)


# ---------------------------------------------------------------------------
# Run lifecycle (orchestrator monkey-patched)
# ---------------------------------------------------------------------------


def _stub_run_full_factory(events_to_emit: list[tuple[str, dict]]) -> object:
    """Build a fake run_full that emits given events then completes."""

    def fake(plan, options, *, on_event=None, cancel_token=None,
             pause_token=None):
        from yt_uniquifier.core.runner import RunEvent
        if on_event:
            for kind, payload in events_to_emit:
                on_event(RunEvent(kind=kind, payload=payload))

        # Build a minimal RunSummary the caller doesn't use here.
        from yt_uniquifier.core.orchestrator import RunSummary
        return RunSummary(
            output=options.output,
            plan=plan,
            segments_done=len(events_to_emit),
            preflight_findings=[],
        )

    return fake


def test_run_lifecycle_streams_events_and_completes(
    client: TestClient,
    web_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end: post a run, SSE stream emits events, status flips
    to completed. The orchestrator is stubbed so no ffmpeg is forked.
    """
    work, output, profiles = web_dirs

    # Stub orchestrator + build_plan + load_profile so we don't need
    # a real source / ffmpeg / encoder probe.
    from yt_uniquifier.web.routes import run as run_routes

    fake_run_full = _stub_run_full_factory([
        ("log", {"phase": "preflight", "message": "ok"}),
        ("segment_done", {"idx": 0}),
        ("completed", {"output": "out.mp4"}),
    ])
    monkeypatch.setattr(run_routes, "run_full", fake_run_full)

    def fake_build_plan(_in, profile, _enc):
        from yt_uniquifier.core.models import (
            EncoderCandidate,
            HDRInfo,
            Plan,
            SourceMeta,
            VideoStream,
        )
        src = SourceMeta(
            path=tmp_path / "in.mp4", container="mp4",
            duration_sec=1, size_bytes=10,
            video=[VideoStream(
                index=0, codec="h264", width=64, height=64, fps=24.0,
                duration_sec=1, pix_fmt="yuv420p", bit_rate=1_000_000,
                color=HDRInfo(is_hdr=False, transfer="bt709",
                              primaries="bt709", space="bt709"),
            )],
            audio=[], subtitle=[],
        )
        enc = EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        )
        return Plan(source=src, profile=profile, encoder=enc,
                    plan_hash="deadbeef")
    monkeypatch.setattr(run_routes, "build_plan", fake_build_plan)

    # Real load_profile is fine — give it a real YAML.
    prof_path = profiles / "stub.yaml"
    prof_path.write_text("name: stub\ntransforms: []\n", encoding="utf-8")
    src_path = tmp_path / "in.mp4"
    src_path.touch()

    r = client.post("/api/run", json={
        "input_path": str(src_path),
        "profile_path": str(prof_path),
    })
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    # Drain the SSE stream — keep it sync via TestClient.stream().
    with client.stream("GET", f"/api/run/{run_id}/events") as resp:
        assert resp.status_code == 200
        chunks: list[str] = []
        for chunk in resp.iter_text():
            chunks.append(chunk)
            if "event: end" in "".join(chunks):
                break
    body = "".join(chunks)
    assert "preflight" in body
    assert "segment_done" in body
    assert "event: end" in body

    # Status endpoint reflects the terminal state.
    r2 = client.get(f"/api/run/{run_id}/status")
    assert r2.status_code == 200
    assert r2.json()["status"] in {"completed", "running"}


def test_run_rejects_missing_input(client: TestClient, tmp_path: Path) -> None:
    r = client.post("/api/run", json={
        "input_path": str(tmp_path / "absent.mp4"),
        "profile_path": str(tmp_path / "absent.yaml"),
    })
    assert r.status_code == 404


def test_run_rejects_input_outside_root(
    web_dirs: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    work, output, profiles = web_dirs
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    config = WebConfig(
        work_dir=work, output_dir=output, profile_dir=profiles,
        input_root=sandbox,
    )
    client = TestClient(build_app(config))
    outside = tmp_path / "outside.mp4"
    outside.touch()
    r = client.post("/api/run", json={
        "input_path": str(outside),
        "profile_path": str(profiles / "any.yaml"),
    })
    assert r.status_code == 403


def test_cancel_unknown_run_id(client: TestClient) -> None:
    r = client.post("/api/run/does_not_exist/cancel")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# QA path traversal guard
# ---------------------------------------------------------------------------


def test_qa_rejects_path_traversal(client: TestClient) -> None:
    r = client.get("/api/qa/..%2Fpasswd/html")
    # Either 400 (traversal rejected) or 404 (basename normalised) is fine.
    assert r.status_code in {400, 404}


def test_qa_serves_html_when_present(
    client: TestClient, web_dirs: tuple[Path, Path, Path]
) -> None:
    _, output, _ = web_dirs
    (output / "myrun.qa.html").write_text("<h1>ok</h1>", encoding="utf-8")
    r = client.get("/api/qa/myrun/html")
    assert r.status_code == 200
    assert "ok" in r.text


# ---------------------------------------------------------------------------
# v1.1.0 Task 15: /readyz + /metrics
# ---------------------------------------------------------------------------


def test_readyz_returns_200_when_encoders_present(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Encoder probe goes through encoder.detect_encoders. Stub it to
    return one working candidate so /readyz does not depend on a real
    ffmpeg install in the test runner.
    """
    from yt_uniquifier.core import encoder
    from yt_uniquifier.core.models import EncoderCandidate

    monkeypatch.setattr(
        encoder, "detect_encoders",
        lambda *a, **kw: [EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        )],
    )
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert "encoders" in body["checks"]
    assert "work_dir" in body["checks"]


def test_readyz_returns_503_when_no_working_encoder(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core import encoder

    monkeypatch.setattr(encoder, "detect_encoders", lambda *a, **kw: [])
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False
    assert "no working encoder" in body["checks"]["encoders"]


def test_metrics_endpoint_serves_prometheus_text(
    client: TestClient,
) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    # prometheus_client emits text/plain with a versioned content-type.
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # The custom counter families must be present in the registry.
    assert "yt_uniq_segments_total" in body
    assert "yt_uniq_runs_total" in body
    assert "yt_uniq_active_runs" in body


def test_metrics_update_from_event_increments_counters() -> None:
    """v1.1.0 Task 15: ``update_from_event`` plumbs RunEvents into the
    appropriate Prometheus family without coupling the orchestrator to
    the metrics module.
    """
    from yt_uniquifier.core.runner import RunEvent
    from yt_uniquifier.web import metrics

    before = metrics.SEGMENTS_TOTAL.labels(status="done")._value.get()
    metrics.update_from_event(RunEvent(
        kind="segment_done",
        payload={"status": "done", "duration_sec": 12.5, "run_id": "r1"},
    ))
    after = metrics.SEGMENTS_TOTAL.labels(status="done")._value.get()
    assert after == before + 1.0

    err_before = metrics.FFMPEG_FAILURES_TOTAL.labels(encoder="libx264")._value.get()
    metrics.update_from_event(RunEvent(
        kind="error",
        payload={"encoder": "libx264", "message": "boom"},
    ))
    err_after = metrics.FFMPEG_FAILURES_TOTAL.labels(encoder="libx264")._value.get()
    assert err_after == err_before + 1.0
