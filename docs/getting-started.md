# Getting started

A 5-minute tour of `yt-uniquifier`. Covers CLI, GUI, and web —
pick one and the others will look familiar afterwards.

## 1. Install

```bash
pip install yt-uniquifier[gui,web]
```

Pulls the core CLI, the PyQt6 desktop GUI, and the FastAPI web
shell. Drop a bracket if you don't need that surface. See
[Install](install.md) for binary requirements (`ffmpeg`,
optional `fpcalc`, optional Whisper).

## 2. Pick (or install) a profile

The shipped profiles cover most use cases:

| Profile id           | Use case                              |
|----------------------|---------------------------------------|
| `soft`               | Lowest-impact baseline                |
| `medium`             | Balanced default                      |
| `aggressive`         | Experimental stronger processing      |
| `cid_aware`          | Legacy experimental compatibility     |
| `cid_aggressive`     | Legacy maximum-change compatibility   |
| `youtube_shorts`     | 9:16 reframe for Shorts (≤ 60 s)      |
| `youtube_1080p`      | 16:9 1080p                            |
| `youtube_4k`         | 16:9 4K                               |
| `tiktok_vertical`    | 9:16 for TikTok                       |
| `instagram_reels`    | 9:16 for Reels                        |
| `instagram_square`   | 1:1 for Instagram                     |
| `linkedin_square`    | 1:1 for LinkedIn                      |

Browse community-contributed profiles too:

```bash
yt-uniq profile list-community
yt-uniq profile install cid_aware       # installs into ~/.config/yt_uniquifier/profiles/
```

The catalog is SHA-pinned per entry; the installer refuses to
write the file if the hash does not match. Details in
[Community marketplace](marketplace.md).

## 3. Sanity-check the input

```bash
yt-uniq probe ~/Videos/in.mp4
yt-uniq preflight ~/Videos/in.mp4 --profile cid_aware
```

`probe` prints the parsed `SourceMeta` (container, streams, HDR
state). `preflight` runs the same warning matrix that's enforced
inside `run` and tells you what would fail before the encode
starts.

## 4. Run an encode

=== "CLI"

    ```bash
    yt-uniq run ~/Videos/in.mp4 \
        --profile cid_aware \
        --output ~/Videos/out.mp4
    ```

=== "Desktop GUI"

    ```bash
    yt-uniq-gui
    ```

    Then on the **Run** screen: pick input, pick profile, click
    **▶ Run**. The live divergence indicator on the right shows
    a sparkline of pHash divergence per segment.

=== "Web UI (Docker)"

    ```bash
    docker run --rm -p 127.0.0.1:8080:8080 \
        -v $PWD/input:/data/input:ro \
        -v $PWD/output:/data/output \
        -v $PWD/work:/data/work \
        yt-uniquifier:1.4.0
    ```

    Open http://127.0.0.1:8080 and fill in the form. Full guide
    at [Web UI & Docker](web.md).

## 5. Inspect the QA report

Every run drops `<out>.qa.json` and `<out>.qa.html` next to the
output. Open the HTML in a browser for the full report including
the pHash / chromaprint / VMAF / (optional) SSCD scores. The
schema is documented in [QA report](qa_report.md).

## 6. Next steps

* **Auto-tune a profile against this source** — `yt-uniq
  calibrate` (or the **Auto-tune** button in the GUI Run screen)
  bisects parameters against a quality target. See
  [Calibration](calibrate.md).
* **Batch a folder** — `yt-uniq batch ~/Videos/in/` or the
  Batch screen.
* **Distribute across a NAS / lab** — `yt-uniq worker` + a
  shared filesystem. See [Distributed batch](distributed.md).
* **Pre-generate subtitles** — `yt-uniq subtitles generate
  ~/Videos/in.mp4`. The `video.subtitles` transform burns them
  in at encode time.
* **Opt into telemetry** — Settings → Local telemetry, or pass
  `--telemetry` in your own scripts. Local-only JSONL; no network.

Stuck? Open `yt-uniq <subcommand> --help` for any subcommand,
or file an issue.
