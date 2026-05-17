# Spec 13 — Distributed Batch (shared-FS leasing)

> **Phase 13 (v0.3)** · 2.5 дня · **No deps** (parallel-safe with 11, 12)

## Goal

N машин с общей файловой системой (NFS / SMB / mounted S3 via goofys или
fsspec backend) обрабатывают одну очередь видео без координатора и
без внешних зависимостей (redis / sqlite / message queue). Лизинг через
atomic POSIX rename. Если worker умрёт — heartbeat-reaper передаст файл
другому через 5 минут.

## Scope

**In:**

- `core/queue/leasing.py` — `FileQueue` over a shared directory.
- `cli/cmd_queue.py` — `yt-uniq queue init|add|status|reset` подкоманды.
- `cli/cmd_worker.py` — `yt-uniq worker <queue_dir>` long-running loop.
- Heartbeat файлы + reaper для stale leases.
- Документация `docs/distributed.md` с требованиями к shared FS.

**Not in:** S3 object lock backend (отдельный driver — v0.4); web
dashboard очереди; coordination of which worker uses which encoder
(каждый worker сам через `pick_encoder`); multi-encoder consistency check
для variants одного входа.

## Modules

### Layout очереди

```
<queue_dir>/
├── pending/           — файлы ждут лизинга
├── in_progress/
│   ├── <host>/        — файлы, забранные этим хостом
│   └── <host>.alive   — heartbeat файл (mtime обновляется)
├── done/              — успешно обработанные (просто как маркер)
└── failed/
    └── <host>/<input>.err.txt  — лог ошибки
```

Имя файла в pending/ — относительный путь / basename; абсолютные пути
выходят через `--out-dir` в worker'е.

### `core/queue/leasing.py`

```python
from dataclasses import dataclass
from pathlib import Path
import os
import socket
import time

@dataclass(frozen=True)
class QueueLayout:
    root: Path
    pending: Path
    in_progress: Path
    done: Path
    failed: Path


def queue_layout(root: Path) -> QueueLayout:
    return QueueLayout(
        root=root, pending=root / "pending",
        in_progress=root / "in_progress",
        done=root / "done", failed=root / "failed",
    )


def init_queue(root: Path) -> QueueLayout:
    layout = queue_layout(root)
    for d in (layout.pending, layout.in_progress, layout.done, layout.failed):
        d.mkdir(parents=True, exist_ok=True)
    _verify_atomic_rename(root)
    return layout


def _verify_atomic_rename(root: Path) -> None:
    """Fail fast if the FS doesn't support cross-dir atomic rename.

    Some setups (older NFS, certain SMB configs) silently fall back to
    copy+delete which breaks the lease invariant.
    """
    a = root / ".rename_test_a"
    b = root / ".rename_test_b"
    a.write_text("x")
    os.rename(a, b)
    b.unlink()


class FileQueue:
    def __init__(self, root: Path, *, host: str | None = None) -> None:
        self.layout = queue_layout(root)
        self.host = host or socket.gethostname()
        self.host_dir = self.layout.in_progress / self.host
        self.host_dir.mkdir(parents=True, exist_ok=True)

    # ---- producer ----

    def add(self, path: Path) -> Path:
        """Copy or hard-link the file into pending/. Returns the queued path."""
        dest = self.layout.pending / path.name
        if dest.exists():
            raise FileExistsError(f"already queued: {dest}")
        try:
            os.link(path, dest)               # hard-link if same FS
        except OSError:
            import shutil
            shutil.copy2(path, dest)
        return dest

    # ---- consumer ----

    def lease(self) -> Path | None:
        """Atomically rename one pending file into host_dir. None if empty."""
        for candidate in sorted(self.layout.pending.iterdir()):
            if candidate.name.startswith("."):
                continue
            dest = self.host_dir / candidate.name
            try:
                os.rename(candidate, dest)     # POSIX atomic — winner takes it
            except (OSError, FileNotFoundError):
                continue                       # someone else got it
            return dest
        return None

    def heartbeat(self) -> None:
        """Update <host>.alive mtime."""
        alive = self.layout.in_progress / f"{self.host}.alive"
        alive.touch()

    def release_done(self, leased: Path) -> Path:
        dest = self.layout.done / leased.name
        os.rename(leased, dest)
        return dest

    def release_failed(self, leased: Path, error: str) -> None:
        host_failed = self.layout.failed / self.host
        host_failed.mkdir(parents=True, exist_ok=True)
        dest = host_failed / leased.name
        os.rename(leased, dest)
        (host_failed / f"{leased.name}.err.txt").write_text(error, encoding="utf-8")

    # ---- maintenance ----

    def reap_stale(self, *, stale_sec: int = 300) -> int:
        """Move files from dead workers' in_progress/<host>/ back to pending/.

        A worker is dead if its <host>.alive mtime is older than stale_sec.
        Returns count of files relocated.
        """
        now = time.time()
        count = 0
        for host_dir in self.layout.in_progress.iterdir():
            if not host_dir.is_dir():
                continue
            alive = self.layout.in_progress / f"{host_dir.name}.alive"
            if not alive.exists():
                continue
            if now - alive.stat().st_mtime <= stale_sec:
                continue
            for f in list(host_dir.iterdir()):
                os.rename(f, self.layout.pending / f.name)
                count += 1
            alive.unlink(missing_ok=True)
        return count

    def stats(self) -> dict[str, int]:
        def _count(d: Path) -> int:
            return sum(1 for x in d.iterdir() if not x.name.startswith("."))
        return {
            "pending": _count(self.layout.pending),
            "in_progress": sum(
                _count(d) for d in self.layout.in_progress.iterdir() if d.is_dir()
            ),
            "done": _count(self.layout.done),
            "failed": sum(
                _count(d) for d in self.layout.failed.iterdir() if d.is_dir()
            ),
        }
```

### `cli/cmd_queue.py`

```python
queue_app = typer.Typer(no_args_is_help=True,
                         help="Manage a shared-FS distributed work queue.")

@queue_app.command("init")
def cmd_init(queue_dir: Path) -> None:
    """Create the pending/in_progress/done/failed structure + verify atomic rename."""

@queue_app.command("add")
def cmd_add(queue_dir: Path, paths: list[Path]) -> None:
    """Enqueue one or more files for processing."""

@queue_app.command("status")
def cmd_status(queue_dir: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    """Show counts and (optionally) list in_progress holders."""

@queue_app.command("reset")
def cmd_reset(
    queue_dir: Path,
    include_failed: bool = typer.Option(False, "--include-failed"),
    older_than_days: int | None = typer.Option(None, "--done-older-than"),
) -> None:
    """Move stale in_progress (and optionally failed) entries back to pending."""
```

### `cli/cmd_worker.py`

```python
def worker_cmd(
    queue_dir: Path = typer.Argument(..., exists=True),
    profile: Path = typer.Option(..., "--profile"),
    out_dir: Path = typer.Option(..., "--out-dir"),
    encoder_override: str | None = typer.Option(None, "--encoder"),
    workers: int = typer.Option(1, "--workers"),
    work_dir: Path = typer.Option(Path(".yt_uniq_work"), "--work-dir"),
    stop_after_empty: bool = typer.Option(
        False, "--stop-after-empty",
        help="Exit when the queue has no pending files.",
    ),
    heartbeat_sec: float = typer.Option(30.0, "--heartbeat-sec"),
    poll_sec: float = typer.Option(5.0, "--poll-sec"),
    no_qa: bool = typer.Option(False, "--no-qa"),
) -> None:
    """Long-running worker that drains a queue."""
```

Поток:
1. Init: `FileQueue(queue_dir)`; loud heartbeat поток (`threading.Thread`).
2. На каждой итерации: `reap_stale()`; `lease()`.
3. Если пусто:
   - `--stop-after-empty` → break
   - иначе `time.sleep(poll_sec)` → continue
4. `orchestrator.run_full(plan, options)` на leased file.
5. Успех → `release_done(leased)`; ошибка → `release_failed(leased, traceback)`.
6. (опционально) QA.

### `cli/app.py` — регистрация

```python
app.add_typer(queue_app, name="queue")
app.command("worker")(worker_cmd)
```

### `docs/distributed.md`

- Поддерживаемые FS: NFSv4 с `noac`, ZFS, ext4 на shared block storage.
- Не поддерживаемые: NFSv3 без noac (rename не атомарен через client
  cache); SMB1 (нет атомарных rename across dirs); локальный
  cloud-mount без POSIX (`s3fs-fuse`, `goofys` — rename = copy+delete,
  ломает leasing).
- Пример docker-compose с двумя workers и общим NFS volume.

## Acceptance

```bash
# Machine A — set up + add files.
yt-uniq queue init /shared/queue
yt-uniq queue add /shared/queue ~/movies/*.mp4
yt-uniq queue status /shared/queue
# pending: 12, in_progress: 0, done: 0, failed: 0

# Machine A — start a worker.
yt-uniq worker /shared/queue \
  --profile /shared/profiles/cid_aware.yaml \
  --out-dir /shared/uniq/ \
  --encoder libx264 --workers 2 &

# Machine B (NFS client of /shared) — start another.
yt-uniq worker /shared/queue \
  --profile /shared/profiles/cid_aware.yaml \
  --out-dir /shared/uniq/ \
  --encoder h264_videotoolbox --workers 2 &

# A and B each pick different files, no doubles.
yt-uniq queue status /shared/queue --json
# {"pending": 8, "in_progress": 2, "done": 2, "failed": 0}

# Kill machine A's worker hard. Wait > 5 min.
# Then check:
yt-uniq queue status /shared/queue
# pending count should re-include the file A was holding (reaped).
```

## Tests

| Уровень | Файл | Что |
|---|---|---|
| Unit | `tests/unit/test_queue_layout.py` | init creates 4 dirs; `_verify_atomic_rename` fails if mocked rename raises |
| Unit | `tests/unit/test_lease_atomic.py` | two `FileQueue` instances (different host) lease concurrently in threads → each gets a unique file, no double-lease |
| Unit | `tests/unit/test_lease_empty.py` | pending empty → lease returns None |
| Unit | `tests/unit/test_release_done.py` | leased file → release_done moves it under done/ |
| Unit | `tests/unit/test_release_failed.py` | release_failed moves file + writes `.err.txt` |
| Unit | `tests/unit/test_heartbeat_reaper.py` | mtime > stale_sec → reap_stale returns count and file is in pending again |
| Unit | `tests/unit/test_queue_stats.py` | counts across all four dirs |
| Integration | `tests/integration/test_worker_drains_queue.py` | spawn 2 worker processes on localhost (subprocess), 4 tiny clips, after both finish: 4 in done/, 0 in pending/in_progress, no duplicates |
| Integration | `tests/integration/test_queue_cli.py` | init/add/status/reset via CliRunner |

## Risks

| Риск | Митигация |
|---|---|
| NFSv3 / SMB rename не атомарен через client cache | `_verify_atomic_rename` failит на init; docs/distributed.md перечисляет supported FS configs (требует `noac`) |
| Worker умер прямо во время `os.rename` (атомарность пострадала) | теоретически файл может остаться в pending/ ИЛИ in_progress/<host>/. lease() и reap_stale() обрабатывают оба случая (lease ищет по pending, reap по in_progress/<host>) |
| Двe machines на одной NFS share с разными mount options | `_verify_atomic_rename` гарантирует только локальную ОС; распределённое тестирование ручное в runbook |
| Worker сделал часть сегментов и умер — work_dir теряется | acceptable: следующий worker начинает с нуля. work_dir per-input per-host; никакого distributed work_dir resume (потребовал бы координатора) |
| `--out-dir` shared между workers, два worker'а пишут один output на одно имя | каждый worker сейчас выходит как `out_dir/<input.stem>.uniq.mp4`. Если конфликт — `out_dir/<host>/<pid>/<stem>.uniq.mp4` через atomic move в финал |
| Очередь распухает done/ за полгода работы | `yt-uniq queue reset --done-older-than 30` (v0.3.1) |
| Encoder варьируется между workers → variants одного фильма имеют разные H.264 params | by design: каждый worker — независимый variant. Если важна consistency — пользователь использует `--encoder libx264` явно на всех воркерах |

## Hand-off (release v0.3.0)

После Phase 13:
- 8 → 10 CLI команд: + `queue` (sub-app: init/add/status/reset) + `worker`.
- Очередь работает на NFS / ZFS / ext4 share без external dependencies.
- `tools/benchmark.py` можно расширить для distributed runs (отдельный
  скрипт `tools/queue_bench.py` — отложено в v0.3.1).
- Готово к `git tag v0.3.0` после Phase 11+12 закрытия.

## Что НЕ закрыто в v0.3 (явно)

- S3 / GCS / Azure Blob backend (отдельный driver — v0.4).
- Multi-encoder consistency между workers (variants одного входа могут
  иметь разные encoder-params).
- Web dashboard очереди.
- Priority / scheduling (FIFO simple sort).
