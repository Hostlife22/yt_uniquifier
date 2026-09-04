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
    --tmpfs /fault-disk:size=2m,mode=1777 \
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
  --resume-timeout 120 \
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
server_ip="$(docker inspect --format \
  "{{(index .NetworkSettings.Networks \"${network_name}\").IPAddress}}" \
  "${server_name}")"
test -n "${server_ip}"
# Isolate only NFS traffic. Disconnecting a container with an active kernel NFS
# mount can block Docker's own control plane, preventing the reconnect command
# from running and invalidating the fault test itself.
docker exec "${client_a}" iptables -I OUTPUT -d "${server_ip}" \
  -p tcp --dport 2049 -j REJECT
docker exec "${client_a}" iptables -C OUTPUT -d "${server_ip}" \
  -p tcp --dport 2049 -j REJECT
sleep 3
docker exec "${client_b}" "${tool[@]}" reap /shared/queue-partition \
  --stale-sec 1 --expected 1 --result /shared/lab-results/reap.json
docker exec "${client_a}" iptables -D OUTPUT -d "${server_ip}" \
  -p tcp --dport 2049 -j REJECT
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

# Kill the worker with SIGKILL at every durable-publication boundary. Recovery
# must either publish the already-fenced bytes or re-lease and retry; a second
# recovery pass must always be idempotent.
for phase in after-stage after-journal after-fence after-publish; do
  queue="/shared/queue-${phase}"
  output="/shared/output-${phase}"
  ready="/shared/lab-results/${phase}-ready.json"
  docker exec "${client_b}" "${tool[@]}" init "${queue}"
  docker exec "${client_b}" "${tool[@]}" seed "${queue}" \
    --count 1 --prefix "${phase}"
  docker exec "${client_a}" "${tool[@]}" crash-commit-phase "${queue}" \
    --output "${output}" --phase "${phase}" --worker "worker-${phase}" \
    --ready "${ready}" &
  crash_exec_pid=$!
  for _attempt in $(seq 1 100); do
    if docker exec "${client_b}" test -f "${ready}"; then
      break
    fi
    sleep 0.1
  done
  docker exec "${client_b}" test -f "${ready}"
  worker_pid="$(docker exec "${client_b}" python -c \
    "import json; print(json.load(open('${ready}'))['pid'])")"
  test -n "${worker_pid}"
  docker exec "${client_a}" kill -9 "${worker_pid}"
  set +e
  wait "${crash_exec_pid}"
  crash_phase_rc=$?
  set -e
  test "${crash_phase_rc}" -ne 0
  docker exec "${client_b}" "${tool[@]}" recover-commit-phase "${queue}" \
    --output "${output}" --phase "${phase}" --worker "recovery-${phase}" \
    --result "/shared/lab-results/${phase}.json"
done

# A malformed resume checkpoint must fail closed on the actual NFS mount.
docker exec "${client_b}" "${tool[@]}" corrupt-checkpoint \
  /shared/checkpoint-faults \
  --result /shared/lab-results/corrupt-checkpoint.json

# A bounded tmpfs gives a deterministic ENOSPC without risking the Docker host.
# The last durable checkpoint must remain byte-identical and no partial temp
# state may survive.
docker exec "${client_b}" "${tool[@]}" disk-full-checkpoint /fault-disk \
  --result /shared/lab-results/disk-full-checkpoint.json

docker cp "${server_name}:/exports/lab-results/." "${run_artifact_dir}/"
docker inspect "${server_name}" "${client_a}" "${client_b}" \
  > "${run_artifact_dir}/docker-inspect.json"
docker exec "${client_b}" cat /proc/mounts > "${run_artifact_dir}/client-mounts.txt"
printf 'NFSv4 fault-injection qualification passed; artifacts: %s\n' "${run_artifact_dir}"
