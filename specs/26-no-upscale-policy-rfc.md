# RFC: explicit no-upscale geometry policy

Status: proposed — [GitHub RFC #11](https://github.com/Hostlife22/yt_uniquifier/issues/11)

SemVer classification: MINOR, with an explicit compatibility migration.

## Problem

`video.fit_aspect` always scales toward its configured delivery dimensions. A
640×360 source processed by a 1920×1080 or 3840×2160 profile therefore receives
an expensive upscale that cannot restore detail and may amplify ringing before a
second platform transcode. Preflight reports the condition but cannot prevent it.

The current behaviour is also ambiguous: specifying an aspect ratio implicitly
opts into a resolution increase. Operators need a machine-readable distinction
between an aspect/canvas requirement and permission to enlarge source pixels.

## Proposal

Add the following optional parameter to `FitAspectParams`:

```python
allow_upscale: bool = False
```

The transform must calculate even output dimensions before constructing the
filter graph.

- `allow_upscale=false`: the foreground/source image scale factor must never
  exceed `1.0`. Crop mode selects the largest even target-aspect rectangle that
  fits inside both the decoded source and configured dimension caps, then only
  downscales when necessary. Pad modes keep the foreground at or below source
  resolution and select the smallest even target-aspect canvas that contains it,
  bounded by the configured caps. Padding or a blurred background may add canvas
  pixels but must not claim restored source detail.
- `allow_upscale=true`: preserve the existing exact target-canvas behaviour.
- Preflight remains source-aware and reports the actual foreground scale and
  resolved canvas. An implicit upscale is impossible; an explicitly allowed
  upscale remains informational.
- Dimensions, scale decisions and the new parameter participate in the existing
  canonical profile/plan hash through `TransformConfig.params`.
- Odd/smaller-than-two dimensions and a source that cannot produce a valid even
  target-aspect canvas fail before FFmpeg.

Shipped platform profiles whose names promise a fixed delivery size
(`youtube_4k`, `youtube_1080p`, AV1 equivalents and fixed-canvas vertical/square
profiles) will set `allow_upscale: true` explicitly to preserve their existing
output contract. New/custom profiles that omit the field receive the safe
no-upscale default.

Required verification:

- unit snapshots for crop, black-pad and blur-pad below/equal/above target;
- real FFmpeg 360p→1080p no-upscale and explicit-upscale cases;
- SAR 1:1, even dimensions and requested aspect checks;
- HDR preflight compatibility and profile/plan-hash regressions;
- refreshed shipped-profile contract goldens and documentation.

## Alternatives

1. Keep only the current warning. Rejected because unattended production jobs
   still perform a known wasteful operation.
2. Remove target dimensions from shipped profiles. Rejected because those names
   promise delivery canvases and existing users depend on them.
3. Always fail when a source is smaller. Rejected because explicit fixed-canvas
   delivery is legitimate and should remain available.
4. Infer intent from whether width/height were supplied. Rejected because shipped
   defaults and user overrides become indistinguishable after validation.

## Migration plan

This is additive YAML syntax. Existing fixed-canvas shipped profiles gain an
explicit `allow_upscale: true`, so their rendered dimensions do not change.
Custom profiles that rely on implicit enlargement must add the same parameter.
The release notes and profile docs will include before/after examples. Contract
goldens are regenerated only in the implementation PR after this RFC is accepted.

The implementation must not land until the repository RFC comment window and
maintainer sign-off requirements are satisfied.
