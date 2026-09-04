# YouTube targets

`yt-uniq preflight` checks a source + profile against YouTube's recommended
upload encoding settings before the run, and against HDR sanity.

## What the matrix checks

| Code | Severity | Triggered when |
|------|----------|----------------|
| `container.ok` / `container.unsupported` | ok / warn | Source container not in `{mp4, mov, mkv}` |
| `video.codec.ok` / `video.codec.unusual` | ok / warn | Source video codec outside `{h264, hevc, vp9, av1}` |
| `video.missing` | fail | No video stream in source |
| `audio.missing` | warn | No audio stream — output will be video-only |
| `audio.codec.unusual` | warn | Source audio codec outside `{aac, opus, mp3}` (will be re-encoded to AAC) |
| `audio.sr.bad` | warn | Sample rate ≠ 44.1k / 48k (YouTube will resample) |
| `fps.ok` / `fps.unusual` | ok / warn | FPS not within ±0.1 of `{23.976, 24, 25, 29.97, 30, 50, 59.94, 60}` |
| `subs.image_based` | warn | PGS/DVB subtitles present — cannot copy into mp4, will be dropped |
| `hdr.color.transforms` | **fail** | HDR source + profile has color/eq/noise transforms without `keep_hdr=true` **and** without `video.tonemap_sdr` enabled (either keep HDR through a zscale wrap or collapse to SDR via tonemap) |
| `hdr.unsupported.encoder` | **fail** | HDR source + encoder is libx264 (no 10-bit profile) and the profile keeps HDR (workaround: enable `video.tonemap_sdr` for SDR output, or pick `libx265`/HEVC encoder) |
| `loudnorm.ok` / `loudnorm.missing` | ok / warn | Profile lacks `audio.loudnorm` — output won't hit -14 LUFS |
| `bitrate.over` | warn | Projected output bitrate > YouTube ceiling for the resolution |

## H.264 output structure

The qualified `libx264` path explicitly emits YouTube's documented upload structure:

- progressive 4:2:0 output from the existing video pipeline;
- High Profile and CABAC entropy coding;
- at most two consecutive B-frames;
- closed GOP with a maximum size of half the native frame rate (`12` frames at
  23.976/24 FPS, `13` at 25 FPS, `15` at 29.97/30 FPS and `30` at 59.94/60 FPS).

The actual bitstream regression checks profile, B-frame runs, IDR cadence and CABAC.
Changing this internal encoder policy increments the plan-hash policy revision, so
resume cannot combine segments encoded under different GOP rules.

H.264 VideoToolbox requests the same generic structure and is verified for High,
CABAC, closed half-FPS IDR cadence and bounded B-frame reordering. FFmpeg exposes
Apple's frame-reordering switch through any positive `-bf` value rather than a
reliable exact count: qualified Intel hardware emits runs of one B-frame and GitHub's
Apple Silicon runner emits runs of three. It is therefore not claimed as
byte-for-byte or B-pattern equivalent to libx264. NVENC, QSV and AMF remain
**NOT VERIFIED**. Accepting `-g`/`-bf` arguments is insufficient: each backend must
prove its output on matching physical hardware.

## HEVC and AV1 random access

YouTube's documented half-frame-rate upload GOP applies specifically to H.264. The
project does not present that rule as an HEVC/AV1 upload requirement. Instead, HEVC
and AV1 use an internal two-second maximum GOP to make seeking, segment recovery and
downstream inspection predictable:

- libx265 and HEVC VideoToolbox emit closed IDR GOPs;
- SDR HEVC VideoToolbox explicitly uses Main/8-bit and HDR uses Main10/10-bit;
- SVT-AV1 and libaom-AV1 emit Main-profile 4:2:0 output with the same GOP bound;
- libaom constant-quality mode uses `-b:v 0` without incompatible VBV options.

These paths are bitstream-tested on the qualified Intel Mac; the three software paths
also pass in the Debian 12 production container. NVENC/QSV/AMF and AV1 VideoToolbox
remain hardware-qualification items.

## Bitrate ceilings (h264)

| Max height | Ceiling |
|------------|---------|
| ≤ 1080p | 12 Mbps |
| ≤ 1440p | 24 Mbps |
| > 1440p (e.g. 2160p) | 68 Mbps |

The check compares against `source.video[0].bit_rate × 1.25` since the encoder
allocates ~25% headroom above source.

## Loudness target

Default target is **-14.0 LUFS Integrated** (YouTube content loudness).
Set in `Profile.target_loudness_lufs`; `audio.loudnorm` honours it.

## When fail is returned

`yt-uniq run` aborts before any encoding when preflight returns a finding with
`severity=fail`. Override with `--no-preflight` (warnings only) if you know
what you're doing.

`yt-uniq preflight` exits with code 1 on fail (so it's CI-friendly).

## References

- [YouTube — recommended upload encoding settings](https://support.google.com/youtube/answer/4603579)
- [YouTube — recommended bitrates](https://support.google.com/youtube/answer/1722171)
- [YouTube — content loudness target](https://support.google.com/youtube/answer/13197387) (-14 LUFS Integrated, -1 dB True Peak)
