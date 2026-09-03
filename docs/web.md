# Web UI & Docker

A headless FastAPI server that drives the same orchestrator the
CLI and the desktop GUI use. The web layer is a thin shell —
every endpoint builds a `Plan` + `RunOptions` and hands them to
`core.orchestrator.run_full`. No business logic lives in `web/`.

The intended deployment is a homelab NAS: drop the container,
mount your media tree, drive encodes from a phone or laptop on
the LAN. A reverse proxy in front (nginx, Caddy, Traefik) gives
you TLS and saner auth than the built-in basic-auth bypass.

## Run it locally

```bash
pip install yt-uniquifier[web]
yt-uniq-web                           # binds 127.0.0.1:8080 by default
```

Then open <http://127.0.0.1:8080>. The page is a vanilla-JS SPA
(no framework). Pick an input path, a profile from the dropdown,
optionally an output filename — click **Start**. Live progress
streams via Server-Sent Events.

CLI flags (env-var equivalents in parentheses):

| Flag             | Env var                  | Default                       |
|------------------|--------------------------|-------------------------------|
| `--host`         | `YT_UNIQ_WEB_HOST`       | `127.0.0.1`                   |
| `--port`         | `YT_UNIQ_WEB_PORT`       | `8080`                        |
| `--work-dir`     | `YT_UNIQ_WEB_WORK_DIR`   | `~/.cache/yt_uniquifier/web`  |
| `--output-dir`   | `YT_UNIQ_WEB_OUTPUT_DIR` | `./output`                    |
| `--profile-dir`  | `YT_UNIQ_WEB_PROFILE_DIR`| per-user XDG config           |
| `--input-root`   | `YT_UNIQ_WEB_INPUT_ROOT` | current working directory     |
| —                | `YT_UNIQ_WEB_MAX_CONCURRENT_RUNS` | `2`                    |
| —                | `YT_UNIQ_WEB_RUN_RETENTION_SEC` | `604800` (7 days)       |
| —                | `YT_UNIQ_WEB_MAX_RUN_RECORDS` | `1000`                    |

Basic auth is gated on both `YT_UNIQ_WEB_USER` and
`YT_UNIQ_WEB_PASS` being set. With neither set, the server is
LAN-trust mode and treats every request as authenticated — acceptable
only for a loopback bind or an isolated container network. HTTP Basic
credentials are merely encoded, not encrypted: never publish this service
directly on an untrusted network. Terminate TLS and enforce access policy at
a reverse proxy; bind the host-side port to `127.0.0.1` when the proxy runs
on the same host.

## Docker

The shipped `Dockerfile` is a two-stage build: a Python wheel
builder and a runtime stage that copies `ffmpeg` and `ffprobe`
out of `jrottenberg/ffmpeg:7-alpine` into a `python:3.12-slim`
base. The runtime user is non-root (`uid 1000 ytuniq`); `tini`
reaps zombie ffmpeg subprocesses if the container is killed
mid-encode.

The image deliberately does **not** install the `[ml]` extra
(`torch` alone is ~800 MB). If you want SSCD inside the
container, bake your own:

```dockerfile
FROM yt-uniquifier:1.4.0
USER root
RUN pip install --no-cache-dir "yt-uniquifier[ml]"
USER ytuniq
```

### Build

```bash
docker build -t yt-uniquifier:1.4.0 .
```

### Run (LAN trust)

```bash
docker run --rm -p 127.0.0.1:8080:8080 \
    -v $PWD/input:/data/input:ro \
    -v $PWD/output:/data/output \
    -v $PWD/work:/data/work \
    yt-uniquifier:1.4.0
```

### Run behind a same-host TLS reverse proxy

```bash
docker run --rm -p 127.0.0.1:8080:8080 \
    -e YT_UNIQ_WEB_USER=alice \
    -e YT_UNIQ_WEB_PASS=hunter2 \
    -v $PWD/input:/data/input:ro \
    -v $PWD/output:/data/output \
    yt-uniquifier:1.4.0
```

Configure nginx, Caddy, or Traefik to expose an `https://` endpoint and proxy
to `http://127.0.0.1:8080`. Do not change the publish address to `0.0.0.0`
unless an external firewall provides an equivalent trusted boundary.

### docker-compose

A reference `docker-compose.yml` ships at the repo root with
loopback bind, volume mounts for input/output/work/profiles, and
the basic-auth env vars commented out. Copy to your NAS, edit
the volume paths, `docker compose up -d`.

## API surface

Every endpoint is JSON in / JSON out except the SSE event stream
and the QA HTML/JSON passthroughs.

| Method | Path                                                | Notes                             |
|--------|-----------------------------------------------------|-----------------------------------|
| GET    | `/`                                                 | SPA shell (HTML)                  |
| GET    | `/healthz`                                          | liveness; returns `ok`            |
| GET    | `/api/profiles/local`                               | bundled + per-user YAMLs          |
| GET    | `/api/profiles/community?refresh=true`              | marketplace catalog               |
| POST   | `/api/profiles/community/{id}/install`              | verify SHA + install              |
| POST   | `/api/run`                                          | start an encode; returns `run_id` |
| GET    | `/api/run/{id}/events`                              | SSE stream of RunEvents           |
| GET    | `/api/run/{id}/status`                              | terminal-state poll               |
| POST   | `/api/run/{id}/cancel`                              | flip CancelToken (≤ 7 s honour)   |
| GET    | `/api/qa/{name}/html`                               | serve `<name>.qa.html`            |
| GET    | `/api/qa/{name}/json`                               | serve `<name>.qa.json`            |

`POST /api/run` body:

```json
{
  "input_path": "/data/input/source.mp4",
  "profile_path": "/data/profiles/cid_aware.yaml",
  "output_name": "optional.mp4",
  "encoder_override": null,
  "workers": 1
}
```

Input paths outside `WebConfig.input_root` are rejected with **403**;
when it is unset, the server uses its current working directory as the
root. Set `--input-root /data/input` explicitly for NAS/container mounts.
Path-traversal attempts against
`/api/qa/...` are rejected with **400 / 404**.
Body-bearing requests require exactly one valid non-negative `Content-Length`;
missing, malformed, negative, duplicate, or over-limit lengths are rejected before
route handling. Combining `Transfer-Encoding` with `Content-Length` is also rejected.
Chunked request bodies are intentionally unsupported so they cannot bypass the
configured hard ceiling.

Terminal run status is stored atomically in `<work-dir>/web_runs.json` and
survives process restarts. A run that was `pending` or `running` when the server
stopped is reported as `failed` with an interrupted/restart diagnostic; FFmpeg
checkpoint data remains in its per-run work directory for operator inspection.
Only run IDs, status, redacted error class, timestamps, and output basenames are
persisted — source/profile paths and full exception messages are not written to
this web registry.

The default output filename uses the selected profile's container (`.mp4`, `.mkv`,
or `.mov`). An explicit filename with an incompatible suffix is rejected before a
background job starts. Active final outputs are reserved atomically in
`<output-dir>/.yt_uniquifier-reservations/`, so separate server processes sharing a
local filesystem return **409** instead of writing the same file. The exact owner
releases its reservation at terminal state; a dead owner on the same host can be
recovered. Foreign-host stale records fail closed because remote process liveness
cannot be proven. Shared/NFS deployments must qualify atomic-create and recovery
semantics for their filesystem; that matrix is currently **NOT VERIFIED**.

`max_concurrent_runs` is enforced across server processes that share the same
`output_dir`. Atomic owner slots and their immutable capacity record live under
`<output-dir>/.yt_uniquifier-admission/`; a full pool returns **429**, while an
unavailable or inconsistently configured pool returns **503**. Every instance that
shares the directory must use the same value. To change it, stop every such server,
verify no encode process is still active, remove only that admission directory, and
then restart the servers with the new common value. Dead same-host owners are
recovered; malformed and foreign-host owners fail closed.

Every `run_full` caller also coordinates encoder slots and estimated temporary-disk
bytes through `YT_UNIQ_RESOURCE_LOCK_DIR` (default:
`~/.cache/yt_uniquifier/resource-admission`). The Docker image points it at the
mounted `/data/work/.resource-admission`, so sibling containers must share that work
volume to share the budget. Use the same absolute registry path and service account
for all local instances. Stop all participating processes before clearing it or
changing a detected encoder capacity.

This remains an estimate-based local safety boundary, not a cluster scheduler.
Mixed GPU visibility/device routing, different service accounts, NFS atomicity,
network partitions and the accuracy of natural 4K/long-form bitrate estimates remain
**NOT VERIFIED**; enforce hard filesystem quotas externally in those deployments.

## What's *not* on the web yet

* Calibrate, Batch, Queue, Validation, Corpus screens — desktop
  GUI only in v0.9. The web shell intentionally targets the
  single-input "drive an encode" use case for NAS deployments;
  full screen parity is on the v1.0 list.
* No filesystem browser on `/` — paths must be typed (or pasted
  from your NAS web UI). A picker is on the v1.0 wishlist.
* No SSE reconnect on transient drop. Refresh the page; the
  `run_id` is preserved server-side until the next start.
