# Telemetry (v0.9.0 R3)

`yt-uniquifier` records **zero** telemetry until you explicitly
opt in. When enabled, exactly one summary event is appended to a
local JSONL file at the end of each completed or failed encode.
No network egress in v0.9 — uploading aggregate stats to a
shared collector is a separate v1.0 conversation that will
require its own consent step.

## What gets recorded

Per encode, on success or failure, the following fields are
written to `events.jsonl`:

| Field            | Example                          | Notes                              |
|------------------|----------------------------------|------------------------------------|
| `kind`           | `run_summary`                    | fixed                              |
| `status`         | `completed` / `failed`           | fixed set                          |
| `profile_name`   | `cid_aware`                      | from the user's YAML               |
| `profile_codec`  | `h264`                           | from Profile.target_codec          |
| `encoder_name`   | `libx264`                        | from EncoderCandidate.name         |
| `encoder_vendor` | `x264` / `nvenc` / `videotoolbox`| from EncoderCandidate.vendor       |
| `wall_clock_sec` | `423.17`                         | end-to-end time                    |
| `workers`        | `4`                              | from RunOptions.workers            |
| `os`             | `darwin` / `linux` / `win32`     | sys.platform                       |
| `os_release`     | `25.5.0`                         | platform.release()                 |
| `python`         | `3.12`                           | major.minor only                   |
| `segments_done`  | `27`                             | success only                       |
| `output_basename`| `clip__cid_aware.mp4`            | success only; **basename** only    |
| `error_summary`  | `PipelineError: …` (first 200 c.)| failure only                       |
| `schema_version` | `1`                              | bumped on any breaking shape change|
| `event_id`       | UUID4                            | per-event unique key               |
| `ts`             | Unix epoch                       | server-side stamp                  |

What is **never** recorded: source file paths, source file
content, file hashes, audio fingerprints, profile YAML body,
durations, resolutions, error tracebacks past the first 200
chars, or anything that would identify the source artifact.

The same bounded recursive redactor is installed before structured log sinks,
audit JSONL writers, and telemetry serialization. Sensitive key names
(`authorization`, cookies, passwords, secrets, API/access/private keys and tokens),
inline bearer/basic credentials, common GitHub/OpenAI token forms and token-bearing
query parameters are replaced with `<REDACTED>`. Public audit fields redact every
absolute path to `<PATH>/<basename>`; structured logs retain only that safe basename.
Prometheus uses fixed state and encoder label vocabularies and never stores paths,
tokens, run IDs, plan IDs, job IDs, or segment IDs as labels.

## What does *not* go into the event

* Full paths. The default config has `redact_paths=True` which
  rewrites `$HOME` → `<HOME>` in any string field that survives
  the schema. The basename-only `output_basename` is already
  HOME-free; the rule exists as defence-in-depth.
* Error tracebacks. We capture `type(exc).__name__: str(exc)`
  truncated to 200 characters. Internal frames and locals stay
  off the event.

## Where the events live

| OS      | Path                                                                           |
|---------|--------------------------------------------------------------------------------|
| macOS   | `~/Library/Application Support/yt_uniquifier/telemetry/events.jsonl`           |
| Linux   | `$XDG_DATA_HOME/yt_uniquifier/telemetry/events.jsonl` (or `~/.local/share/…`)  |
| Windows | `%APPDATA%\yt_uniquifier\telemetry\events.jsonl`                               |

The file rotates when it reaches 1 MiB (configurable per
`TelemetryConfig.rotate_at_bytes`); exactly one backup
(`events.jsonl.1`) is retained.

The consent marker — proof you have answered the first-run
dialog one way or the other — lives at
`~/.config/yt_uniquifier/telemetry-consent`. Its body is the
literal string `enabled` or `disabled` so a support technician
can tell the state without parsing JSON.

## CLI

```bash
yt-uniq telemetry status                      # path, event count, consent state
yt-uniq telemetry status --json
yt-uniq telemetry export ~/share.jsonl        # copy out for sharing
yt-uniq telemetry purge --yes                 # wipe events dir
```

There is no `yt-uniq telemetry enable` subcommand on purpose.
Turning it on must be an explicit GUI click or a programmatic
`RunOptions(telemetry=TelemetryConfig(enabled=True))` so a
script can't accidentally flip the global flag.

## GUI

**Settings → Local telemetry (opt-in)** has:

* an **Enabled** toggle
* a **Redact paths** toggle (on by default)
* status (event count + on-disk path)
* **Apply telemetry** — persists the config and writes the
  consent marker
* **Open events folder** — reveals the dir in the OS file
  browser
* **Purge events** — irreversible wipe with a confirm dialog

The first launch shows a one-time dialog with two buttons
(**Enable** / **Keep disabled**); the default is *disabled*.
Dismissing the dialog without choosing records "disabled" so
you are never prompted again.

## Programmatic use

```python
from yt_uniquifier.core.telemetry import TelemetryConfig
from yt_uniquifier.core.orchestrator import RunOptions, run_full

opts = RunOptions(
    work_dir=Path("work"),
    output=Path("out.mp4"),
    telemetry=TelemetryConfig(enabled=True),  # off by default
)
run_full(plan, opts)
```

`record(event, config)` is also public if you want to push
custom events into the same file from your own pipeline glue.
Failures swallow and log via `logging` — telemetry never
alters run outcomes.
