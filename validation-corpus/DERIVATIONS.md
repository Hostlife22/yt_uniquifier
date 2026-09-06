# Open-corpus derivations

These local-only fixtures are derived from the pinned sources in
`open-sources.yaml`. They are engineering inputs, not camera-native HDR masters.

## SDR dialogue

`sdr-dialogue-60s.webm` is a stream-copy of the first 60 seconds of the official
Tears of Steel 1080p derivative. SHA-256:
`0d43a23dd8c6932dbd8bbcf75b31f106b9ed58e458bf2f3354c28002b6e4f469`.

```bash
ffmpeg -ss 0 -t 60 -i media/tears-of-steel-1080p.webm \
  -map 0:v:0 -map 0:a:0 -c copy -avoid_negative_ts make_zero \
  media/sdr-dialogue-60s.webm
```

## HDR10 dark/highlight

`hdr10-dark-highlight-30s.mp4` uses 30 seconds beginning at 00:02:00 of the
publisher-labelled P3/PQ Meridian asset. The untagged 8-bit delivery is decoded
with explicit P3/PQ/BT.709-matrix assumptions, converted to BT.2020/PQ, scaled to
1080p and encoded as 10-bit HEVC. SHA-256:
`4a41abfd474f9330a49d4ab770c786c11d75f08f81f99e93c8263a4758889279`.

```bash
ffmpeg -ss 120 -t 30 -i media/meridian-uhd-hdr-p3pq.mp4 \
  -vf 'zscale=pin=smpte432:tin=smpte2084:min=bt709:rin=limited:p=bt2020:t=smpte2084:m=bt2020nc:r=limited:w=1920:h=1080,format=yuv420p10le' \
  -an -c:v libx265 -preset ultrafast -crf 20 -tag:v hvc1 \
  -x265-params 'hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400' \
  media/hdr10-dark-highlight-30s.mp4
```

Verified contract: 1799 frames, 1920×1080, `yuv420p10le`, `bt2020nc`,
`smpte2084`, `bt2020`, ST2086 mastering display, MaxCLL 1000 and MaxFALL 400.

## HLG motion

`hlg-motion-30s.mp4` uses 30 seconds beginning at 00:05:00 and the same declared
input assumptions, converted to BT.2020/HLG. SHA-256:
`a3b5f2231fa3caf7c0334b3b6ff3ea101b0e9fc07b5677ab7ad91a1968456773`.

```bash
ffmpeg -ss 300 -t 30 -i media/meridian-uhd-hdr-p3pq.mp4 \
  -vf 'zscale=pin=smpte432:tin=smpte2084:min=bt709:rin=limited:p=bt2020:t=arib-std-b67:m=bt2020nc:r=limited:w=1920:h=1080,format=yuv420p10le' \
  -an -c:v libx265 -preset ultrafast -crf 20 -tag:v hvc1 \
  -x265-params 'repeat-headers=1:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc' \
  media/hlg-motion-30s.mp4
```

Verified contract: 1799 frames, 1920×1080, `yuv420p10le`, `bt2020nc`,
`arib-std-b67`, `bt2020`.

Do not use either derived HDR clip to claim camera-native HDR performance. They
exercise color/timestamp/encode contracts and natural-picture tone mapping only.

## Extended natural corpus

Tears of Steel: (CC) Blender Foundation, https://mango.blender.org/,
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The 4K file and original
surround mix are pinned separately in `open-sources.yaml`. Local excerpt:

```bash
ffmpeg -v error -n -ss 90 -i media/tears-of-steel-4k.webm \
  -ss 90 -i media/tears-of-steel-surround.flac -map 0:v:0 -map 1:a:0 \
  -t 30 -c copy media/natural-4k-surround-30s.mkv
```

Stream-copy seeking includes video keyframe preroll: actual probed duration is
31.436 seconds, not exactly the requested 30 seconds. This is a 4K/5.1 processing
fixture, **not** proof that the independently released editions are editorially
aligned. Source/current/proposed tests compare against this same assembled source.
The listening report retains original gains; no normalization is applied to excerpts.

The 176-minute *Intolerance* (1916) historical copy contains video only. Unlike a
looped short clip it exercises a continuous non-repeating long timeline. Its
400×300 resolution and absence of audio do not qualify modern 4K feature films or
long-form A/V sync. Public-domain source-page evidence is jurisdiction-specific.

```bash
ffmpeg -v error -n -i media/intolerance-1916.ogv -map 0:v:0 -c copy \
  media/intolerance-1916.mkv
```

Remux SHA-256: `55f92114b47267f5d7d1babc707bb1c7904d41f2ee48cce04bd2aed1fc35fa1d`.
