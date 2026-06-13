"""A10 (v0.5.5) regression: every user-facing transform *Params* schema
must reject unknown keys via ``model_config = ConfigDict(extra="forbid")``.

Without this, a YAML profile that contains a typo in a param key
(``brigthness`` for ``brightness``) is silently accepted and the typo'd
value is discarded — the run executes with defaults and the user thinks
their profile setting took effect. With ``extra="forbid"`` the typo is
surfaced at load time as a ``ValidationError``.

This is also security-relevant: future builders may grow the habit of
``getattr(params, key)`` over arbitrary user-supplied keys. The
``extra="forbid"`` guarantees those builders see only the typed schema.

We walk the live transform registry rather than hard-coding the schema
list, so a transform added later that forgets ``extra="forbid"`` is
caught automatically.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from yt_uniquifier.core.transforms import all_ids, get
from yt_uniquifier.core.transforms.audio_loudnorm import LoudnormMeasurement


@pytest.mark.parametrize("transform_id", sorted(all_ids()))
def test_every_transform_params_forbids_extra(transform_id: str) -> None:
    spec = get(transform_id)
    schema = spec.schema

    # The library-internal ``LoudnormMeasurement`` is intentionally NOT
    # ``extra="forbid"`` because it's parsed from ffmpeg's JSON output
    # and tolerating future ffmpeg fields is a feature. We test only
    # user-facing *Params* classes.
    assert schema is not LoudnormMeasurement, (
        "LoudnormMeasurement is an internal struct, not a user Params class"
    )

    # Build a payload with valid defaults plus one unknown key. extra="forbid"
    # must reject it; the absence of extra="forbid" would silently drop
    # the key and validate successfully.
    payload = {**spec.defaults, "this_key_does_not_exist": 42}

    with pytest.raises(ValidationError) as exc_info:
        schema.model_validate(payload)
    # Pydantic 2 reports the unknown field with the "extra_forbidden" type.
    assert any(
        err.get("type") == "extra_forbidden"
        for err in exc_info.value.errors()
    ), (
        f"{transform_id} accepted unknown key — missing "
        "model_config = ConfigDict(extra='forbid')"
    )


def test_loudnorm_measurement_intentionally_tolerates_extra() -> None:
    """LoudnormMeasurement is the documented exception — it must NOT forbid."""
    # ffmpeg loudnorm may add fields in future versions; parsing must
    # not break. We construct from a payload with an unknown key and
    # expect success.
    m = LoudnormMeasurement.model_validate({
        "input_i": -23.0,
        "input_tp": -1.5,
        "input_lra": 7.0,
        "input_thresh": -33.0,
        "target_offset": 0.5,
        "future_ffmpeg_field_xyz": "harmless",
    })
    assert m.input_i == -23.0
