"""An optional post-mux pass must not replace a previous good publication on failure."""

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import needs_ffmpeg
from yt_uniquifier.core import sanitizer
from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import Profile
from yt_uniquifier.core.orchestrator import RunOptions, build_plan, run_full


@pytest.mark.integration
@needs_ffmpeg
def test_sanitization_failure_preserves_previous_publication(
    tiny_clip: Path, tmp_path: Path, isolated_cache: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "published.mp4"
    output.write_bytes(b"previous-publication")
    plan = build_plan(
        tiny_clip, Profile(name="staged-sanitize", skip_watermark_check=True),
        encoder_override="libx264",
    )
    monkeypatch.setattr(sanitizer, "needs_sanitization", lambda encoder: True)

    def fail(source: Path, destination: Path, **kwargs: Any) -> None:
        assert source == destination
        assert destination != output
        assert destination.stat().st_size > 0
        assert output.read_bytes() == b"previous-publication"
        raise PipelineError("injected sanitizer failure")

    monkeypatch.setattr(sanitizer, "sanitize_bitstream", fail)
    with pytest.raises(PipelineError, match="injected sanitizer failure"):
        run_full(plan, RunOptions(
            work_dir=tmp_path / "work", output=output, sanitize_bitstream=True,
        ))
    assert output.read_bytes() == b"previous-publication"
    assert not list(tmp_path.glob(".published.*.part.mp4"))
