"""Optional full-stream measurements, never a listening or normalization verdict."""

from __future__ import annotations

import math
from pathlib import Path

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.models import QAAudioLoudness, QALoudness, SourceMeta
from yt_uniquifier.core.redaction import redact_text
from yt_uniquifier.core.runner import CancelToken
from yt_uniquifier.core.transforms.audio_loudnorm import measure


def measure_output(
    output: Path, metadata: SourceMeta, *, cancel_token: CancelToken | None = None,
) -> QALoudness:
    token = cancel_token or CancelToken()
    streams: list[QAAudioLoudness] = []
    for stream in metadata.audio:
        if token.is_cancelled():
            raise PipelineError("QA loudness cancelled by user")
        try:
            result = measure(
                output, pre_filter_complex=f"[0:{stream.index}]anull[qa_audio]",
                pre_output_label="qa_audio", cancel_token=token,
            )
        except (PipelineError, OSError, ValueError, KeyError) as exc:
            if token.is_cancelled():
                raise PipelineError("QA loudness cancelled by user") from exc
            streams.append(QAAudioLoudness(
                stream_index=stream.index, status="not_verified",
                note=redact_text(str(exc), all_absolute_paths=True),
            ))
            continue
        integrated = result.input_i if math.isfinite(result.input_i) else None
        peak = result.input_tp if math.isfinite(result.input_tp) else None
        invalid = any(math.isnan(v) or v == math.inf for v in (result.input_i, result.input_tp))
        streams.append(QAAudioLoudness(
            stream_index=stream.index, status="not_verified" if invalid else "passed",
            integrated_lufs=integrated,
            true_peak_dbtp=peak,
            note="Backend returned an invalid nonfinite measurement." if invalid else (
                "Undefined loudness/peak (e.g. silence) encoded as null."
                if integrated is None or peak is None else None
            ),
        ))
    return QALoudness(
        status="passed" if all(s.status == "passed" for s in streams) else "not_verified",
        streams=streams,
        note="No output audio streams." if not streams else
        "Measurement status only; no loudness target or human listening verdict.",
    )
