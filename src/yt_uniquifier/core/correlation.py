"""Stable correlation identifiers for one orchestration tree.

The public ``RunEvent`` shape remains unchanged: identifiers are additive
payload fields so existing CLI, GUI, and web consumers continue to work.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from yt_uniquifier.core.runner import RunEvent


@dataclass(frozen=True)
class CorrelationIds:
    """Identifiers shared by a run, its immutable plan, and its encode job."""

    run_id: str
    plan_id: str
    job_id: str

    @classmethod
    def for_run(cls, run_id: str, plan_hash: str) -> CorrelationIds:
        # A job is one execution of a plan inside a run. Deriving it keeps event
        # replay and resume deterministic without expanding the RunOptions API.
        digest = hashlib.sha256(
            f"{run_id}\0{plan_hash}\0encode".encode()
        ).hexdigest()[:24]
        return cls(run_id=run_id, plan_id=plan_hash, job_id=digest)

    def segment_id(self, index: int) -> str:
        """Return the deterministic child ID for a zero-based segment index."""
        return f"{self.job_id}:{index:06d}"

    def enrich(self, event: RunEvent) -> RunEvent:
        """Return an event carrying the complete available correlation chain."""
        payload = {
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            # Keep the established key while introducing the explicit plan ID.
            "plan_hash": self.plan_id,
            "job_id": self.job_id,
            **event.payload,
        }
        segment = payload.get("segment")
        if isinstance(segment, int) and not isinstance(segment, bool):
            payload.setdefault("segment_id", self.segment_id(segment))
        return RunEvent(kind=event.kind, payload=payload)


__all__ = ["CorrelationIds"]
