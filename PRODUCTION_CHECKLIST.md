# Production Release Checklist

Ни один пункт не считается выполненным по документации или mock-only test. Для media
capability нужен retained command/result artifact. Текущий audit baseline имеет
release blockers, поэтому checklist намеренно не отмечен как пройденный.

## 1. Scope and release identity

- [ ] Release commit/tag immutable; worktree clean.
- [ ] Version едина в package, CLI, GUI, web OpenAPI, artifacts и changelog.
- [ ] Stable API/Profile/CLI/Event changes имеют RFC, migration, snapshots и
      `CHANGELOG.md` entry.
- [ ] Supported Python, FFmpeg, OS и hardware matrix документирована.
- [ ] Legal-use framing сохранён; similarity metrics не называются доказательством
      обхода или вероятностью внешней rights-detection system.

## 2. Correctness blockers

- [x] 44.1 kHz pitch+tempo сохраняет expected duration; 48/96 kHz full matrix pending.
- [x] Final processed audio sample rate соответствует policy: 48 kHz.
- [x] Output с no audio transforms сохраняет selected main audio.
- [x] Negative AAC priming/edit-list PTS не сдвигает video на regression fixture.
- [x] Нет unintended frame drop на regression fixture (752/752); long-form matrix pending.
- [ ] A/V sync проходит start/end/impulse tests.
- [x] Chapters сохраняются согласно policy на MKV→MP4 regression fixture.
- [ ] Multi-audio language сохраняется; title/default disposition matrix pending.
- [ ] SRT→MP4 и image-subtitle preflight проверены; ASS/PGS/MKV/MOV matrix pending.
- [ ] Attachments/data streams сохраняются или явно объявлены unsupported.
- [x] SAR/DAR соответствует declared crop transform; square pixels не становятся
      anamorphic случайно.
- [x] `video.speed` и main-audio tempo сверяются; unsafe aux-stream retiming rejected.
- [x] Divergent window audio duration error ≤ one encoded audio frame (125 s fixture).
- [ ] Final output полностью декодируется без errors/warnings classified as corrupt.

## 3. SDR/HDR/color

- [ ] SDR BT.709 tags/range/pixel format verified.
- [ ] Full/limited range conversion explicit and tested.
- [ ] HDR10: 10-bit, PQ, Rec.2020 primaries/matrix, range, ST2086, MaxCLL/FALL verified.
- [ ] HLG: 10-bit, HLG/Rec.2020 metadata verified.
- [ ] HDR10+/Dolby Vision policy explicit: preserve with proof or reject early.
- [ ] HDR→SDR zscale/tonemap output BT.709 verified on dark/highlight/skin content.
- [ ] No SDR transform is accidentally applied in nonlinear HDR domain.
- [ ] NVENC/QSV/AMF/VideoToolbox/x265/AV1 HDR capability tested per advertised target.

## 4. Containers and cadence

- [ ] MP4 roundtrip: streams, metadata, chapters, faststart, no unintended edit list.
- [ ] MKV roundtrip: streams, attachments, subtitles, chapters.
- [ ] MOV roundtrip: timecode/edit-list/metadata policy.
- [ ] CFR: 23.976/24/25/29.97/30/50/59.94/60.
- [ ] VFR: single and multi-segment, software and advertised hardware encoders.
- [ ] Long-GOP, sparse keyframes, static scene and rapid scene-cut segmentation.
- [ ] Every concat seam checked against matching source interval.

## 5. Audio

- [ ] Mono, stereo, 5.1 and multiple tracks.
- [ ] 44.1, 48 and 96 kHz inputs.
- [ ] AAC/Opus and advertised passthrough codecs per target container.
- [x] Loudnorm measures actual pre-loudnorm chain, not original source.
- [x] Final integrated loudness within ±0.5 LU on regression fixture; corpus pending.
- [ ] Linear/dynamic normalization mode recorded; fallback not silent.
- [ ] Haas rejects non-stereo; reverb/compand/noise/pitch layout matrix pending.
- [ ] Boundary impulse/listening tests find no click, gap, repeat or accumulated shift.

## 6. Profiles and transforms

- [ ] All shipped YAML profiles validate and contract snapshot is updated.
- [x] `target_loudness_lufs`, `audio_tracks`, `output_container`, `target_vmaf` affect
      actual command or are removed through approved migration.
- [x] Total crop semantics match documented maximum.
- [ ] No-upscale default enforced unless user explicitly requests upscale.
- [ ] Transform compatibility graph rejects invalid HDR/audio/time/container combos.
- [ ] Each transform has purpose, quality/size/time data and expected ordering.
- [ ] Quality-first profile has corpus-backed VMAF/SSIM/PSNR/LUFS/size/time bands.
- [ ] Aggressive/destructive profiles are opt-in and visibly marked experimental.
- [ ] Random seeds reproduce exact plan/commands/output semantics on resume.

## 7. Encoders

- [ ] `--encoder` either selects exact requested encoder or fails clearly.
- [ ] Availability probe covers job codec, pixel format, bit depth, resolution and RC.
- [ ] Encoder cache invalidates on FFmpeg/GPU/driver/device changes and runtime failure.
- [ ] libx264/libx265/AV1 software paths verified.
- [ ] Advertised NVENC/QSV/AMF/VideoToolbox paths verified on real runner hardware.
- [ ] Per-device concurrency measured; jobs routed to a device, not only counted.
- [ ] GOP/keyframe/B-frame/profile/level policy documented per target.
- [ ] Hardware-first is not implicit in quality-first mode.

## 8. Resume, crash and long-form

- [x] Plan identity contains stable input content + full media topology fingerprint.
- [x] Same-size/source-replacement test cannot reuse stale artifacts.
- [ ] Completed segment SHA + media invariants verified before reuse.
- [x] Cached main audio and final output SHA/final contract verified before no-op.
- [x] Work-dir lock acquisition uses atomic exclusive creation.
- [ ] Cross-host lease has worker UUID/fencing and cannot be stolen while live.
- [x] Checkpoint lock is released in the shared orchestrator `finally` path.
- [ ] Kill/crash at each phase resumes without reprocessing valid completed segments.
- [ ] Corrupt/zero/truncated segment is reprocessed automatically.
- [ ] Low/full disk fails safely, preserves recoverable state and cleans partial output.
- [ ] 1 h, 2 h and 3 h+ tests pass with peak RAM/disk/elapsed artifacts retained.

## 9. Runner and observability

- [ ] FFmpeg logs stream incrementally to disk with bounded in-memory tail.
- [ ] Silent stall watchdog and configurable wall policy tested.
- [ ] Cancellation terminates complete process tree on Linux/macOS/Windows.
- [x] Parallel first failure cancels sibling work even without external token.
- [ ] Run/plan/job/segment correlation IDs appear in structured logs/events.
- [ ] Metrics distinguish queued, active, failed, cancelled, resumed and completed work.
- [ ] Sensitive paths/tokens are not exposed in public logs/metrics.

## 10. QA and calibration

- [x] Orchestrator enforces stream/duration/HDR correctness before success.
- [x] Known QA topology correctness failures cannot receive GREEN.
- [ ] Raw and spatial/temporal registered quality metrics both reported.
- [ ] No silent substitution of VMAF with SSIM/pHash under one threshold.
- [ ] Audio fingerprint covers stratified full duration and preserves temporal ordering.
- [x] SSCD direction verified: higher cosine means higher similarity.
- [ ] SSCD frame extraction is batched and reports temporal coverage/confidence.
- [ ] Similarity diagnostics clearly separated from image/audio quality.
- [ ] Corpus IDs/content cache survive move and invalidate on content change.
- [x] Calibration fixed seed/common random numbers reproduce trials.
- [ ] Calibration clips cover opening/middle/end plus representative motion/audio.
- [x] Failed trial is retried/aborted, never converted into optimization score.
- [ ] Search handles non-monotone objective and returns feasible Pareto candidate only.
- [ ] Old inverted SSCD calibration results are invalidated/migrated.

## 11. Web and distributed operation

- [ ] Production bind requires documented auth/TLS/reverse-proxy policy.
- [ ] Global bounded job scheduler and CPU/GPU/disk quotas enabled.
- [x] Duplicate active output reservation returns conflict within one web process.
- [ ] Run state persists across restart and completed records have TTL/pruning.
- [x] SSE full queue cannot block processing finalizer.
- [ ] Web and distributed worker run the same mandatory final QA/correctness gates.
- [ ] Two worker processes on one host have independent liveness.
- [ ] Reaped job retains stable work/resume identity on another host.
- [ ] Shared filesystem/NFS configuration is qualified under concurrent lease/reap.

## 12. Security and supply chain

- [ ] Ruff, strict mypy, tests, coverage gate pass.
- [ ] CodeQL has zero open high/critical alerts or documented accepted risk.
- [ ] Dependency vulnerability scan reviewed; lock files reproducible.
- [ ] Plugin manifest/sandbox/allowlist tests pass; pre-import disable documented.
- [ ] Web path traversal/symlink/upload size/rate/concurrency tests pass.
- [ ] Container runs non-root with read-only/minimal permissions where practical.
- [ ] Wheel/GUI/container SBOM and signatures generated and verified.
- [ ] Secrets absent from repository, logs and built artifacts.

## 13. Build and release commands

- [x] `make lint`
- [x] `make typecheck`
- [x] `make test-unit`
- [x] `make test-integration`
- [x] Full contracts/property/GUI/offscreen suite (`make check`: 1330 passed)
- [ ] Visual suite or approved retained baseline review
- [x] Profile validation for all shipped profiles
- [ ] FFmpeg SDR/HDR/container smoke matrix
- [x] `make build-wheel` and clean-environment import smoke (1.3.1 code candidate; 1.3.2 release rebuild)
- [x] `make build` GUI artifact on local macOS; Linux/Windows CI artifacts pending
- [ ] Docker multi-arch build/start/health/process smoke
- [ ] Benchmark comparison against approved baseline
- [ ] Production risk register reviewed; no unaccepted P0/P1

## Текущий статус candidate v1.3.2

- [x] Unit/contracts/property: 1159 passed, 2 skipped.
- [x] Integration/smoke: 98 passed, 9 skipped.
- [x] Ruff: passed.
- [x] Strict mypy: passed (155 source files).
- [x] All 16 shipped profiles load.
- [x] Подтверждённые local P0 correctness regressions исправлены.
- [ ] HDR/rubberband/SSCD real-model: **NOT VERIFIED** (local capabilities missing).
- [ ] 1h/2h/3h+, 4K and full hardware matrix: **NOT VERIFIED**.
- [ ] Полная advertised production matrix: **BLOCKED** до закрытия `NOT VERIFIED`.
