# Filter graph

The pipeline composes transforms linearly into a single `-filter_complex`
graph for each ffmpeg invocation. There's exactly one Python-side stage per
input (no `bgr24` round-tripping through `subprocess.stdin`).

## Building blocks

```
TransformSpec(id, kind="video"|"audio", schema, build)
  ↓ build(params, alloc, in_label) ↓
FilterChain(in_label, out_label, filter_str, extra_inputs)
```

`LabelAllocator` hands out unique labels (`v1`, `v2`, …, `a1`, `a2`, …) so the
pipeline can splice fragments together without coordination between transforms.

## Composition for a typical profile (medium)

```
[0:v:0]
   crop=iw*0.9892:ih*0.9871:iw*0.0011:ih*0.0061,
   scale=iw/0.9892:ih/0.9871:flags=lanczos                              [v1]
[v1]
   eq=brightness=0.012:contrast=1.018:gamma=0.995:saturation=1.03       [v2]
[v2]
   noise=alls=4:allf=t+u                                                [v3]
[v3]
   scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p                    [vout]

[0:a:0]
   asetrate=48000*1.000800,aresample=48000,atempo=0.999201             [a1]
[a1]
   equalizer=f=120.0:t=q:w=1.0:g=-0.6,
   equalizer=f=4500.0:t=q:w=1.0:g=0.4                                   [a2]
[a2]
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
  -metadata encoder=yt-uniquifier/0.1.0a0 \
  output.mp4
```

## Even-dimensions guard

The tail always inserts `scale=trunc(iw/2)*2:trunc(ih/2)*2` before
`format=yuv420p`. `libx264` rejects odd widths/heights; micro-crop can leave a
319×180 frame that we round down to 318×180 here.

## The `video.blend_b` special case

`video.blend_b` is the only transform that needs an extra input. Its
`FilterChain.extra_inputs` lists the secondary file. The pipeline:

1. Appends `-i B.mp4` to the ffmpeg argv (input index 1).
2. Rewrites the placeholder token `__B__` in `filter_str` to `[1:v]`.

```
[0:v:0][1:v]scale2ref=w=iw:h=ih[b_scaled][a_ref];
[a_ref][b_scaled]blend=all_expr='A*0.97+B*0.03'                       [v_out]
```

## Segment mode vs full mode

For long inputs `core/segmenter.py` calls `build_video_segment_command`
(video transforms only, audio = stream copy) once per segment, and
`build_main_audio_command` once on the full source (audio transforms only,
two-pass loudnorm cached). The final mux uses the concat demuxer and stream
copy, so the seams introduce zero re-encoding noise.

## Quality args per encoder

| Vendor | Quality args |
|--------|--------------|
| `nvenc` | `-preset p6 -rc vbr -cq 19 -b:v 0 -maxrate ~1.25× -bufsize 2×maxrate` |
| `qsv` | `-global_quality 19 -look_ahead 1` |
| `amf` | `-rc cqp -qp_i 19 -qp_p 19` |
| `videotoolbox` | `-q:v 50` |
| `x264` / `x265` | `-preset slow -crf 18` |
