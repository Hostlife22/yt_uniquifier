"""Regression coverage for production observability and compatibility rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from yt_uniquifier.core.correlation import CorrelationIds
from yt_uniquifier.core.models import (
    EncoderCandidate,
    Plan,
    Profile,
    SourceMeta,
    TransformConfig,
)
from yt_uniquifier.core.redaction import REDACTED, redact_mapping, redact_path
from yt_uniquifier.core.runner import RunEvent
from yt_uniquifier.core.transform_compatibility import (
    COMPATIBILITY_GRAPH,
    evaluate_transform_compatibility,
)
from yt_uniquifier.core.transforms import all_ids, get


def _plan(*transforms: TransformConfig, keep_hdr: bool = False) -> Plan:
    profile = Profile(
        name="compat-test",
        transforms=list(transforms),
        keep_hdr=keep_hdr,
    )
    return Plan(
        source=SourceMeta(
            path=Path("source.mp4"),
            container="mp4",
            duration_sec=1.0,
            size_bytes=1,
        ),
        profile=profile,
        encoder=EncoderCandidate(
            name="libx264", vendor="x264", codec="h264", works=True,
        ),
        plan_hash="ab" * 32,
    )


def test_correlation_chain_adds_segment_child_without_mutating_event() -> None:
    correlation = CorrelationIds.for_run("run-1", "plan-1")
    original = RunEvent(kind="progress", payload={"segment": 7, "frame": "2"})
    enriched = correlation.enrich(original)

    assert original.payload == {"segment": 7, "frame": "2"}
    assert enriched.payload["run_id"] == "run-1"
    assert enriched.payload["plan_id"] == "plan-1"
    assert enriched.payload["plan_hash"] == "plan-1"
    assert enriched.payload["job_id"] == correlation.job_id
    assert enriched.payload["segment_id"] == f"{correlation.job_id}:000007"

    forged = correlation.enrich(RunEvent(kind="log", payload={
        "run_id": "wrong", "plan_id": "wrong", "job_id": "wrong",
        "segment": 2, "segment_id": "wrong",
    }))
    # Preserve the established additive-event contract: a callee that already
    # supplied correlation fields remains authoritative.
    assert forged.payload["run_id"] == "wrong"
    assert forged.payload["plan_id"] == "wrong"
    assert forged.payload["job_id"] == "wrong"
    assert forged.payload["segment_id"] == "wrong"


def test_compatibility_rejects_conflicting_hdr_modes() -> None:
    plan = _plan(
        TransformConfig(id="video.tonemap_sdr", enabled=True),
        keep_hdr=True,
    )
    assert {issue.code for issue in evaluate_transform_compatibility(plan)} == {
        "hdr.mode.conflict",
    }


def test_compatibility_rejects_audio_after_loudnorm_and_duplicate() -> None:
    ordered_wrong = _plan(
        TransformConfig(id="audio.loudnorm", enabled=True),
        TransformConfig(id="audio.eq", enabled=True),
    )
    assert "audio.loudnorm.order" in {
        issue.code for issue in evaluate_transform_compatibility(ordered_wrong)
    }

    duplicate = _plan(
        TransformConfig(id="audio.loudnorm", enabled=True),
        TransformConfig(id="audio.loudnorm", enabled=True),
    )
    assert "audio.loudnorm.duplicate" in {
        issue.code for issue in evaluate_transform_compatibility(duplicate)
    }


def test_compatibility_graph_covers_requested_domains() -> None:
    assert {rule.domain for rule in COMPATIBILITY_GRAPH} >= {
        "hdr", "audio", "temporal", "container",
    }
    assert {rule.code for rule in COMPATIBILITY_GRAPH} >= {
        "hdr.output_policy.missing",
        "hdr.dynamic_metadata.unsupported",
        "hdr.static_metadata.encoder_unverified",
        "audio.haas_requires_stereo",
        "audio.loudnorm.order",
        "timeline.rate_mismatch",
        "timeline.aux_stream_rate",
        "subs.image_based",
        "aux.attachments.unsupported",
        "aux.data.unsupported",
        "aux.attached_pic.unsupported",
    }


def test_observability_redacts_nested_tokens_and_absolute_paths() -> None:
    fake_token = "ghp_" + ("a" * 32)
    source = {
        "input_path": "/srv/private/licensed-master.mkv",
        "nested": {
            "authorization": "Bearer top-secret-token",
            "message": "request token=abc123 failed",
            "unknown_field": "/var/private/customer/source.mov",
            "provider_error": f"rejected {fake_token}",
            "trace": 'File "/srv/app/private/worker.py", line 12',
        },
    }
    redacted = redact_mapping(source, all_absolute_paths=True)
    assert redacted["input_path"] == "<PATH>/licensed-master.mkv"
    assert redacted["nested"]["authorization"] == REDACTED
    assert "abc123" not in redacted["nested"]["message"]
    assert redacted["nested"]["unknown_field"] == "<PATH>/source.mov"
    assert "ghp_" not in redacted["nested"]["provider_error"]
    assert "/srv/app/private" not in redacted["nested"]["trace"]


@pytest.mark.parametrize(
    "path",
    [
        "/srv/private/licensed-master.mkv",
        "C:/Users/customer/licensed-master.mkv",
        r"C:\Users\customer\licensed-master.mkv",
        r"\\server\licensed\licensed-master.mkv",
    ],
)
def test_absolute_path_redaction_is_independent_of_host_os(path: str) -> None:
    assert redact_path(path, all_absolute=True) == "<PATH>/licensed-master.mkv"


def test_metrics_have_bounded_states_and_no_correlation_labels() -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    from yt_uniquifier.web import metrics

    before = metrics.RUN_STATE_EVENTS_TOTAL.labels(state="resumed")._value.get()
    metrics.update_from_event(RunEvent(
        kind="log",
        payload={
            "phase": "plan",
            "resumed": True,
            "run_id": "/secret/run-token",
        },
    ))
    after = metrics.RUN_STATE_EVENTS_TOTAL.labels(state="resumed")._value.get()
    assert after == before + 1

    exposition = prometheus_client.generate_latest().decode()
    for state in metrics.RUN_STATES:
        assert f'state="{state}"' in exposition
    assert "/secret/run-token" not in exposition
    assert "run_id=" not in exposition

    metrics.update_from_event(RunEvent(
        kind="error",
        payload={"encoder": "ghp_" + ("a" * 32)},
    ))
    assert "ghp_" not in prometheus_client.generate_latest().decode()


def test_transform_reference_lists_every_registered_transform() -> None:
    reference = (
        Path(__file__).resolve().parents[2] / "docs" / "transform_reference.md"
    ).read_text(encoding="utf-8")
    builtins = [
        transform_id
        for transform_id in all_ids()
        if get(transform_id).build.__module__.startswith(
            "yt_uniquifier.core.transforms.",
        )
    ]
    missing = [
        transform_id
        for transform_id in builtins
        if f"`{transform_id}`" not in reference
    ]
    assert missing == []
