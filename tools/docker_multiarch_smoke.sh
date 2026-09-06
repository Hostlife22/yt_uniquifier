#!/usr/bin/env bash
set -euo pipefail

platform="${1:-linux/amd64}"
tag="${2:-yt-uniquifier:smoke-${platform##*/}}"
smoke_dir="$(mktemp -d)"
container_id=""

cleanup() {
  if [[ -n "${container_id}" ]]; then
    docker rm -f "${container_id}" >/dev/null 2>&1 || true
  fi
  rm -rf "${smoke_dir}"
}
trap cleanup EXIT

mkdir -p "${smoke_dir}/input" "${smoke_dir}/output" "${smoke_dir}/work"
# mktemp creates 0700, owned by the host runner. The image runs as UID 1000,
# which differs from hosted Linux's UID; it must traverse the mounted root too.
chmod 0755 "${smoke_dir}"
chmod 0777 "${smoke_dir}/input" "${smoke_dir}/output" "${smoke_dir}/work"

build_cache_args=()
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  cache_scope="docker-${platform##*/}"
  build_cache_args+=(
    --cache-from "type=gha,scope=${cache_scope}"
    --cache-to "type=gha,mode=max,scope=${cache_scope}"
  )
fi

docker buildx build --platform "${platform}" --load --tag "${tag}" \
  "${build_cache_args[@]}" .

docker run --rm --platform "${platform}" --entrypoint ffmpeg \
  --volume "${smoke_dir}/input:/work" "${tag}" \
  -hide_banner -loglevel error -y -f lavfi \
  -i "testsrc2=size=160x90:rate=24:duration=1" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=1" \
  -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest /work/input.mp4

docker run --rm --platform "${platform}" --entrypoint yt-uniq \
  --volume "${smoke_dir}:/work" "${tag}" \
  run /work/input/input.mp4 --profile auto --out /work/output/output.mp4 \
  --work-dir /work/work --encoder libx264 --segment-sec 1 \
  --accept-watermark-risk --no-progress --no-qa

docker run --rm --platform "${platform}" --entrypoint ffprobe \
  --volume "${smoke_dir}/output:/work:ro" "${tag}" \
  -v error -select_streams v:0 -show_entries stream=codec_name \
  -of default=noprint_wrappers=1:nokey=1 /work/output.mp4 | grep -qx h264

container_id="$(docker run -d --platform "${platform}" \
  --publish 127.0.0.1::8080 "${tag}")"
host_port="$(docker port "${container_id}" 8080/tcp | awk -F: 'NR==1 {print $NF}')"

for _attempt in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${host_port}/healthz" | grep -qx ok; then
    docker inspect --format '{{.State.Health.Status}}' "${container_id}" \
      | grep -Eq 'healthy|starting'
    exit 0
  fi
  sleep 1
done

docker logs "${container_id}"
exit 1
