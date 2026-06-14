# yt-uniquifier — headless web UI image (v0.9.0 R4 / F13).
#
# Two-stage build keeps the runtime layer free of build toolchains.
# Base ffmpeg image gives us a known-working static ffmpeg without
# distro-specific surprises (libass, libvmaf, libfdk-aac all
# included). Python is layered on top so we can install yt-uniquifier
# itself + the [web] extra.
#
# Build:
#   docker build -t yt-uniquifier:0.9.0 .
#
# Run (LAN-trusted; no auth):
#   docker run --rm -p 8080:8080 \
#       -v $PWD/input:/data/input:ro \
#       -v $PWD/output:/data/output \
#       -v $PWD/work:/data/work \
#       yt-uniquifier:0.9.0
#
# Run (with basic auth):
#   docker run --rm -p 8080:8080 \
#       -e YT_UNIQ_WEB_USER=alice -e YT_UNIQ_WEB_PASS=hunter2 \
#       -v $PWD/input:/data/input:ro \
#       -v $PWD/output:/data/output \
#       yt-uniquifier:0.9.0
#
# The [ml] / [scene] extras are deliberately NOT installed here —
# torch alone would push the image past 1 GB. Bake your own image
# with `pip install yt-uniquifier[ml]` inside the runtime stage if
# you need SSCD/PySceneDetect on the container.

# v1.1.0 Task 12: multi-arch image. Both base tags resolve to a
# manifest list with linux/amd64 + linux/arm64 entries — buildx will
# pick the right arch per --platform leg. Dependabot's docker
# ecosystem updates this file weekly so the floating tags stay close
# to upstream patch releases without losing reproducibility (digest
# pins are added by Dependabot's `docker-image` updater when it
# rewrites these lines).
ARG FFMPEG_IMAGE=jrottenberg/ffmpeg:7-alpine
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---- Build stage: wheels only, no system tools in final layer ----
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip wheel build && \
    python -m build --wheel --outdir /wheels

# ---- Runtime stage ---------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# ffmpeg from the official static image — copy the binaries only so we
# don't pull alpine's libc into a Debian-based Python image.
COPY --from=${FFMPEG_IMAGE} /usr/local/bin/ffmpeg  /usr/local/bin/ffmpeg
COPY --from=${FFMPEG_IMAGE} /usr/local/bin/ffprobe /usr/local/bin/ffprobe

# Runtime deps for ffmpeg's shared libs (libass etc. when present in
# the static build) — bookworm-slim ships musl-compatible shims for
# most of what the alpine build expects.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libssl3 ca-certificates curl tini && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --create-home --shell /usr/sbin/nologin --uid 1000 ytuniq

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl 'yt-uniquifier[web]' && \
    rm -rf /wheels

USER ytuniq
WORKDIR /home/ytuniq

ENV YT_UNIQ_WEB_HOST=0.0.0.0 \
    YT_UNIQ_WEB_PORT=8080 \
    YT_UNIQ_WEB_WORK_DIR=/data/work \
    YT_UNIQ_WEB_OUTPUT_DIR=/data/output \
    YT_UNIQ_WEB_INPUT_ROOT=/data/input \
    PYTHONUNBUFFERED=1

VOLUME ["/data/input", "/data/output", "/data/work"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:8080/healthz || exit 1

# tini reaps zombie ffmpeg subprocesses if the container is killed
# mid-encode; without it Docker prints "Cannot kill container" warnings.
ENTRYPOINT ["/usr/bin/tini", "--", "yt-uniq-web"]
