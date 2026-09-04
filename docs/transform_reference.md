# Transform reference and compatibility graph

This page documents the built-in transform registry used for controlled processing
of owned or licensed media. Order means order among transforms of the same media
kind; video and audio chains are built independently. “Quality” describes likely
generation loss, not whether a third-party matching system will accept a file.

## Recommended order

Video: `tonemap_sdr` (when converting HDR) → canvas/geometry → colour → blend or
subtitle composition → noise/sharpen → temporal operations. Audio: pitch/tempo → EQ
and other effects → resample → `loudnorm` last. Preflight rejects combinations that
would produce an ambiguous colour mode, invalid loudness result, A/V desync, or
unsupported stream/container mapping.

## Video transforms

| Transform | Purpose and order | Quality / size | Speed | HDR | VFR and compatibility |
|---|---|---|---|---|---|
| `video.tonemap_sdr` | PQ/HLG → BT.709; must be first video operation | Visible highlight/gamut remap; often smaller 8-bit output | Slow | Input HDR only; incompatible with `keep_hdr=true` | VFR-safe; invalidates raw-source VMAF reference |
| `video.fit_aspect` | Fit a delivery canvas by crop, blur-pad, or black-pad; before micro-crop | Crop loses edges; blur-pad raises bitrate; black pad compresses well | Medium–slow | Supported after tonemap or in HDR geometry path | Timestamp-neutral; combining with `crop_resize` adds a second resample |
| `video.crop_resize` | Small crop followed by scale back to canvas | Slight detail loss; usually raises edge complexity | Medium | Supported; colour transforms are wrapped separately in linear light | VFR-safe; incompatible with raw-source target-VMAF feedback |
| `video.rotate` | Small rotation with filled borders | Interpolation softens detail and can expose corners | Medium | Supported; HDR fill uses legal near-black | VFR-safe; incompatible with raw-source target-VMAF feedback |
| `video.mirror` | Horizontal flip | Lossless in pixel values before encode | Fast | Supported | VFR-safe; semantically destructive for text, logos, and directionality |
| `video.color_eq` | Brightness/contrast/gamma/saturation adjustment | Can clip or band at strong settings; bitrate impact is small | Fast | Linear-light wrapper used when preserving HDR | VFR-safe; combine conservatively with noise |
| `video.blend_b` | Composite a licensed B-roll/video layer | Ghosting and extra motion increase bitrate | Slow; decodes second input | Warning in keep-HDR mode: blend is transfer-domain, not linear-light | VFR inputs are normalized by FFmpeg but differing cadence/duration needs QA |
| `video.subtitles` | Burn an existing SRT/ASS-family subtitle file | Permanent rasterization; small bitrate increase around glyphs | Medium | Apply after tonemap for SDR delivery; HDR styling needs visual QA | VFR-safe; source subtitle streams remain a separate container concern |
| `video.noise` | Add film-grain-like luma/chroma noise | Direct quality/size cost; amplifies later encoding loss | Slow | Linear-light wrapper used when preserving HDR | VFR-safe; noise + sharpen compounds ringing and bitrate |
| `video.subpixel_sharpen` | Mild unsharp mask | May ring edges; often increases bitrate | Medium | Supported, but inspect HDR highlights | VFR-safe; avoid stacking with added noise without measured QA |
| `video.temporal_jitter` | Experimental timestamp-based blackout/drop effect | Destructive flashes/judder; bitrate varies | Medium | Technically supported, visually unqualified for HDR | Timestamp-grid works with VFR; invalidates raw-source VMAF and is excluded from quality-first profiles |
| `video.speed` | Change playback rate with `setpts` | Motion cadence changes; file size follows duration | Fast | Supported | Requires matching `audio.pitch_tempo.tempo`; incompatible with copied extra audio, subtitles, or chapters until they can be retimed |

## Audio transforms

| Transform | Purpose and order | Quality / loudness | Speed | Channel/layout compatibility |
|---|---|---|---|---|
| `audio.pitch_tempo` | Pitch and/or tempo change; early in chain | Resampling can colour transients; Rubber Band is higher quality but slower | Medium–slow | Any main layout supported by FFmpeg; tempo must equal `video.speed.rate` |
| `audio.eq` | Parametric spectral shaping | Strong gain can clip before normalization | Fast | Layout-neutral; place before loudnorm |
| `audio.haas_stereo` | Widen stereo by delaying one channel | Audible phase/mono-compatibility risk | Fast | Stereo only; preflight rejects mono and surround |
| `audio.compand` | Compress dynamic range | Reduces dynamics and changes peaks | Fast | Layout-neutral; measure natural speech/music before production use |
| `audio.spectral_smear` | Subtle chorus/phaser modulation | Audible on sustained tones at stronger settings | Medium | Layout-neutral; do not stack effects blindly |
| `audio.reverb` | Add room/plate echo | Audible and can reduce intelligibility | Fast–medium | Layout-neutral; place before loudnorm |
| `audio.noise_overlay` | Mix generated noise under program audio | Raises noise floor and true peak | Medium | Layout-neutral; place before loudnorm and validate speech/music |
| `audio.resample` | Intermediate sample-rate round trip | Small spectral/transient loss | Fast–medium | Final pipeline output is 48 kHz AAC; place before loudnorm |
| `audio.loudnorm` | Two-pass EBU R128 normalization | Targets loudness/true peak; dynamic fallback is reported | Slow: full measurement pass | Exactly once and final among audio transforms |

## Runtime compatibility graph

The executable inventory is `core/transform_compatibility.py`; specialised media
checks remain in `core/preflight.py`. The table below describes every hard or
quality-sensitive edge across the requested domains.

| Domain | Edge / context | Result |
|---|---|---|
| HDR | `keep_hdr=true` + `video.tonemap_sdr` | Fail: contradictory output colour modes |
| HDR | tonemap not first, tonemap on SDR, or HDR with neither keep nor tonemap | Fail before encoding |
| HDR | keep HDR without 10-bit HEVC capability | Fail; static metadata is fully verified only on libx265 |
| HDR | keep HDR + `video.blend_b` | Warn: blend is not linear-light wrapped |
| Audio | `audio.haas_stereo` + non-stereo main track | Fail |
| Audio | duplicate loudnorm or any audio operation after loudnorm | Fail |
| Audio/temporal | video speed differs from main-audio tempo | Fail: A/V desync |
| Temporal | speed + extra copied audio track | Fail: extra track cannot be retimed |
| Temporal/container | speed + subtitles or chapters | Fail: auxiliary timestamps cannot be retimed safely |
| Quality | target VMAF + geometry, overlay, mirror, temporal, subtitle, or tonemap operation | Fail: raw reference is not registered |
| Container | image subtitle → MP4/MOV | Fail; select MKV |
| Container | unsupported attachments/data or dispositions → MP4/MOV | Fail or warn according to whether data would be lost |
| Plugin | both enabled specs where either declares `incompatible_with` | Fail once per unordered pair |

Warnings are intentionally not auto-corrected: changing transform order or disabling
an effect changes the requested derivative. Run `yt-uniq preflight` and review the
generated FFmpeg graph before a long encode.
