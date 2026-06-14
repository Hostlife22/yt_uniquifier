"""Lock the literal members of ``EventKind``.

A ``RunEvent.kind`` is a wire-format identifier: third-party GUI
consumers, telemetry pipelines, and webhook handlers match on the
exact string. Adding a kind is MINOR; removing or renaming one is
MAJOR and requires an RFC per ``docs/versioning.md``.

We also snapshot the structure of the ``RunEvent`` dataclass
itself so a refactor that, say, renamed ``payload`` to ``data``
would trip the test.
"""

from __future__ import annotations

import dataclasses
from typing import get_args

from tests.contracts._snapshot import snapshot
from yt_uniquifier.core import EventKind, RunEvent


def test_eventkind_literal_members_are_stable() -> None:
    members = list(get_args(EventKind))
    snapshot("runevent_kinds.json", members)


def test_runevent_dataclass_shape_is_stable() -> None:
    assert dataclasses.is_dataclass(RunEvent), \
        "RunEvent must remain a dataclass (frozen=True)"
    fields_summary = [
        {
            "name": f.name,
            "type": str(f.type),
            "has_default": f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING,
        }
        for f in dataclasses.fields(RunEvent)
    ]
    snapshot("runevent_dataclass.json", fields_summary)
