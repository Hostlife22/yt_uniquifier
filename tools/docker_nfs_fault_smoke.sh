#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${1:-${repo_dir}/.nfs-qualification}"
run_key="yt-uniq-nfs-${RANDOM}-$$"
network_name="${run_key}-network"
server_name="${run_key}-server"
client_a="${run_key}-client-a"
client_b="${run_key}-client-b"
server_image="${run_key}-server-image"
client_image="${run_key}-client-image"
export_volume="${run_key}-export"
run_artifact_dir="${artifact_dir}/${run_key}"

cleanup() {
  docker rm -f "${client_a}" "${client_b}" "${server_name}" >/dev/null 2>&1 || true
  docker network rm "${network_name}" >/dev/null 2>&1 || true
  docker volume rm "${export_volume}" >/dev/null 2>&1 || true
  docker image rm "${client_image}" "${server_image}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "${run_artifact_dir}"
docker build -f "${repo_dir}/tools/nfs_lab/server.Dockerfile" \
  -t "${server_image}" "${repo_dir}"
docker build -f "${repo_dir}/tools/nfs_lab/client.Dockerfile" \
  -t "${client_image}" "${repo_dir}"
docker network create "${network_name}" >/dev/null
docker volume create "${export_volume}" >/dev/null
docker run -d --privileged --name "${server_name}" --network "${network_name}" \
  --hostname nfs-server --volume "${export_volume}:/exports" "${server_image}" >/dev/null
sleep 6
if [[ "$(docker inspect --format '{{.State.Running}}' "${server_name}")" != "true" ]]; then
  docker logs "${server_name}"
  exit 1
fi

for client in "${client_a}" "${client_b}"; do
  docker run -d --privileged --name "${client}" --network "${network_name}" \
    --volume "${repo_dir}:/repo:ro" --workdir /repo \
    --env PYTHONPATH=/repo/src "${client_image}" >/dev/null
done

for _attempt in $(seq 1 30); do
  if docker exec "${client_a}" mount -t nfs4 \
    -o vers=4,noac,soft,timeo=10,retrans=2 nfs-server:/ /shared 2>/dev/null; then
    break
  fi
  sleep 1
done
mount_a="$(docker exec "${client_a}" awk '$2 == "/shared" {print $3}' /proc/mounts)"
test "${mount_a}" = "nfs4"
docker exec "${client_b}" mount -t nfs4 \
  -o vers=4,noac,soft,timeo=10,retrans=2 nfs-server:/ /shared

tool=(python /repo/tools/nfs_queue_qualification.py)
docker exec "${client_a}" "${tool[@]}" init /shared/queue-race
docker exec "${client_a}" "${tool[@]}" seed /shared/queue-race --count 80
docker exec "${client_a}" mkdir -p /shared/lab-results
docker exec "${client_a}" "${tool[@]}" drain /shared/queue-race \
  --worker worker-a --result /shared/lab-results/drain-a.json &
drain_a_pid=$!
docker exec "${client_b}" "${tool[@]}" drain /shared/queue-race \
  --worker worker-b --result /shared/lab-results/drain-b.json &
drain_b_pid=$!
wait "${drain_a_pid}" "${drain_b_pid}"
docker exec "${client_b}" "${tool[@]}" verify /shared/queue-race \
  --results /shared/lab-results --result /shared/lab-results/concurrent.json \
  --expected 80 --workers 2

docker exec "${client_b}" "${tool[@]}" init /shared/queue-partition
docker exec "${client_b}" "${tool[@]}" seed /shared/queue-partition \
  --count 1 --prefix partition
docker exec "${client_a}" "${tool[@]}" partition-worker /shared/queue-partition \
  --output /shared/output-partition --resume /tmp/partition-resume \
  --ready /shared/lab-results/partition-ready.json \
  --result /shared/lab-results/partition.json &
partition_pid=$!
for _attempt in $(seq 1 30); do
  if docker exec "${client_b}" test -f /shared/lab-results/partition-ready.json; then
    break
  fi
  sleep 0.2
done
docker exec "${client_b}" test -f /shared/lab-results/partition-ready.json
docker network disconnect --force "${network_name}" "${client_a}"
sleep 3
docker exec "${client_b}" "${tool[@]}" reap /shared/queue-partition \
  --stale-sec 1 --expected 1 --result /shared/lab-results/reap.json
docker network connect "${network_name}" "${client_a}"
for _attempt in $(seq 1 30); do
  if docker exec "${client_a}" test -d /shared/queue-partition 2>/dev/null; then
    break
  fi
  sleep 0.2
done
docker exec "${client_a}" test -d /shared/queue-partition
docker exec "${client_a}" touch /tmp/partition-resume
wait "${partition_pid}"

docker exec "${client_b}" "${tool[@]}" init /shared/queue-crash
docker exec "${client_b}" "${tool[@]}" seed /shared/queue-crash --count 1 --prefix crash
set +e
docker exec "${client_a}" "${tool[@]}" crash-after-fence /shared/queue-crash \
  --output /shared/output-crash
crash_rc=$?
set -e
test "${crash_rc}" -eq 73
docker exec "${client_b}" "${tool[@]}" recover /shared/queue-crash \
  --output /shared/output-crash --result /shared/lab-results/recovery.json

docker cp "${server_name}:/exports/lab-results/." "${run_artifact_dir}/"
docker inspect "${server_name}" "${client_a}" "${client_b}" \
  > "${run_artifact_dir}/docker-inspect.json"
docker exec "${client_b}" cat /proc/mounts > "${run_artifact_dir}/client-mounts.txt"
printf 'NFSv4 fault-injection qualification passed; artifacts: %s\n' "${run_artifact_dir}"
