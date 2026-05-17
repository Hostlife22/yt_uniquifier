# yt-uniquifier

> Production-grade re-encoder with controlled micro-transforms for owned or
> licensed video content.

## What it does

- One CLI + optional PyQt6 GUI on top of `ffmpeg`.
- Applies a configurable set of micro-transforms (crop+rescale, color jitter,
  noise, rotation, frame-blend, pitch / tempo, EQ, EBU R128 loudness norm)
  composed into a single `-filter_complex` per ffmpeg invocation.
- Keyframe-aware split → per-segment process → concat demuxer, so multi-hour
  files survive Ctrl+C and resume from `state.json` on the next run.
- Multi-track audio, soft subtitles, and chapters are passed through.
- HDR-aware (10-bit pix_fmt + color tags when `keep_hdr: true`).
- Multi-vendor encoder detect (NVENC, QSV, AMF, VideoToolbox, libx264/x265)
  with real test-run on a null source.
- QA report on every run: pHash, audio fingerprint (chromaprint), VMAF, SSIM,
  MD5 — rendered to a single-page HTML.

## What it is NOT

A tool to evade rights-holder detection of third-party copyrighted material.
The intended scenarios are: re-uploading your own content, distributing
licensed material in multiple cuts, or producing fair-use derivatives. If your
use case is "make YouTube Content ID stop matching someone else's movie" —
this is the wrong tool, and I won't help you wire it up.

## Install

Requires Python 3.11+ and `ffmpeg` / `ffprobe` on `PATH`.

```bash
pip install -e ".[dev]"           # CLI + dev tooling
pip install -e ".[dev,gui]"       # also installs PyQt6 for the desktop UI
pip install -e ".[dev,qa]"        # adds chromaprint (fpcalc) Python bindings
```

Optional binaries (graceful skip in the QA report if missing):

- `fpcalc` (chromaprint) — audio fingerprint similarity
- ffmpeg built with `libvmaf` — VMAF score

## Quickstart

```bash
# 1. Inspect a source.
yt-uniq probe /path/to/master.mp4 | jq '.video[0]'

# 2. Validate against YouTube targets + HDR sanity.
yt-uniq preflight /path/to/master.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml

# 3. Re-encode with micro-transforms (resume-capable).
yt-uniq run /path/to/master.mp4 \
  --profile src/yt_uniquifier/profiles/medium.yaml \
  --out    /path/to/uniq.mp4

# 4. Inspect the QA report.
open /path/to/uniq.mp4.qa.html

# 5. Standalone QA on a pre-existing pair (no encode).
yt-uniq qa /path/to/master.mp4 /path/to/uniq.mp4 --no-vmaf

# 6. Batch a directory.
yt-uniq batch /path/to/movies/ \
  --profile src/yt_uniquifier/profiles/soft.yaml \
  --out /path/to/uniq/

# 7. Launch the GUI (requires [gui] extra).
yt-uniq-gui
```

## CLI reference

| Command | What it does |
|---------|--------------|
| `yt-uniq version` | Print version |
| `yt-uniq probe <path>` | Print SourceMeta JSON |
| `yt-uniq probe --encoders` | List working encoders (cached) |
| `yt-uniq preflight <in> --profile p.yaml` | YouTube target validation |
| `yt-uniq run <in> --profile p.yaml --out o.mp4` | Single-file run with resume + auto QA |
| `yt-uniq batch <dir> --profile p.yaml --out <dir>` | Sequential directory processing |
| `yt-uniq qa <in> <out>` | Standalone similarity report |

Run any command with `--help` for full flag listings.

## Project docs

- [Architecture](./docs/architecture.md)
- [Profiles](./docs/profiles.md)
- [Filter graph](./docs/filter_graph.md)
- [YouTube targets](./docs/youtube_targets.md)
- [Implementation specs](./specs/README.md)

## Development

```bash
pip install -e ".[dev]"
ruff check .
mypy src/yt_uniquifier
pytest -q
```

CI runs lint + tests on Ubuntu and macOS for Python 3.11 and 3.12.

## License

MIT — see [LICENSE](./LICENSE).
