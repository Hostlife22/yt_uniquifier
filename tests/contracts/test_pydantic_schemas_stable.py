"""Lock the JSON schema of every SemVer-stable pydantic model.

If pydantic ever changes the shape of its JSON-schema output
between PATCH releases, that would trip every test in this file at
once — which is actually what we want, because we ship a JSON
schema as part of the contract surface.

The models tested here are the ones re-exported from
``yt_uniquifier.core``. To intentionally evolve any of them,
follow the workflow in ``tests/contracts/__init__.py``.
"""

from __future__ import annotations

import pytest

from tests.contracts._snapshot import snapshot
from yt_uniquifier.core import (
    AudioStream,
    Chapter,
    EncoderCandidate,
    HDRInfo,
    Plan,
    Profile,
    QAReport,
    SegmentationConfig,
    SourceMeta,
    SubtitleStream,
    TransformConfig,
    VideoStream,
)

STABLE_MODELS = [
    HDRInfo,
    VideoStream,
    AudioStream,
    SubtitleStream,
    Chapter,
    SourceMeta,
    EncoderCandidate,
    TransformConfig,
    SegmentationConfig,
    Profile,
    Plan,
    QAReport,
]


@pytest.mark.parametrize("model", STABLE_MODELS, ids=lambda m: m.__name__)
def test_model_json_schema_is_stable(model: type) -> None:
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    snapshot(f"schemas/{model.__name__}.json", schema)
