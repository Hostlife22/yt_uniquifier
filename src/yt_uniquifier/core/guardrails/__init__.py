"""v1.3.0 — content guardrails.

Ethics gates that fire before encoding:

  * ``watermark`` (Task 30) — detect broadcaster bugs / station IDs in
    sampled frames; default ``error`` severity so the operator
    explicitly acknowledges they own / have licensed the source.
  * ``drm`` (Task 31) — refuse encrypted MP4 / Matroska sources outright
    so DRM-stripping is impossible by mistake.

The guardrails live alongside but separate from the YouTube-target
preflight checks (codec compatibility, container, audio loudness)
because the rejection semantics are different: a preflight failure
asks the operator to fix the profile; a guardrail failure asks the
operator to attest that the use is legitimate.
"""

from __future__ import annotations
