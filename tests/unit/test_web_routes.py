"""FastAPI route tests for the headless web UI (v0.9.0 R4 / F13).

Skip-gated on the ``[web]`` extra so a default ``[dev]`` install
still has a green ``make test-unit``. Exercises the route handlers
in-process via ``starlette.testclient.TestClient`` — no socket,
no real ``run_full`` (the orchestrator is monkeypatched for the
run-lifecycle test so we don't fork ffmpeg).
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
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
        input_root=work.parent,
    )
    return TestClient(build_app(config))


# ---------------------------------------------------------------------------
# Static surface
# ---------------------------------------------------------------------------


def test_healthz_returns_ok(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_run_status_survives_restart_and_marks_active_interrupted(
    web_dirs: tuple[Path, Path, Path],
) -> None:
    work, output, profiles = web_dirs
    now = time.time()
    (work / "web_runs.json").write_text(json.dumps({
        "schema_version": 1,
        "runs": [{
            "run_id": "before-restart",
            "status": "running",
            "error": None,
            "output_basename": "movie.mp4",
            "created_at": now - 60,
            "updated_at": now - 1,
        }],
    }), encoding="utf-8")

    restarted = TestClient(build_app(WebConfig(
        work_dir=work,
        output_dir=output,
        profile_dir=profiles,
        input_root=work.parent,
    )))
    response = restarted.get("/api/run/before-restart/status")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "restarted" in response.json()["error"]
    persisted = json.loads((work / "web_runs.json").read_text(encoding="utf-8"))
    assert persisted["runs"][0]["status"] == "failed"
    assert str(work.parent) not in (work / "web_runs.json").read_text(encoding="utf-8")


def test_expired_run_status_is_pruned_on_startup(
    web_dirs: tuple[Path, Path, Path],
) -> None:
    work, output, profiles = web_dirs
    (work / "web_runs.json").write_text(json.dumps({
        "schema_version": 1,
        "runs": [{
            "run_id": "expired",
            "status": "completed",
            "output_basename": "old.mp4",
            "created_at": 1,
            "updated_at": 1,
        }],
    }), encoding="utf-8")

    restarted = TestClient(build_app(WebConfig(
        work_dir=work,
        output_dir=output,
        profile_dir=profiles,
        input_root=work.parent,
        run_retention_sec=60,
    )))

    assert restarted.get("/api/run/expired/status").status_code == 404


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
    from yt_uniquifier.web import metrics
    from yt_uniquifier.web.routes import run as run_routes

    fake_run_full = _stub_run_full_factory([
        ("log", {"phase": "preflight", "message": "ok"}),
        ("segment_done", {"segment": 0, "status": "done"}),
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

    state_before = {
        state: metrics.RUN_STATE_EVENTS_TOTAL.labels(state=state)._value.get()
        for state in ("queued", "active", "completed")
    }

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
    assert '"plan_id": "deadbeef"' in body
    assert '"job_id":' in body
    assert '"segment_id":' in body

    # Status endpoint reflects the terminal state.
    r2 = client.get(f"/api/run/{run_id}/status")
    assert r2.status_code == 200
    assert r2.json()["status"] in {"completed", "running"}
    for state in ("queued", "active", "completed"):
        assert (
            metrics.RUN_STATE_EVENTS_TOTAL.labels(state=state)._value.get()
            == state_before[state] + 1
        )


def test_two_app_instances_cannot_reserve_the_same_output(
    web_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The output reservation must be shared across web processes."""
    work, output, profiles = web_dirs
    source_path = tmp_path / "shared-input.mp4"
    source_path.touch()
    profile_path = profiles / "shared.yaml"
    profile_path.write_text("name: shared\ntransforms: []\n", encoding="utf-8")

    from yt_uniquifier.core.models import (
        EncoderCandidate,
        HDRInfo,
        Plan,
        Profile,
        SourceMeta,
        VideoStream,
    )
    from yt_uniquifier.core.orchestrator import RunSummary
    from yt_uniquifier.web.routes import run as run_routes

    profile = Profile(name="shared", transforms=[])
    source = SourceMeta(
        path=source_path,
        container="mp4",
        duration_sec=1.0,
        size_bytes=1,
        video=[VideoStream(
            index=0,
            codec="h264",
            width=64,
            height=64,
            fps=24.0,
            duration_sec=1.0,
            pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[],
        subtitle=[],
    )
    plan = Plan(
        source=source,
        profile=profile,
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="shared-output-plan",
    )
    started = threading.Event()
    release = threading.Event()

    def blocking_run_full(plan, options, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        started.set()
        release.wait(timeout=5)
        return RunSummary(
            output=options.output,
            plan=plan,
            segments_done=0,
            preflight_findings=[],
        )

    monkeypatch.setattr(run_routes, "load_profile", lambda path: profile)
    monkeypatch.setattr(run_routes, "build_plan", lambda *args: plan)
    monkeypatch.setattr(run_routes, "run_full", blocking_run_full)

    first = TestClient(build_app(WebConfig(
        work_dir=work / "instance-a",
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
    )))
    second = TestClient(build_app(WebConfig(
        work_dir=work / "instance-b",
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
    )))
    payload = {
        "input_path": str(source_path),
        "profile_path": str(profile_path),
        "output_name": "shared.mp4",
    }

    try:
        first_response = first.post("/api/run", json=payload)
        assert first_response.status_code == 200
        first_run_id = first_response.json()["run_id"]
        assert started.wait(timeout=2)

        second_response = second.post("/api/run", json=payload)
        assert second_response.status_code == 409
        assert "reserved" in second_response.json()["detail"]
    finally:
        release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = first.get(f"/api/run/{first_run_id}/status").json()["status"]
        if status == "completed":
            break
        time.sleep(0.01)
    assert status == "completed"
    retry_response = second.post("/api/run", json=payload)
    assert retry_response.status_code == 200


def test_two_app_instances_share_global_run_admission(
    web_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Different output names must still share max_concurrent_runs."""
    work, output, profiles = web_dirs
    source_path = tmp_path / "shared-input.mp4"
    source_path.touch()
    profile_path = profiles / "shared.yaml"
    profile_path.write_text("name: shared\ntransforms: []\n", encoding="utf-8")

    from yt_uniquifier.core.models import Profile
    from yt_uniquifier.web.routes import run as run_routes

    started = threading.Event()
    release = threading.Event()

    def blocking_run_full(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(run_routes, "load_profile", lambda _path: Profile(name="shared"))
    monkeypatch.setattr(run_routes, "build_plan", lambda *_args: object())
    monkeypatch.setattr(run_routes, "run_full", blocking_run_full)

    first = TestClient(build_app(WebConfig(
        work_dir=work / "instance-a",
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
        max_concurrent_runs=1,
    )))
    second = TestClient(build_app(WebConfig(
        work_dir=work / "instance-b",
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
        max_concurrent_runs=1,
    )))
    base_payload = {
        "input_path": str(source_path),
        "profile_path": str(profile_path),
    }

    try:
        first_response = first.post(
            "/api/run",
            json={**base_payload, "output_name": "first.mp4"},
        )
        assert first_response.status_code == 200
        first_run_id = first_response.json()["run_id"]
        assert started.wait(timeout=2)

        second_response = second.post(
            "/api/run",
            json={**base_payload, "output_name": "second.mp4"},
        )
        assert second_response.status_code == 429
        assert "shared maximum" in second_response.json()["detail"]
    finally:
        release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = first.get(f"/api/run/{first_run_id}/status").json()["status"]
        if status == "completed":
            break
        time.sleep(0.01)
    assert status == "completed"

    retry_response = second.post(
        "/api/run",
        json={**base_payload, "output_name": "second.mp4"},
    )
    assert retry_response.status_code == 200


def test_run_uses_profile_container_and_rejects_conflicting_suffix(
    web_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    work, output, profiles = web_dirs
    source_path = tmp_path / "movie.mp4"
    source_path.touch()
    profile_path = profiles / "mkv.yaml"
    profile_path.write_text(
        "name: archive\noutput_container: mkv\ntransforms: []\n",
        encoding="utf-8",
    )

    from yt_uniquifier.core.models import Profile
    from yt_uniquifier.web.routes import run as run_routes

    profile = Profile(name="archive", output_container="mkv", transforms=[])
    monkeypatch.setattr(run_routes, "load_profile", lambda path: profile)
    monkeypatch.setattr(run_routes, "build_plan", lambda *args: object())
    monkeypatch.setattr(run_routes, "run_full", lambda *args, **kwargs: None)
    client = TestClient(build_app(WebConfig(
        work_dir=work,
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
    )))
    base_payload = {
        "input_path": str(source_path),
        "profile_path": str(profile_path),
    }

    default_response = client.post("/api/run", json=base_payload)
    assert default_response.status_code == 200
    assert default_response.json()["output_basename"] == "movie__archive.mkv"

    wrong_response = client.post(
        "/api/run",
        json={**base_payload, "output_name": "wrong.mp4"},
    )
    assert wrong_response.status_code == 400
    assert "container" in wrong_response.json()["detail"]


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


def test_run_rejects_input_symlink_escaping_root(
    web_dirs: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    work, output, profiles = web_dirs
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.touch()
    link = sandbox / "linked.mp4"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available for this test account")
    config = WebConfig(
        work_dir=work, output_dir=output, profile_dir=profiles,
        input_root=sandbox,
    )
    client = TestClient(build_app(config))

    r = client.post("/api/run", json={
        "input_path": str(link),
        "profile_path": str(profiles / "any.yaml"),
    })

    assert r.status_code == 403


def test_run_rejects_profile_outside_configured_root(
    client: TestClient, web_dirs: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    _, _, profiles = web_dirs
    input_path = tmp_path / "input.mp4"
    input_path.touch()
    outside_profile = tmp_path / "outside.yaml"
    outside_profile.write_text("name: outside\ntransforms: []\n", encoding="utf-8")

    r = client.post("/api/run", json={
        "input_path": str(input_path),
        "profile_path": str(outside_profile),
    })

    assert r.status_code == 403
    assert str(outside_profile) not in r.text


@pytest.mark.parametrize("output_name", ["../escape.mp4", "sub/escape.mp4", "sub\\escape.mp4"])
def test_run_rejects_output_path_traversal(
    client: TestClient,
    web_dirs: tuple[Path, Path, Path],
    tmp_path: Path,
    output_name: str,
) -> None:
    _, output, profiles = web_dirs
    input_path = tmp_path / "input.mp4"
    input_path.touch()
    profile_path = profiles / "safe.yaml"
    profile_path.write_text("name: safe\ntransforms: []\n", encoding="utf-8")

    r = client.post("/api/run", json={
        "input_path": str(input_path),
        "profile_path": str(profile_path),
        "output_name": output_name,
    })

    assert r.status_code == 400
    assert not (output.parent / "escape.mp4").exists()


def test_run_rejects_output_symlink_escaping_root(
    client: TestClient,
    web_dirs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    _, output, profiles = web_dirs
    input_path = tmp_path / "input.mp4"
    input_path.touch()
    profile_path = profiles / "safe.yaml"
    profile_path.write_text("name: safe\ntransforms: []\n", encoding="utf-8")
    outside = tmp_path / "outside.mp4"
    link = output / "linked.mp4"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available for this test account")

    r = client.post("/api/run", json={
        "input_path": str(input_path),
        "profile_path": str(profile_path),
        "output_name": link.name,
    })

    assert r.status_code == 400
    assert not outside.exists()


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


def test_readyz_does_not_expose_work_dir_error(
    web_dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yt_uniquifier.core import encoder

    work, output, profiles = web_dirs
    blocked_work_path = work / "not-a-directory"
    blocked_work_path.write_text("blocked", encoding="utf-8")
    monkeypatch.setattr(encoder, "detect_encoders", lambda *a, **kw: [])
    test_client = TestClient(build_app(WebConfig(
        work_dir=blocked_work_path,
        output_dir=output,
        profile_dir=profiles,
    )))

    r = test_client.get("/readyz")

    assert r.status_code == 503
    assert r.json()["checks"]["work_dir"] == "unwritable"
    assert str(blocked_work_path) not in r.text


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


# ---------------------------------------------------------------------------
# v1.1.0 Task 16: upload cap + audit log
# ---------------------------------------------------------------------------


def test_upload_limit_rejects_oversized_content_length(
    web_dirs: tuple[Path, Path, Path],
) -> None:
    """Content-Length > config.max_upload_bytes → 413 with JSON body."""
    work, output, profiles = web_dirs
    config = WebConfig(
        work_dir=work, output_dir=output, profile_dir=profiles,
        max_upload_bytes=1024,  # 1 KiB ceiling for the test
    )
    client = TestClient(build_app(config))
    # Construct a POST whose declared body length blows past the cap.
    # We don't actually send 1 MB — Starlette only reads after the
    # middleware runs, and the middleware short-circuits on the
    # Content-Length header alone.
    headers = {
        "content-length": str(10 * 1024),
        "content-type": "application/json",
    }
    r = client.post(
        "/api/run", headers=headers, content=b"x" * 10 * 1024,
    )
    assert r.status_code == 413
    body = r.json()
    assert "10240" in body["detail"]
    assert "1024" in body["detail"]


def test_run_rate_limit_rejects_request_after_budget(
    web_dirs: tuple[Path, Path, Path],
    tmp_path: Path,
) -> None:
    work, output, profiles = web_dirs
    config = WebConfig(
        work_dir=work,
        output_dir=output,
        profile_dir=profiles,
        input_root=tmp_path,
        rate_limit_run="2/minute",
    )
    client = TestClient(build_app(config))
    payload = {
        "input_path": str(tmp_path / "missing.mp4"),
        "profile_path": str(profiles / "missing.yaml"),
    }

    assert client.post("/api/run", json=payload).status_code == 404
    assert client.post("/api/run", json=payload).status_code == 404
    limited = client.post("/api/run", json=payload)

    assert limited.status_code == 429
    assert "rate limit exceeded" in limited.json()["detail"]


def test_audit_log_records_run_start_and_cancel(
    tmp_path: Path, web_dirs: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.1.0 Task 16: audit_log_path receives one JSONL line per
    state-changing request.
    """
    work, output, profiles = web_dirs
    audit_path = tmp_path / "audit.jsonl"
    config = WebConfig(
        work_dir=work, output_dir=output, profile_dir=profiles,
        input_root=tmp_path,
        audit_log_path=audit_path,
    )

    # Stub the orchestrator so /api/run doesn't actually encode.
    from yt_uniquifier.core import orchestrator
    from yt_uniquifier.core.models import (
        AudioStream,
        EncoderCandidate,
        HDRInfo,
        Plan,
        SourceMeta,
        VideoStream,
    )
    from yt_uniquifier.core.models import (
        Profile as CoreProfile,
    )
    from yt_uniquifier.core.pipeline import compute_plan_hash

    src_path = tmp_path / "x.mp4"
    src_path.write_bytes(b"x")
    src = SourceMeta(
        path=src_path, container="mp4", duration_sec=1.0, size_bytes=10,
        video=[VideoStream(
            index=0, codec="h264", width=128, height=72, fps=24.0,
            duration_sec=1.0, pix_fmt="yuv420p",
            color=HDRInfo(is_hdr=False),
        )],
        audio=[AudioStream(index=1, codec="aac", sample_rate=48000, channels=2)],
    )
    prof = CoreProfile(name="t", transforms=[])
    enc = EncoderCandidate(name="libx264", vendor="x264", codec="h264", works=True)
    plan = Plan(
        source=src, profile=prof, encoder=enc,
        plan_hash=compute_plan_hash(src, prof, enc),
    )

    def fake_build_plan(input_path, profile, encoder_override):  # type: ignore[no-untyped-def]
        return plan

    monkeypatch.setattr(
        "yt_uniquifier.web.routes.run.build_plan", fake_build_plan,
    )
    monkeypatch.setattr(
        "yt_uniquifier.web.routes.run.load_profile",
        lambda _p: prof,
    )

    # Run a noop orchestrator so the worker thread finishes promptly.
    def fake_run_full(plan, opts, on_event=None, cancel_token=None, pause_token=None):  # type: ignore[no-untyped-def]
        if on_event is not None:
            on_event(orchestrator.RunEvent(kind="log", payload={"phase": "noop"}))
        return orchestrator.RunSummary(
            output=opts.output, plan=plan, segments_done=0,
            preflight_findings=[],
        )

    monkeypatch.setattr(
        "yt_uniquifier.web.routes.run.run_full", fake_run_full,
    )

    profile_path = profiles / "profile.yaml"
    profile_path.write_text("name: t\ntransforms: []\n", encoding="utf-8")

    client = TestClient(build_app(config))
    r = client.post("/api/run", json={
        "input_path": str(src_path),
        "profile_path": str(profile_path),
    })
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    r2 = client.post(f"/api/run/{run_id}/cancel")
    assert r2.status_code == 200

    # The audit log must contain at least one ``api.run.start`` and
    # one ``api.run.cancel`` line.
    import json as _json
    lines = [
        _json.loads(ln)
        for ln in audit_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    events = [ln["event"] for ln in lines]
    assert "api.run.start" in events
    assert "api.run.cancel" in events
    start = next(ln for ln in lines if ln["event"] == "api.run.start")
    assert start["payload"]["run_id"] == run_id
    assert start["payload"]["input"] == "<PATH>/x.mp4"


def test_audit_is_noop_when_path_unset(
    web_dirs: tuple[Path, Path, Path],
) -> None:
    """No ``audit_log_path`` configured → audit() writes nothing,
    raises nothing.
    """
    from yt_uniquifier.web.audit import audit

    audit(
        "api.run.start",
        principal="alice",
        payload={"run_id": "r1"},
        audit_log_path=None,
    )  # must not raise; nothing on disk to check
