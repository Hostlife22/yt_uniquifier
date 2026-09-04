# Distributed batch

`yt-uniq queue` + `yt-uniq worker` let multiple machines share a single
work queue without any external coordinator. Coordination is just
`os.rename(2)` between two directories on a shared filesystem.

## Filesystem requirements

The contract: cross-directory rename must be **atomic** — exactly one
worker wins each lease. Compatible setups:

| FS / mount | Status |
|---|---|
| **NFSv4** with `noac` mount option | target configuration; qualify on the real hosts before production |
| **ZFS** shared via NFSv4 (`sharenfs=on`) | supported |
| **ext4** on a single host (multi-process on one machine) | supported |
| **APFS** on macOS (single-host testing) | supported |
| NFSv3 without `noac` | NOT supported — attribute caching makes rename appear non-atomic |
| `s3fs-fuse`, `goofys` (S3 over FUSE) | NOT supported — rename is implemented as copy+delete |
| SMB1 | NOT supported |
| SMB2/3 | partial; verify with `init` step |

`yt-uniq queue init` performs a real atomic-rename test against the chosen
directory and fails fast with a clear error if the filesystem can't honour
the contract.

## NFS mount options (Linux)

On the client:

```
your.nfs.server:/exports/queue  /shared/queue  nfs4  noac,vers=4.2,hard,timeo=600  0  0
```

`noac` disables the attribute cache that makes `rename` look non-atomic to
concurrent readers. Yes, it slows down metadata operations — that's the
trade-off for correct leasing.

## Workflow

```bash
# On one machine (or any one machine that mounts the share):
yt-uniq queue init /shared/queue
yt-uniq queue add  /shared/queue /shared/sources/movie1.mp4 \
                                  /shared/sources/movie2.mp4 \
                                  /shared/sources/movie3.mp4

# On worker hosts A and B (both mount /shared):
yt-uniq worker /shared/queue \
  --profile /shared/profiles/cid_aware.yaml \
  --out-dir /shared/uniq/ \
  --encoder libx264 \
  --workers 4 \
  --heartbeat-sec 30 &

# Monitor from anywhere:
yt-uniq queue status /shared/queue --json
# {"pending": 0, "in_progress": 2, "done": 1, "failed": 0}
```

## Heartbeat and recovery

Each worker process has a host+PID+nonce identity and touches its `.alive` file every
`--heartbeat-sec` (default 30s). If the file's mtime is older than
`stale_sec` (default 300s, configurable via `yt-uniq queue reset
--stale-sec`), any other worker's next `lease()` will move that host's
leased files back to `pending/`.

CLI and GUI workers keep this heartbeat running while FFmpeg is active. A completed
encode is first written to a hidden worker-specific file beside the final output.
The shared orchestrator probes its stream/timestamp/color contract and decodes the
complete primary video plus every audio stream before the worker may commit that file.
This correctness gate is mandatory; rich pHash/VMAF/SSIM HTML/JSON reports remain
separate diagnostics and are not a publication criterion.
The worker may publish it only after atomically moving its lease to a journal-specific
hidden fence in `done/`; if a reaper already reclaimed the lease, the stale result is
discarded. After publication, that fence becomes the ordinary `done/<input>` marker.

Before that fence, the worker persists a small permission-controlled journal under
`<queue>/.commits/`. If the process dies between the lease-to-fence rename and final
output rename, another CLI or GUI worker waits for the original heartbeat to become
stale and publishes the already validated staged file. The random fence token belongs
to exactly one journal, so an older same-name `done` marker or a requeued hard link
cannot authorize a new output. If the lease was reaped before reaching its fence,
recovery deletes the unfenced staged artifact instead of publishing it.

This closes the local hard-crash publication window and preserves fenced,
at-least-once processing. It is not a transactional exactly-once guarantee across a
network partition. Cross-host NFS deployments must still run lease/reap/journal/crash
qualification on the actual mount before production; that matrix is **NOT VERIFIED**.
All workers need the same service UID, or an explicit inheritable ACL granting the
required read/write/delete access to the queue, output directory, staged files and
`.commits/`. A shared GID alone is insufficient for commit-journal recovery. Journal
payloads contain basenames and a random fence token only, not absolute source/output
paths. Journal files are immutable after publication and owner-only (`0600`, further
restricted by `umask`); recovery deletes their directory entries rather than modifying
their contents.

## Local NFS fault-injection lab

Docker on a Linux host can create an ephemeral NFSv4 server and two independent
clients, then exercise concurrent leasing, a network partition with stale reaping,
stale-owner publication fencing, and recovery after a crash between fence and publish:

```bash
tools/docker_nfs_fault_smoke.sh
```

Evidence is written to `.nfs-qualification/` and includes per-scenario JSON, Docker
inspection and the client mount table. The lab uses a short `soft` timeout so a forced
partition cannot wedge Docker cleanup. That is deliberately different from the
recommended production `hard` mount above: passing the lab is a deterministic
application/fencing gate, while the real production hosts must still be qualified
with their actual `hard` mount, server, network and failure-recovery policy.

## Per-machine encoder variation

By design, two workers may use **different** encoders (one libx264 on
Linux, one hevc_videotoolbox on a Mac). The resulting variants of the
same source therefore differ slightly in H.264 / HEVC bitstream params.
This is usually fine for the CID-divergence use case (variants are
supposed to differ). If you need consistency, pin all workers to the same
encoder via `--encoder libx264`.

## Output collisions

Workers publish to `out_dir/<input.stem>.uniq.<profile-container>`. Queue insertion
rejects duplicate basenames, but independent queue roots can still target the same
output directory and race on equal stems. Avoid that deployment by:

1. Ensuring unique basenames in `pending/`, or
2. Setting per-worker `--out-dir` and merging later.

## Docker Compose example

```yaml
services:
  worker:
    image: yt-uniquifier:1.4.0
    volumes:
      - /mnt/shared:/shared:rw            # NFSv4 noac mount on host
    environment:
      YT_UNIQ_PROFILE: /shared/profiles/cid_aware.yaml
    command:
      - yt-uniq
      - worker
      - /shared/queue
      - --profile=/shared/profiles/cid_aware.yaml
      - --out-dir=/shared/uniq
      - --encoder=libx264
      - --workers=4
    deploy:
      mode: replicated
      replicas: 4                          # 4 workers in this stack
```

## Cleanup

`done/` is just a marker — files accumulate. Periodically:

```bash
find /shared/queue/done -mtime +30 -delete
```

Do not prune a `done/` marker while a matching file remains in `<queue>/.commits/`:
the marker is the publication fence. Healthy workers reconcile journals after the
owner heartbeat timeout, well before the 30-day example retention window.

(A `yt-uniq queue prune --done-older-than 30d` subcommand is deferred to
v0.4 — there's no work going into it before the real-CID validation
harness lands. Use the `find` snippet above in cron until then.)
