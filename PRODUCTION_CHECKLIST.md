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
- [x] A/V sync проходит start/end и internal flash/impulse tests на software path;
      hardware and natural-content listening matrix pending.
- [x] Chapters сохраняются согласно policy на MKV→MP4 regression fixture.
- [x] Selected multi-audio/subtitle language, title и supported dispositions
      сохраняются и валидируются вместе с supported auxiliary streams.
- [ ] SRT/ASS→MP4/MKV/MOV и image-subtitle preflight проверены; real PGS
      roundtrip остаётся NOT VERIFIED из-за отсутствия fixture encoder.
- [x] MKV attachments, MOV `tmcd` и MP4 JPEG/PNG `attached_pic` сохраняются;
      несовместимые attachment/data/cover-art combinations явно rejected.
- [x] SAR/DAR соответствует declared crop transform; square pixels не становятся
      anamorphic случайно.
- [x] `video.speed` и main-audio tempo сверяются; unsafe aux-stream retiming rejected.
- [x] Divergent window audio duration error ≤ one encoded audio frame (125 s fixture).
- [x] MP4/MKV/MOV regression outputs полностью декодируются по всем A/V streams;
      subtitle/data topology проверяется отдельно, без ложного `null`-mux failure.

## 3. SDR/HDR/color

- [x] Synthetic SDR BT.709: transfer/primaries/matrix/limited range/yuv420p verified.
- [ ] Full/limited range conversion explicit and tested.
- [x] Synthetic HDR10: 10-bit, PQ, Rec.2020, ST2086 и MaxCLL/FALL verified на x265.
- [x] Synthetic HLG: 10-bit, HLG/Rec.2020/limited range и A/V timeline verified на
      libx265 и HEVC VideoToolbox этого Mac.
- [x] HDR10+/Dolby Vision policy explicit: dynamic metadata rejected early.
- [ ] HDR→SDR zscale/tonemap output BT.709 verified on dark/highlight/skin content.
- [ ] No SDR transform is accidentally applied in nonlinear HDR domain.
- [ ] NVENC/QSV/AMF/VideoToolbox/x265/AV1 HDR capability tested per advertised target.

## 4. Containers and cadence

- [x] MP4 regression roundtrip: streams, metadata, chapters, subtitles, AAC priming
      bounds и faststart (`moov` before `mdat`).
- [x] MKV roundtrip: streams, byte-identical attachment, SRT/ASS, chapters.
- [x] MOV roundtrip: `tmcd`, edit-list/audio bounds, SRT/ASS conversion и metadata.
- [x] CFR: software libx264 23.976/24/25/29.97/30/50/59.94/60 сохраняет frame/PTS.
- [x] VFR: multi-segment libx264 preserves 220/220 frames and 30/20/60 FPS cadence;
      advertised hardware encoders remain pending.
- [x] Synthetic long-GOP/sparse keyframes, static scene and rapid scene-cut
      segmentation verified; natural licensed corpus remains pending.
- [x] Every concat seam checked against its matching source interval on the
      deterministic sparse long-GOP fixture; full natural corpus remains pending.

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

- [x] All shipped YAML profiles validate and additive schema snapshots are updated.
- [x] `target_loudness_lufs`, `audio_tracks`, `output_container`, `target_vmaf` affect
      actual command or are removed through approved migration.
- [x] Total crop semantics match documented maximum.
- [ ] No-upscale default enforced unless user explicitly requests upscale.
- [ ] Transform compatibility graph rejects invalid HDR/audio/time/container combos.
- [x] Invalid `target_vmaf` feedback rejects unregistered geometry/time/overlay/
      tonemap paths before encoding, including direct segmenter calls.
- [x] Preflight surfaces implicit/explicit upscale, multiple resampling,
      noise+sharpen, temporal jitter and compound audio-effect quality risks.
- [ ] Each transform has purpose, quality/size/time data and expected ordering.
- [ ] Quality-first profile has corpus-backed VMAF/SSIM/PSNR/LUFS/size/time bands.
- [x] Aggressive/destructive profiles are opt-in by explicit profile selection and
      visibly marked experimental; corpus-backed safe operating bounds remain pending.
- [ ] Random seeds reproduce exact plan/commands/output semantics on resume.

## 7. Encoders

- [x] `--encoder` either selects exact requested encoder or fails clearly.
- [x] Availability probe covers job codec, pixel format, bit depth, resolution and RC.
- [ ] Encoder cache includes FFmpeg/OS/GPU signature; invalidation after a later runtime
      device failure remains open.
- [x] libx264/libx265/SVT-AV1 software paths verified locally.
- [ ] Advertised NVENC/QSV/AMF/VideoToolbox paths verified on real runner hardware;
      H.264 + HEVC VideoToolbox verified on this Intel Mac, other vendors unavailable.
- [ ] Per-device concurrency measured; jobs routed to a device, not only counted.
- [ ] GOP/keyframe/B-frame/profile/level policy documented per target.
- [x] Hardware-first is not implicit in quality-first mode; `quality` is default and
      `balanced|speed` require an explicit environment policy.

## 8. Resume, crash and long-form

- [x] Plan identity contains stable input content + full media topology fingerprint.
- [x] Same-size/source-replacement test cannot reuse stale artifacts.
- [x] Changed `--segment-sec` topology invalidates same-plan checkpoint state.
- [x] Completed segment SHA + existence/size are verified before reuse; full per-segment
      media manifest remains future hardening.
- [x] Cached main audio and final output SHA/final contract verified before no-op.
- [x] Work-dir lock acquisition uses atomic exclusive creation.
- [ ] Cross-host lease has worker UUID/fencing and cannot be stolen while live;
      process UUID, continuous CLI/GUI heartbeat and pre-publish fence verified on APFS,
      real NFS/network-partition qualification remains open.
- [x] Checkpoint lock is acquired before `--new-variant` mutates state and is released
      for checkpoint initialization, observer startup and processing failures.
- [ ] Kill/crash at each phase resumes without reprocessing valid completed segments;
      deterministic audio/concat/final-replace faults and POSIX random SIGKILL are
      covered, but reboot/power-loss and every phase boundary are not yet qualified.
- [x] Corrupt/zero/truncated segment is reprocessed automatically.
- [ ] Low/full disk fails safely: injected checkpoint `fsync`, audio, concat and final
      replace failures preserve prior artifacts and clean partials; a real disposable
      full-volume/reboot qualification is still required.
- [x] Synthetic 1 h, 2 h and 3 h tests pass with RAM/disk/elapsed results retained in
      `BENCHMARKS.md`; natural licensed corpus remains required.

## 9. Runner and observability

- [x] FFmpeg logs stream incrementally to disk with bounded in-memory tail.
- [x] Silent stall watchdog and configurable wall policy tested with real subprocesses.
- [x] Segment encode, concat and sanitizer use the same bounded-log/watchdog runner;
      no fixed one-hour concat/sanitizer termination remains.
- [ ] Cancellation terminates complete process tree on Linux/macOS/Windows.
- [x] Parallel first failure cancels sibling work even without external token.
- [ ] Run/plan/job/segment correlation IDs appear in structured logs/events.
- [ ] Metrics distinguish queued, active, failed, cancelled, resumed and completed work.
- [ ] Sensitive paths/tokens are not exposed in public logs/metrics.

## 10. QA and calibration

- [x] Orchestrator enforces stream/duration/HDR correctness before success.
- [x] Known QA topology/timestamp/decode correctness failures produce `INVALID`.
- [ ] Raw and spatial/temporal registered quality metrics both reported.
- [x] No low VMAF substitution and no pHash-as-quality fallback; SSIM is used only
      when VMAF is unavailable and retains its metric identity.
- [x] Audio fingerprint uses ordered start/middle/tail windows for >600 s media and
      derives all fields from one extraction per file; natural-film corpus pending.
- [x] SSCD direction verified: higher cosine means higher similarity.
- [ ] SSCD frame extraction is batched and reports temporal coverage/confidence.
- [x] CLI/HTML similarity diagnostics are clearly separated from correctness/quality;
      structured JSON axis fields await an approved stable-schema RFC.
- [ ] Corpus IDs/content cache survive move and invalidate on content change.
- [x] Calibration fixed seed/common random numbers reproduce trials.
- [x] Calibration's fixed time budget covers opening/middle/end with video/audio;
      natural-content representativeness remains a release qualification item.
- [x] Failed trial is retried/aborted, never converted into optimization score.
- [x] Bounded search explores non-monotone intervals and selects feasible candidates
      by quality before intensity/similarity; non-feasible fallback is explicit.
- [x] Durable calibration cache uses v2 semantics, so old/inverted SSCD scores cannot
      load; profiles exported by older builds must be recalibrated manually.

## 11. Web and distributed operation

- [ ] Production bind requires documented auth/TLS/reverse-proxy policy.
- [ ] Global bounded resource scheduler and CPU/GPU/disk quotas enabled; the web
      run-count cap is shared across processes using one output directory and
      in-process CPU/vendor semaphores are complete, while cross-process per-device
      locks and disk-byte reservation remain open.
- [x] Duplicate active output reservation returns conflict across web processes sharing
      a local filesystem; exact-owner release and dead same-host recovery are tested.
- [x] Run state persists across restart and completed records have TTL/count pruning.
- [x] SSE full queue cannot block processing finalizer.
- [x] Web and distributed worker run the same mandatory media-contract and complete
      primary-video/all-audio decode gates through `run_full`; rich similarity/quality
      reports remain optional diagnostics.
- [x] Two worker processes on one host receive independent host+PID+nonce liveness IDs.
- [x] Crash between a journal-specific token fence and final rename is recovered from
      a durable journal; old same-name markers and unfenced staged outputs cannot
      authorize publication.
- [ ] Reaped job retains stable work/resume identity on another host.
- [ ] Shared filesystem/NFS configuration is qualified under concurrent lease/reap and
      final-output reservation; foreign-host stale owners intentionally fail closed.

## 12. Security and supply chain

- [x] Ruff, strict mypy and full local test gate pass (`1519 passed`, `2 skipped`);
      local CI-equivalent core branch coverage is `81.95%` (`1418 passed`, one
      expected skip), above the required 80%; remote commit gate remains required.
- [x] CodeQL has zero open alerts before this change; post-push commit scan is a
      required final gate.
- [x] Base/dev/GUI hash-lock reproducible and `pip-audit` clean; Intel macOS ML
      exception documented and restricted to the pinned official SSCD checkpoint.
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
- [x] Full contracts/property/GUI/offscreen suite (`make check`: 1509 passed, 2 skipped)
- [x] Visual suite passed locally with optional QtCharts backend accounted for
- [x] Profile validation for all shipped profiles
- [x] FFmpeg synthetic SDR/HDR10/HLG and MP4/MKV/MOV core smoke matrix on this Mac.
- [x] `make build-wheel` and clean-environment import smoke for v1.4.0
- [x] `make build` GUI artifact on local macOS; Linux/Windows CI artifacts pending
- [ ] Docker multi-arch build/start/health/process smoke
- [ ] Benchmark comparison against approved baseline
- [ ] Production risk register reviewed; no unaccepted P0/P1

## Текущий статус post-v1.4.0 main

- [x] Fully provisioned `make check`: 1509 passed, 2 expected skips; fault-injection
      recovery matrix and three-round POSIX SIGKILL chaos gate passed on this Mac.
- [x] CI-equivalent non-integration branch coverage gate: 81.92% (required: 80%).
- [x] Heavy GUI real-FFmpeg E2E: 2 passed; one 320×180 VideoToolbox case
      correctly skipped after exact job capability rejection.
- [x] Ruff: passed.
- [x] Strict mypy: passed (157 source files).
- [x] All 16 shipped profiles load.
- [x] Real Calibration v2 CLI: stratified probe, 3 encode/quality/similarity trials,
      strict media contract, tuned YAML and second-run scored-cache reuse passed.
- [x] Подтверждённые local P0 correctness regressions исправлены.
- [x] HDR10/HDR→SDR, Rubber Band и SSCD real model verified locally.
- [x] Synthetic 1h/2h/3h, 4K AV1 и VideoToolbox H.264/HEVC smoke verified locally.
- [ ] Полная advertised production matrix: **BLOCKED** для natural long-form,
      NVENC/QSV/AMF, NFS cross-host и YouTube round-trip. HLG и VideoToolbox
      concurrency закрыты на текущем Intel Mac.
