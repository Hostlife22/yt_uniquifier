# Production Release Checklist

Ни один пункт не считается выполненным по документации или mock-only test. Для media
capability нужен retained command/result artifact. Текущий audit baseline имеет
release blockers, поэтому checklist намеренно не отмечен как пройденный.

## 1. Scope and release identity

Extended Intel Mac review (2026-09-06): manual release dry-run `34026381605`
passed at `1305a3a`; six artifact checksums and seven OIDC/cosign bundles were
independently verified locally. Docker `34027164490` passed both architectures at
`eeda84a` with publication disabled. Subsequent timestamp fixes still need
their own committed CI/release evidence. The correct registry package uses a hyphen:
`ghcr.io/hostlife22/yt-uniquifier`, not the repository's underscore. Its `edge`
digest `sha256:be51edeaf94f6d2d7878e7c20633e9099024a2ea5dca71eba2d352fc3faba210`
has verified amd64/arm64 manifests, per-platform SPDX/SLSA and a valid cosign OIDC
signature. However it identifies old revision `09ac191`, not current HEAD.
The earlier HTTP 403 was for the wrong package path and is not an access blocker.
Manual Docker qualification can now use `publish=false` to run both architecture
smokes without replacing any registry tag; a current release image is still pending.

Natural 5.1 review exposed encoded peak overshoot: the complete stream measured
+0.27 dBTP despite loudnorm reporting -1.50 dBTP before delivery. The local fix
re-rendered audio from the original with linked headroom and measured -3.38 dBTP,
with no full-scale samples in the reviewed excerpt. Stereo/5.1 regression tests
pass locally. Cross-platform CI and human loudness/listening acceptance remain
required. The earlier +1.40 dBTP input-seek excerpt was not full-file evidence.

- [ ] Release commit/tag immutable; worktree clean.
- [x] Package metadata, CLI command/option, GUI runtime, web OpenAPI, wheel and local
      macOS bundle runtime/plist agree on `1.5.0`; tagged Linux/Windows artifacts remain
      covered by the immutable-release gate below.
- [x] Stable API/Profile/CLI/Event changes имеют RFC, migration, snapshots и
      `CHANGELOG.md` entry.
- [x] Supported Python, FFmpeg, OS и hardware matrix документирована; unavailable
      hardware remains explicitly `NOT VERIFIED` rather than advertised as qualified.
- [x] Legal-use framing сохранён; similarity metrics не называются доказательством
      обхода или вероятностью внешней rights-detection system.

## 2. Correctness blockers

- [x] 44.1/48/96 kHz pitch+tempo сохраняет expected duration и explicit 48 kHz output.
- [x] Final processed audio sample rate соответствует policy: 48 kHz.
- [x] Output с no audio transforms сохраняет selected main audio.
- [x] Negative AAC priming/edit-list PTS не сдвигает video на regression fixture.
- [x] Нет unintended frame drop на regression fixture (752/752); long-form matrix pending.
- [x] A/V sync проходит start/end и internal flash/impulse tests на software path;
      hardware and natural-content listening matrix pending.
- [x] Chapters сохраняются согласно policy на MKV→MP4 regression fixture.
- [x] Selected multi-audio/subtitle language, title и supported dispositions
      сохраняются и валидируются вместе с supported auxiliary streams.
- [x] SRT/ASS→MP4/MKV/MOV codec conversion and language/title retention verified.
- [x] MIT-licensed real PGS fixture is byte-identical through MKV; MP4/MOV fail
      preflight before encode.
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
- [x] SDR full/limited range preservation is explicit in FFmpeg and x264 arguments
      and survives real segment encode, concat and final validation.
- [x] Synthetic HDR10: 10-bit, PQ, Rec.2020, ST2086 и MaxCLL/FALL verified на x265.
- [x] Synthetic HLG: 10-bit, HLG/Rec.2020/limited range и A/V timeline verified на
      libx265 и HEVC VideoToolbox этого Mac.
- [x] HDR10+/Dolby Vision policy explicit: dynamic metadata rejected early.
- [ ] HDR→SDR zscale/tonemap output BT.709 is exercised on a checksum-pinned
      natural-scene derived PQ/HLG corpus; native-camera licensed HDR and human
      display review remain required before checking this production gate.
- [x] Undefined HDR→8-bit output policy and tonemap after another video transform
      fail before encode; HDR-preserving colour operations use explicit planar-float
      RGB linear light and return to the source matrix/10-bit format.
- [ ] Full NVENC/QSV/AMF/VideoToolbox/x265/AV1 HDR capability tested per advertised
      target; x265 HDR10/HLG and Intel HEVC VideoToolbox HLG pass, while static HDR10
      VideoToolbox fails closed and the remaining vendor/natural matrix is open.

## 4. Containers and cadence

- [x] MP4 regression roundtrip: streams, metadata, chapters, subtitles, AAC priming
      bounds и faststart (`moov` before `mdat`).
- [x] MKV roundtrip: streams, byte-identical attachment, SRT/ASS, chapters.
- [x] MOV roundtrip: `tmcd`, edit-list/audio bounds, SRT/ASS conversion и metadata.
- [x] CFR: software libx264 23.976/24/25/29.97/30/50/59.94/60 сохраняет frame/PTS.
- [x] VFR: multi-segment libx264 and H.264/HEVC VideoToolbox preserve 220/220 frames
      and the 30/20/60 FPS cadence; other hardware vendors remain pending.
- [x] Synthetic long-GOP/sparse keyframes, static scene and rapid scene-cut
      segmentation verified; natural licensed corpus remains pending.
- [x] Every concat seam checked against its matching source interval on the
      deterministic sparse long-GOP fixture; full natural corpus remains pending.

## 5. Audio

- [x] Mono, stereo and 5.1 topology passes the real transform matrix; multiple
      selected tracks pass the full container pipeline.
- [x] 44.1, 48 and 96 kHz pitch inputs pass and produce explicit 48 kHz output.
- [x] Main AAC plus secondary Opus is container-qualified: Opus is copied in MKV
      and explicitly transcoded to AAC for MP4/MOV while metadata is preserved.
- [x] Loudnorm measures actual pre-loudnorm chain, not original source.
- [x] Final integrated loudness within ±0.5 LU on regression fixture; corpus pending.
- [x] Requested and FFmpeg-reported loudnorm modes are recorded in the audio log
      and emitted as a RunEvent; unusable-measurement and FFmpeg-selected dynamic
      fallbacks have distinct explicit reasons.
- [x] Haas rejects non-stereo; reverb/compand/noise/pitch plus EQ/smear/resample
      preserve mono, stereo and 5.1 channel counts in the real FFmpeg matrix.
- [x] Synthetic impulses before/on/after two window crossfades retain exactly one
      event each within 30 ms, with no repeat/drop or accumulated duration shift.
- [x] Exact final decoded frame count, normalized 48 kHz audio sample count and
      per-stream end delta are bounded across MP4/MOV/MKV; post-transform `asetpts`
      prevents loudnorm PTS gaps from truncating the AAC tail.
- [ ] Natural speech/music listening tests find no click or audible tonal transition.

## 6. Profiles and transforms

- [x] All shipped YAML profiles validate and additive schema snapshots are updated.
- [x] `target_loudness_lufs`, `audio_tracks`, `output_container`, `target_vmaf` affect
      actual command or are removed through approved migration.
- [x] Total crop semantics match documented maximum.
- [x] No-upscale default enforced unless user explicitly requests upscale; shipped
      fixed-canvas profiles retain compatibility through explicit `allow_upscale: true`.
- [x] Transform compatibility graph rejects invalid HDR/audio/time/container combos.
- [x] Invalid `target_vmaf` feedback rejects unregistered geometry/time/overlay/
      tonemap paths before encoding, including direct segmenter calls.
- [x] Preflight surfaces implicit/explicit upscale, multiple resampling,
      noise+sharpen, temporal jitter and compound audio-effect quality risks.
- [x] Each transform has purpose, quality/size/time data and expected ordering.
- [ ] Quality-first profile has corpus-backed VMAF/SSIM/PSNR/LUFS/size/time bands.
- [x] Aggressive/destructive profiles are opt-in by explicit profile selection and
      visibly marked experimental; corpus-backed safe operating bounds remain pending.
- [x] Resume restores the persisted run seed, leaves completed segment/audio artifacts
      untouched and reproduces exact decoded SHA-256 for video and audio.

## 7. Encoders

- [x] `--encoder` either selects exact requested encoder or fails clearly.
- [x] Availability probe covers job codec, pixel format, bit depth, resolution and RC.
- [x] Encoder cache includes FFmpeg/OS/GPU signature; an exact-job success is
      invalidated after a later runtime encode/device failure and must be reprobed.
- [x] libx264/libx265/SVT-AV1/libaom-AV1 software paths verified locally; software
      HEVC/AV1 bitstreams also pass in the Debian 12 production container.
- [x] Manual self-hosted hardware qualification fails closed for explicitly requested
      encoders and retains JUnit, encoded media, hashes, FFprobe/FFmpeg and GPU evidence;
      final run `33966394736` on `6aa0720` passed 22 mandatory VideoToolbox tests,
      retained 39 hashed/probed media artifacts and removed its ephemeral runner.
- [x] H.264 + HEVC VideoToolbox are bitstream-verified on this Intel Mac for
      SDR/HLG, CFR/VFR, 1080p/4K, GOP/profile/level, two sessions, cancellation and
      fallback; hosted Apple Silicon H.264 passes the explicit half-second IDR contract.
- [ ] Advertised NVENC/QSV/AMF and AV1 VideoToolbox paths verified on matching real
      runner hardware; these devices are unavailable on the current Mac.
- [ ] Per-device concurrency measured and jobs routed to a device, not only counted;
      the two-session Intel VideoToolbox limit is measured, routing remains open.
- [x] libx264 H.264 policy is explicit and bitstream-tested: High Profile, CABAC,
      max two B-frames and closed half-frame-rate GOP; plan hash invalidates old
      resume artifacts when the policy changes.
- [x] H.264 VideoToolbox requests the same bounded GOP plus explicit half-second IDRs;
      this prevents the 21-frame gap observed after Apple Silicon accepted `-g 12`.
- [ ] NVENC/QSV/AMF and AV1 VideoToolbox GOP/profile/level behavior is
      bitstream-qualified per advertised target. Local libx265/SVT-AV1/libaom and
      H.264/HEVC VideoToolbox paths have explicit, real-output coverage.
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
- [x] Kill/crash at each full media phase resumes without reprocessing valid completed
      segments; seven-boundary probe/plan/segment/audio/concat/validation/publication
      process-group SIGKILL matrix, deterministic audio/concat/final-replace faults,
      POSIX random SIGKILL,
      and all four distributed publication boundaries are covered, but reboot/power-loss
      and every encode phase boundary are not yet qualified.
- [x] Corrupt/zero/truncated segment is reprocessed automatically.
- [x] Low/full disk fails safely: checkpoint `fsync`, audio, concat and final replace
      injections plus a real bounded ENOSPC tmpfs preserve prior artifacts and clean
      partials. Power-loss durability remains a separate deployment qualification.
- [x] Synthetic 1 h, 2 h and 3 h tests pass with RAM/disk/elapsed results retained in
      `BENCHMARKS.md`; a public-domain natural 95-minute film additionally preserves
      171345 frames and, after exact audio-tail padding, 274426152 samples with a
      4.5 ms A/V end delta. Licensed 2 h/3 h+ corpus remains required.

## 9. Runner and observability

- [x] FFmpeg logs stream incrementally to disk with bounded in-memory tail.
- [x] Silent stall watchdog and configurable wall policy tested with real subprocesses.
- [x] Segment encode, concat and sanitizer use the same bounded-log/watchdog runner;
      no fixed one-hour concat/sanitizer termination remains.
- [x] Cancellation terminates complete process tree on Linux/macOS/Windows; POSIX
      shell-grandchild/watchdog paths pass locally and the six-cell native CI matrix
      passed in final run `33966344170` at commit `6aa0720`.
- [x] Parallel first failure cancels sibling work even without external token.
- [x] Run/plan/job/segment correlation IDs appear in structured logs/events.
- [x] Metrics distinguish queued, active, failed, cancelled, resumed and completed work.
- [x] Sensitive paths/tokens are not exposed in public logs/metrics; metric labels
      use bounded state/encoder vocabularies and never correlation/path labels.

## 10. QA and calibration

- [x] Orchestrator enforces stream/duration/HDR correctness before success.
- [x] Known QA topology/timestamp/decode correctness failures produce `INVALID`.
- [x] Raw and spatial/temporal registered quality metrics are both reported from
      `main` under RFC #12 without changing raw verdict semantics.
- [x] No low VMAF substitution and no pHash-as-quality fallback; SSIM is used only
      when VMAF is unavailable and retains its metric identity.
- [x] Audio fingerprint uses ordered start/middle/tail windows for >600 s media and
      derives all fields from one extraction per file; natural-film corpus pending.
- [x] SSCD direction verified: higher cosine means higher similarity.
- [x] SSCD midpoint frame extraction is batched into one cancellable FFmpeg runner
      process per file and returns the complete requested grid.
- [x] SSCD reports bounded monotonic temporal coverage/confidence and supports a
      cached plan-registered comparison from `main`.
- [x] CLI/HTML similarity diagnostics are clearly separated from correctness/quality;
      structured JSON axis fields await an approved stable-schema RFC.
- [x] Corpus IDs/content cache survive move and invalidate on content change; legacy
      schema-v1 SQLite stores upgrade in place.
- [x] Calibration fixed seed/common random numbers reproduce trials.
- [x] Calibration's fixed time budget covers opening/middle/end with video/audio;
      natural-content representativeness remains a release qualification item.
- [x] Failed trial is retried/aborted, never converted into optimization score.
- [x] Bounded search explores non-monotone intervals and selects feasible candidates
      by quality before intensity/similarity; non-feasible fallback is explicit.
- [x] Durable calibration cache uses v2 semantics, so old/inverted SSCD scores cannot
      load; profiles exported by older builds must be recalibrated manually.

## 11. Web and distributed operation

- [x] Production exposure policy requires TLS/reverse proxy; Basic Auth over raw
      untrusted HTTP is explicitly prohibited and examples retain loopback publish.
- [ ] Global bounded resource scheduler and CPU/GPU/disk quotas enabled; the web
      run-count cap, encoder slots and measured-progress disk-byte reservations are
      shared across local processes; reference Compose enforces CPU/RAM/PID ceilings.
      Exact per-device routing, native hard filesystem/OS quotas,
      mixed-UID/visibility deployments and NFS remain open.
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
- [ ] Production shared filesystem/NFS configuration is qualified under concurrent
      lease/reap and final-output reservation. The expanded ephemeral two-client NFSv4
      lab passes network partition, four SIGKILL boundaries, corrupt checkpoint,
      repeated recovery and ENOSPC; production hard mounts and separate physical hosts
      remain gates.

## 12. Security and supply chain

- [x] Ruff, strict mypy and full local production gate pass (`1725 passed`,
      `55 expected skips`); remote Ubuntu/Python 3.12 branch coverage is `81.23%`
      (`1497 passed`, 12 expected skips), above the required 80%.
- [x] Final six-cell CI run `33966344170` passed on `6aa0720` for
      Linux/macOS/Windows with Python 3.11/3.12.
- [x] CodeQL v4 run `33966344225` passed on `6aa0720`; alert #13 is fixed and the
      GitHub API reports zero open code-scanning alerts.
- [x] Base/dev/GUI hash-lock reproducible and `pip-audit` clean; Intel macOS ML
      exception documented and restricted to the pinned official SSCD checkpoint.
- [x] Plugin manifest/sandbox/allowlist tests pass; pre-import disable documented.
- [x] Web path traversal/symlink/body-size/rate/concurrency tests pass; missing,
      malformed, negative, duplicate, transfer-encoding-conflicting and over-limit
      Content-Length fail closed.
- [x] Container runs as non-root UID 1000; input mount is documented read-only.
- [x] Manual release workflow run `33964477926` generated Linux/macOS/Windows GUI
      bundles, AppImage, CycloneDX 1.5 SBOM, verified SHA-256 checksums and seven
      keyless cosign bundles without publishing a tag. The downloaded candidate and
      clean-Ubuntu AppImage runtime were independently verified locally.
- [ ] Verify Docker buildx SBOM/provenance/signature and the actual immutable v1.5
      release assets when the approved release tag is created.
- [x] Gitleaks found no secrets across 324 commits or the local ~174 MB build
      artifacts; persisted web errors remain redacted by regression tests.

## 13. Build and release commands

- [x] `make lint`
- [x] `make typecheck`
- [x] `make test-unit`
- [x] `make test-integration`
- [x] Full contracts/property/GUI/offscreen suite (`make check`:
      1725 passed, 55 expected hardware-selection/optional skips)
- [x] Visual suite passed locally with optional QtCharts backend accounted for
- [x] Profile validation for all shipped profiles
- [x] FFmpeg synthetic SDR/HDR10/HLG and MP4/MKV/MOV core smoke matrix on this Mac.
- [x] `make build-wheel` produced `yt_uniquifier-1.5.0-py3-none-any.whl`
- [x] `make build` GUI artifact on local macOS; Linux/macOS/Windows GUI archives and
      embedded `1.5.0` versions passed manual release run `33964477926`.
- [x] Local `linux/amd64` Docker build/start smoke: non-root UID 1000, `/healthz`
      and `/readyz` pass, shared resource-registry path is writable.
- [x] Docker multi-arch build/start/health/process smoke for `linux/amd64` and
      QEMU-emulated `linux/arm64` on Docker Desktop/macOS; native Linux CI remains
      the release gate.
- [x] One-command rights-attested current/proposed corpus runner is prepared under
      `validation-corpus/`; exact Plan, VMAF/SSIM/PSNR/LUFS/true peak/size/time/RAM and
      JSON/CSV/HTML output passed synthetic smoke. Licensed media remains `NOT VERIFIED`.
- [x] Scheduled performance workflow on final `6aa0720` passed in run `33966849505`:
      wall time +0.8% and peak RSS +0.2% against the prior same-runner baseline,
      within the 15% hard threshold; no regression issue was created.
- [ ] Benchmark comparison against approved baseline
- [ ] Production risk register reviewed; no unaccepted P0/P1

## Текущий статус post-v1.5.0 main

- [x] Fully provisioned production `make check`: 1725 passed, 55 expected skips;
      fault-injection recovery matrix and three-round POSIX SIGKILL chaos gate passed.
- [x] Remote non-integration branch coverage gate: 81.23% (required: 80%).
- [x] Heavy GUI real-FFmpeg E2E: 2 passed; one 320×180 VideoToolbox case
      correctly skipped after exact job capability rejection.
- [x] Ruff: passed.
- [x] Strict mypy: passed (162 source files).
- [x] All 16 shipped profiles load.
- [x] Real Calibration v2 CLI: stratified probe, 3 encode/quality/similarity trials,
      strict media contract, tuned YAML and second-run scored-cache reuse passed.
- [x] Подтверждённые local P0 correctness regressions исправлены.
- [x] HDR10/HDR→SDR, Rubber Band и SSCD real model verified locally.
- [x] Synthetic 1h/2h/3h, 4K AV1 и VideoToolbox H.264/HEVC smoke verified locally.
- [x] Final `6aa0720` evidence: six-cell CI `33966344170`, CodeQL `33966344225`,
      Intel hardware qualification `33966394736` and perf regression `33966849505`.
- [ ] Полная advertised production matrix: **BLOCKED** для licensed 2 h/3 h+ long-form,
      NVENC/QSV/AMF, NFS cross-host и YouTube round-trip. HLG и VideoToolbox
      concurrency закрыты на текущем Intel Mac.
