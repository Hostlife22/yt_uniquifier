# Filter graph

The pipeline composes transforms linearly into a single `-filter_complex`
graph for each ffmpeg invocation. There's exactly one Python-side stage per
input (no `bgr24` round-tripping through `subprocess.stdin`).

## Building blocks

```
TransformSpec(id, kind="video"|"audio", schema, build)
  ↓ build(params, alloc, in_label, rng=...) ↓
FilterChain(in_label, out_label, filter_str, extra_inputs)
```

`LabelAllocator` hands out unique labels (`v1`, `v2`, …, `a1`, `a2`, …) so the
pipeline can splice fragments together without coordination between transforms.

Builders that opt into per-run variability take an optional `rng=random.Random`
argument seeded from `Plan.run_seed`. Same seed → identical filter graph.

## Registered transforms (18)

| ID | Kind | Notes |
|---|---|---|
| `video.crop_resize` | video | micro-crop + lanczos rescale |
| `video.rotate` | video | 0.05–0.5° rotation with edge fill |
| `video.color_eq` | video | brightness/contrast/gamma/saturation jitter |
| `video.noise` | video | film-grain via `noise=alls=N:allf=t+u` |
| `video.mirror` | video | horizontal flip (opt-in only) |
| `video.blend_b` | video | frame-blend with a second `-i B.mp4` |
| `video.speed` | video | `setpts=PTS/rate` |
| `video.temporal_jitter` | video | per-period blackout + drop on rng-randomized phase (Fojcik 2025) |
| `video.tonemap_sdr` | video | zscale linearize → tonemap (hable/reinhard/mobius/aces) → SDR |
| `audio.pitch_tempo` | audio | `rubberband` (formant-preserving) or `asetrate`+`atempo` cascade |
| `audio.eq` | audio | parametric `equalizer` bands |
| `audio.resample` | audio | `aresample` to and from an unusual intermediate SR |
| `audio.compand` | audio | dynamic-range jitter |
| `audio.reverb` | audio | `aecho` presets (small_room / medium_room / hall / plate) |
| `audio.spectral_smear` | audio | mild `chorus` for chromaprint shift |
| `audio.haas_stereo` | audio | `adelay=0|N` mono-compatible stereo widener (Smitelli 2010) |
| `audio.noise_overlay` | audio | parametric pink/white/brown noise via `anull` + `anoisesrc` + `amix` |
| `audio.loudnorm` | audio | two-pass EBU R128; first pass cached in `state.json` |

## Composition for a typical profile (cid_aware)

```
[0:v:0]
   crop=iw*0.96:ih*0.96:iw*0.02:ih*0.02,
   scale=iw/0.96:ih/0.96:flags=lanczos                                  [v1]
[v1]
   eq=brightness=0.015:contrast=1.022:gamma=0.99:saturation=1.04        [v2]
[v2]
   noise=alls=5:allf=t+u                                                 [v3]
[v3]
   geq=lum='if(eq(mod(N\,30)\,8)\,128\,p(X\,Y))':
       cb='if(eq(mod(N\,30)\,8)\,128\,p(X\,Y))':
       cr='if(eq(mod(N\,30)\,8)\,128\,p(X\,Y))',
   select='not(eq(mod(n\,50)\,17))'                                     [v4]
[v4]
   scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p                    [vout]

[0:a:0]
   rubberband=pitch=1.058:tempo=1.000                                   [a1]
[a1]
   equalizer=f=120:t=q:w=1.0:g=-0.6,
   equalizer=f=4500:t=q:w=1.0:g=0.4                                     [a2]
[a2]
   adelay=0|17                                                          [a3]
[a3]
   aresample=47999,aresample=48000                                      [a4]
[a4]
   compand=attacks=0.05:decays=0.5:points=-80/-80|-20.7/-8.3|0/-3       [a5]
[a5]
   loudnorm=I=-14.0:TP=-1.5:LRA=11.0:
   measured_I=-21.8:measured_TP=-17.69:measured_LRA=0.1:
   measured_thresh=-31.8:offset=-0.0:linear=true:print_format=summary  [aout]
```

Then:

```bash
ffmpeg -hide_banner -y \
  -i input.mp4 \
  -filter_complex "<above>" \
  -map "[vout]" -map "[aout]" \
  -map "0:a:1?" -c:a:1 copy \
  -map "0:s?" -c:s copy \
  -map_chapters -1 \
  -c:v libx264 -preset slow -crf 18 \
  -c:a:0 aac -b:a:0 256k \
  -movflags +faststart \
  -map_metadata -1 \
  output.mp4
```

## Even-dimensions guard

The tail always inserts `scale=trunc(iw/2)*2:trunc(ih/2)*2` before
`format=yuv420p`. `libx264` rejects odd widths/heights; micro-crop can leave
a 319×180 frame that we round down to 318×180 here.

## HDR linear-light wrap

When the source is HDR (PQ / HLG) and `Profile.keep_hdr=true`, the pipeline
wraps every *color-domain* transform (currently `video.color_eq` +
`video.noise`) in a zscale roundtrip via `core/transforms/hdr_wrap.py`:

```
zscale=transfer=linear:npl=100,
  eq=brightness=0.012:contrast=1.018:gamma=0.995:saturation=1.03,
  noise=alls=4:allf=t+u,
zscale=transfer=smpte2084:npl=100
```

Linear-light is required so a "+1 % brightness" means the same thing at the
top and bottom of the PQ curve. Geometry transforms (crop / rescale / rotate /
speed) and temporal ones (temporal_jitter) operate in any domain and are not
wrapped.

If the profile chooses `video.tonemap_sdr` (HDR → SDR) the wrap is skipped:
the tonemap collapses the HDR transfer at the head of the chain, and the rest
of the graph operates in plain BT.709 SDR. Output `pix_fmt` is always
`yuv420p` in that case.

## The `video.blend_b` special case

`video.blend_b` is the only transform that needs an extra input. Its
`FilterChain.extra_inputs` lists the secondary file. The pipeline:

1. Appends `-i B.mp4` to the ffmpeg argv (input index 1).
2. Rewrites the placeholder token `__B__` in `filter_str` to `[1:v]`.

```
[0:v:0][1:v]scale2ref=w=iw:h=ih[b_scaled][a_ref];
[a_ref][b_scaled]blend=all_expr='A*0.97+B*0.03'                       [v_out]
```

## The `audio.noise_overlay` side-chain

`anoisesrc` is a *source* filter (0 inputs, 1 output) so it cannot sit in
the linear chain. The transform emits three sub-chains within its
`filter_str`:

```
anull[main];
anoisesrc=c=pink:r=48000:amplitude=1[noise];
[main][noise]amix=inputs=2:weights=0.75 0.25:duration=first
```

The leading `anull` consumes the pipeline-supplied input label and produces
a labelable `[main]` node that the final `amix` can pair with `[noise]`.

## Segment mode vs full mode

For long inputs `core/segmenter.py` calls `build_video_segment_command`
(video transforms only, audio = stream copy) once per segment, and
`build_main_audio_command` once on the full source (audio transforms only,
two-pass loudnorm cached). The final mux uses the concat demuxer and stream
copy, so the seams introduce zero re-encoding noise.

Under `seed_strategy: divergent`, `segmenter._plan_for_segment` substitutes
a per-segment-derived seed before calling `build_video_segment_command`,
so each segment's stochastic transforms (crop phase, jitter offset, noise
seed, etc.) come out distinct.

## Quality args per encoder

| Vendor | Quality args |
|--------|--------------|
| `nvenc` | `-preset p6 -rc vbr -cq 19 -b:v 0 -maxrate ~1.25× -bufsize 2×maxrate` |
| `qsv` | `-global_quality 19 -look_ahead 1` |
| `amf` | `-rc cqp -qp_i 19 -qp_p 19` |
| `videotoolbox` | `-q:v 50` |
| `x264` / `x265` | `-preset slow -crf 18` |

HDR keep: pipeline picks `yuv420p10le` instead of `yuv420p` when the source
is HDR, `keep_hdr=true`, and the encoder advertises 10-bit support
(`hevc_nvenc`, `hevc_qsv`, `hevc_videotoolbox`, `libx265`). `libx264` does
not support 10-bit profiles; preflight fails before the run starts.
