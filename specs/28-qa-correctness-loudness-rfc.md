# RFC draft: explicit QA correctness/loudness and independent quality thresholds

Status: DRAFT — owner approval requested; no public model or CLI changes applied.
Release class: additive MINOR. This does not reopen approved RFC #11/#12.

## Problem

`QAReport.notes` encodes correctness failures in prose; consumers cannot reliably
distinguish an unavailable measurement from a passed gate. VMAF (0–100) and SSIM
(-1–1) need independent opt-in policies. No natural-corpus-derived defaults have
been approved, and a good similarity score must not hide a correctness failure.

`core/qa/vmaf.py::compute` currently turns any numeric VMAF below 1 into an
unavailable score and falls back to SSIM. A valid low score and an invalid scoring
domain are different conditions; magnitude alone cannot distinguish them. The
extended HDR experiment makes this distinction visible, but is not proof that an
SDR VMAF model can judge HDR mastering quality.

## Proposal

Keep every existing field, flag, default and raw/registered metric meaning.
Add nullable `correctness` and `loudness` objects to `QAReport`:

- correctness: status `passed|failed|not_verified`, existing invariant failure codes,
  scope `plan_contract|pair_contract`, and full-decode status/reason;
- loudness: per selected audio stream, integrated LUFS, true peak dBTP, stream index,
  method and status/reason. Silence/nonfinite results serialize as null, never NaN;
- distinct optional VMAF/SSIM minimums, with explicit raw versus registered domain.
  Unavailable requested metrics fail the requested gate, not silently pass;
- correctness has precedence over quality. Loudness does not imply acceptable
  clipping, phase, clicks, speech quality or human listening approval.
- retain numeric zero/near-zero VMAF when the measurement succeeds; determine
  domain validity/backend failure independently, and never let SSIM silently
  substitute for an explicitly requested VMAF minimum.

## Implementation after acceptance

Extend the existing models/report/verdict/CLI, not a second QA system. Reuse final
decode evidence and the existing loudnorm measurement. Keep benchmark-only decoded
frame/sample diagnostics in `tools/` until independently promoted to a public API.
Update schema/CLI snapshots, `docs/api-contracts.md`, `docs/qa_report.md`, changelog
and version together. Test legacy JSON, optional backends, invalid output, silence,
multitrack audio, incompatible scoring domains and independent threshold boundaries.
Include valid 0/0.5 VMAF, failed backend parsing, and a high-SSIM/low-VMAF pair.

## Alternatives

Parsing notes is fragile. Replacing existing fields or changing default verdict
thresholds would break consumers or invent an unqualified production target.

## Migration

Old consumers may ignore new nullable fields; old report JSON remains readable.
No automated release/tag until the maintainer accepts remaining production risks.
