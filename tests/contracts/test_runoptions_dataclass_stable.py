"""Lock the dataclass shape of ``RunOptions``, ``RunSummary``, ``RunResult``.

These are the three dataclasses that callers of ``run_full`` see —
``RunOptions`` flows in, ``RunSummary`` flows out, ``RunResult`` is
what ``runner.run_ffmpeg`` returns one level lower. They are
public surface per ``docs/api-contracts.md``.

We only snapshot the *shape* (field names + repr of declared type +
whether a default is present). The default *value* itself is not
locked because a default tweak (e.g. ``target_segment_sec=600`` →
``900``) is intentionally allowed within a PATCH if it is a pure
behaviour tune.
"""

from __future__ import annotations

import dataclasses

from tests.contracts._snapshot import snapshot
from yt_uniquifier.core import RunOptions, RunResult, RunSummary

LOCKED_DATACLASSES = [RunOptions, RunSummary, RunResult]


def _shape(dc: type) -> list[dict[str, object]]:
    return [
        {
            "name": f.name,
            "type": str(f.type),
            "has_default": f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING,
        }
        for f in dataclasses.fields(dc)
    ]


def test_runoptions_shape_is_stable() -> None:
    snapshot("dataclasses/RunOptions.json", _shape(RunOptions))


def test_runsummary_shape_is_stable() -> None:
    snapshot("dataclasses/RunSummary.json", _shape(RunSummary))


def test_runresult_shape_is_stable() -> None:
    snapshot("dataclasses/RunResult.json", _shape(RunResult))
