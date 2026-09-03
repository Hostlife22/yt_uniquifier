# RFC: raw and registered QA metrics

Status: proposed — [GitHub RFC #12](https://github.com/Hostlife22/yt_uniquifier/issues/12)

SemVer classification: MINOR (additive QA fields and CLI options).

## Problem

Current VMAF/SSIM compare an encoded derivative to the untransformed source after
only resizing the reference. Legitimate crop, mirror, timing and tonemap changes
can dominate the score, so it does not isolate encoder quality. SSCD pairs equal
grid indices without reporting temporal coverage, and ordered Chromaprint Hamming
has no bounded drift alignment. A low raw metric can therefore mean either an
intentional edit, a registration error, or real generational damage.

Changing the meaning of existing fields would make historical reports and
thresholds incomparable.

## Proposal

Keep every existing raw metric unchanged and add optional registered diagnostics
to `QAReport`:

```text
vmaf_registered_mean: float | null
ssim_registered_mean: float | null
sscd_registered_mean: float | null
audio_fp_registered_hamming_per_frame: float | null
registration: object | null
```

`registration` records the reference mode, bounded offset/drift, compared sample
count, coverage ratio, confidence, and a note when registration is unavailable.
Exact nested schema and numeric bounds will be locked in the implementation PR.

- Plan-aware run/batch QA builds a temporary transformed reference using the
  existing video transform graph, exact run/segment seeds and lossless FFV1. It
  must not create a second pipeline or reimplement transforms.
- VMAF/SSIM compare output against that transformed reference with local PTS reset.
- SSCD uses a bounded monotonic alignment over cached embeddings; matches cannot
  move backward or reuse an output frame. Coverage and displacement are reported.
- Audio fingerprints first find a bounded global offset, then compute ordered
  per-window Hamming on the overlapping region. Low-overlap candidates are
  rejected rather than rewarded.
- Standalone `yt-uniq qa` continues producing raw metrics unless enough plan/profile
  provenance is supplied by an additive option. It must never guess transform
  parameters or seeds.
- Verdict remains based on existing correctness/raw quality fields until an
  owned/licensed natural-content corpus establishes registered thresholds.
- Reference frames/embeddings are cached by source content digest, canonical
  transform plan, seed, tool/FFmpeg/model version and sampling grid.

Required verification:

- identity, fixed offset, variable drift, crop, mirror, speed and frame-drop cases;
- adversarial low-overlap sequences cannot obtain high confidence;
- raw fields are byte-for-byte unchanged for existing fixtures;
- registered identity approaches the expected metric maximum;
- cancellation, cache corruption/invalidation and long-form bounded-resource tests;
- contract snapshots, JSON/HTML rendering and CLI documentation.

## Alternatives

1. Replace existing raw fields in place. Rejected because it silently invalidates
   saved reports and thresholds.
2. Treat pHash/SSCD as quality substitutes. Rejected because representation
   similarity and perceptual quality answer different engineering questions.
3. Apply only spatial resize. Rejected because it does not register crop, mirror,
   retiming or per-segment stochastic transforms.
4. Unbounded DTW. Rejected because it can manufacture a high score from unrelated
   regions and has poor long-form resource bounds.

## Migration plan

All new report fields are optional and default to `null`; existing JSON consumers
remain valid. New CLI options are additive. HTML shows raw and registered metrics
in separate labelled sections. Existing thresholds and verdict semantics remain
unchanged for the first release. Promotion of registered values into a production
gate requires a later corpus-backed RFC.

The implementation must not land until the repository RFC comment window and
maintainer sign-off requirements are satisfied.
