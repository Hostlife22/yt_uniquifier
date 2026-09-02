# Production Risk Register

Дата: 2026-09-02. Статусы: `RESOLVED`, `PARTIAL`, `OPEN`, `NOT VERIFIED`. Сокращённые
`core/...`, `web/...` и `cli/...` пути в таблице имеют общий префикс
`src/yt_uniquifier/`.

`RESOLVED` означает наличие regression test и локально пройденного соответствующего
gate. Это не переносит результат автоматически на непроверенные OS/GPU/HDR cases.

| Priority | Component | Problem | Evidence | Impact | Recommendation |
|---|---|---|---|---|---|
| P0 | Audio pitch | **RESOLVED:** 44.1 kHz source обрабатывался как 48 kHz | `core/pipeline.py::_audio_transform_params`; real FFmpeg 44.1 kHz test | Massive A/V desync, потеря хвоста | Source-aware `asetrate`; final 48 kHz; duration/LUFS regression |
| P0 | Segment timestamps | **RESOLVED:** copied negative AAC PTS сдвигал encoded video | `core/pipeline.py::build_segment_command_fused`; 752/752 decoded frames | Потеря начала/хвоста, A/V offset | Video-only segment, `setpts=PTS-STARTPTS`, final contract |
| P0 | Main audio mapping | **RESOLVED:** профиль без audio transforms терял track 0 | `core/segmenter.py::concat_segments`; real mux regression | Output без audio | Explicit source main-audio mapping |
| P0 | Chapters | **RESOLVED:** source chapters не передавались в concat | `core/orchestrator.py::_run_full_body`; 2→2 chapter test | Потеря навигации/метаданных | Source chapter mapping + title metadata restoration |
| P0 | Subtitle mux | **RESOLVED:** subtitle codec игнорировал target container | `core/preflight.py::_check_subtitles`; `core/segmenter.py::concat_segments` | Late mux failure | MP4/MOV text→mov_text; image subtitle early reject; MKV copy |
| P0 | Windowed audio | **RESOLVED:** physical overlap был вдвое больше crossfade | `core/pipeline.py::build_main_audio_command_windowed`; real 125 s test | Accumulated shift, tail cut | Half-overlap per side; one-AAC-frame gate |
| P0 | SSCD calibration | **RESOLVED:** objective был инвертирован | `core/calibration/loop.py::_evaluate_sscd`; direction tests | Идентичный output считался успехом | Direct clamped similarity |
| P0 | Resume identity | **RESOLVED:** plan hash не удостоверял content/topology | `core/pipeline.py::compute_plan_hash`; replacement/topology tests | Foreign stale artifacts | Content fingerprint + complete topology digest |
| P1 | Loudnorm | **RESOLVED:** pass 1 измерял source до preceding transforms | `core/pipeline.py::_measure_before_loudnorm`; `audio_loudnorm.py::measure` | Silent dynamic fallback | Measure actual pre-loudnorm graph; one resolved jitter target; real -14 LUFS test |
| P1 | Audio sample rate | **RESOLVED:** dynamic loudnorm output SR был implicit | `core/pipeline.py`; real output 48 kHz | Неожиданный format/size | Explicit final 48 kHz |
| P1 | Profile contract | **RESOLVED:** `target_loudness_lufs` игнорировался | `core/pipeline.py::_loudnorm_params_from`; command regression | Неверная loudness | Top-level target unless transform explicitly overrides |
| P1 | Final cache | **RESOLVED:** no-op проверял только non-empty output | `core/checkpoint.py::output_is_valid`; orchestrator | Corrupt/wrong output accepted | Exact output path/hash plus final media contract |
| P1 | Cached main audio | **RESOLVED:** cached audio не имел integrity check | `core/checkpoint.py`; `segmenter.py::process_main_audio` | Corrupt audio reused | SHA256 + atomic temp replace |
| P1 | Speed/duration | **PARTIAL:** mismatched/unsafe timeline combinations теперь rejected | `core/preflight.py::_check_timeline_rate`; `pipeline.py::expected_output_duration` | Tail loss/desync | Matching main A/V rate supported; aux streams require future explicit retiming |
| P1 | SAR/DAR | **RESOLVED:** crop/resize оставлял неверный SAR | `core/transforms/video_geom.py`; real SAR 1:1 test | Искажённое отображение | `setsar=1` after scale |
| P1 | Stream topology | First audio hardcoded; extra codecs copied независимо от container | `core/pipeline.py:717`, `core/segmenter.py:785-791` | Неверный language/default track; DTS/TrueHD/Opus mux failures | Select default/requested track, per-stream mux policy, preserve dispositions/language/title |
| P1 | Profile audio_tracks | **RESOLVED:** extras игнорировали profile selection | `core/stream_policy.py`; orchestrator/concat tests | Contract mismatch/privacy/size surprise | Unified first/all/absolute-index selection |
| P1 | Metadata | Chapters, subtitle dispositions/title, attachments/data stripped | `core/metadata.py`; segment maps `-map_metadata -1` | Lossy remux | Explicit preservation policy and manifest diff gate |
| P1 | HDR metadata | Probe/encode omits mastering display, MaxCLL/FALL, HDR10+/DV | `core/probe.py`, `core/pipeline.py` | Wrong HDR detection/rendering on YouTube | Extend SourceMeta, preserve/verify side data, reject unsupported dynamic HDR rather than silently flatten |
| P1 | Encoder capability | Probe only 640×360 yuv420p 8-bit | `core/encoder.py:254-298` | Selected HW encoder may fail after long preprocessing or lose HDR | Capability probe keyed by codec/pixfmt/resolution/RC/device; short real job smoke |
| P1 | Runner hang | Merged stdout iteration has no wall/stall timeout | `core/runner.py:289-466` | Indefinite production job | Streaming heartbeat/stall watchdog + configurable wall policy + process group termination |
| P1 | Checkpoint lock | **RESOLVED locally:** check-then-replace was not exclusive | `core/checkpoint.py::CheckpointStore`; concurrent lock tests | Concurrent writers | Atomic `O_CREAT|O_EXCL`; conservative cross-host stale policy |
| P1 | Lock lifecycle | **RESOLVED:** `run_full` did not close store | `core/orchestrator.py::_run_full_impl` | Daemon blocks another process | Unconditional `finally: store.close()` |
| P1 | Parallel failure | **RESOLVED:** siblings cancelled only with caller token | `core/segmenter.py::process_video_segments_parallel` | Slow failure | Always-present internal cancellation token |
| P1 | Web scheduling | **PARTIAL:** process-local active-run cap added | `web/app.py::WebConfig`; `web/routes/run.py` | Resource exhaustion | Persistent/global per-encoder scheduler and quotas remain open |
| P1 | Web output race | **RESOLVED process-locally:** duplicate active output rejected | `web/routes/run.py::start_run`; route tests | Last writer wins | Atomic in-memory reservation; distributed reservation still open |
| P1 | Web lifecycle | **PARTIAL:** queue sentinel is nonblocking; persistence/pruning remain open | `web/routes/run.py` | Restart loss/memory growth | Persistent bounded store + TTL remains roadmap |
| P1 | Distributed liveness | Heartbeat per hostname, not worker instance | `core/queue/leasing.py::heartbeat/reap_stale` | Live sibling masks dead worker job | Worker UUID lease + per-job heartbeat/fencing token |
| P1 | Distributed resume | Lease path changes plan hash; target VMAF and QA disabled | `cli/cmd_worker.py:63-76,153-154` | Recovery repeats work; weaker correctness | Stable content/job ID work dir; same quality/QA policy as local pipeline |
| P2 | QA verdict | **PARTIAL:** topology correctness notes force RED; SSCD/loudness gates remain | `core/qa/report.py::build_report`, `QAReport.verdict` | False GREEN | Mandatory orchestrator media contract plus QA correctness notes |
| P2 | Duration QA | Container duration only, ±0.5 s | `core/qa/report.py:187` | Скрывает PTS shift/frame loss/tail cut | Compare per-stream start/end, decoded frame/audio samples, A/V delta |
| P2 | Metric fallback | VMAF/SSIM/pHash treated as common 0..100 | `core/qa/quality.py` | Calibration optimizes incomparable values | Metric-specific constraints; never substitute silently |
| P2 | VMAF registration | Crop/PTS not aligned before quality metric | `core/qa/vmaf.py::compute` | False low score or false diagnosis | Produce encode-only reference or register geometry/time; report raw and aligned metrics |
| P2 | Audio fingerprint | Only first 600 s, set Jaccard loses order | `core/qa/audio_fp.py:53-56,102-106` | Invalid long-form heatmap/similarity | Stratified full-duration windows, cached ordered comparison/alignment |
| P2 | SSCD extraction | One FFmpeg process per frame | `core/qa/sscd.py:173-216` | 64 processes/default pair, slow/cancel-unfriendly | Single fps/select extraction per file, batched inference, temporal alignment |
| P2 | Calibration search | **PARTIAL:** fixed common seed + bracket added; stratified/Pareto search remains | `core/calibration/loop.py::calibrate` | Non-monotone objective | Reproducible trials and bracketing; corpus optimizer remains roadmap |
| P2 | Calibration clip cache | **RESOLVED:** cache was existence-only | `core/calibration/loop.py::_cut_test_clip` | Wrong source evaluated | Content-keyed head/tail digest |
| P2 | Calibration failures | **RESOLVED:** encode error increased factor | `core/calibration/loop.py::calibrate` | Infra failure became signal | Retry same factor, then abort |
| P2 | Aggressive profile | Noise -12 dB, temporal/audio stack highly destructive | `profiles/cid_aggressive.yaml`, transform defaults | Audible/visual damage after YouTube transcode | Do not ship as recommended; add quality gates/warnings or experimental label |
| P2 | Geometric strength | **RESOLVED:** per-side crop doubled advertised maximum | `core/transforms/video_geom.py::_build_crop`; bounds test | Up to 18% axis loss | `max_strength` now total per-axis crop; documented migration |
| P2 | Aspect profiles | Forced resize may upscale source | `core/transforms/video_fit_aspect.py`, YouTube profiles | No detail gain, encode/storage cost, artifacts | `allow_upscale=false` default; preserve source aspect unless user requests format |
| P2 | Temporal cadence | 1440 frames treated as 60 seconds | `core/transforms/video_temporal_jitter.py` | 24/30/60/VFR inconsistent behavior | Base intervals on PTS seconds, not frame number |
| P2 | Channel awareness | **PARTIAL:** Haas now rejects non-stereo; other effects need corpus matrix | `core/preflight.py::_check_audio_channel_layout` | Mono/5.1 damage | Stereo guard added; wider per-layout policy remains |
| P2 | YouTube settings | **PARTIAL:** layout-aware audio bitrate/48 kHz implemented; GOP policy open | `core/pipeline.py::_main_audio_bitrate` | Inconsistent ingestion | Mono 128/stereo 384/surround 512 kbps; encoder policy remains |
| P3 | Scene segmentation | Scene boundaries ignore target segment upper bound | `core/segmenter.py:294-340` | One huge static segment or excessive tiny segments | Coalesce/split after scene detection with min/target/max constraints |
| P3 | Encoder selection | Hardware-first default conflicts with quality-first objective | `core/encoder.py` ordering | Lower quality/predictability for same nominal settings | Policy `quality/balanced/speed`, default quality for production |
| P3 | AV1 Vulkan args | Candidate exists, no vendor-specific pipeline args | `core/encoder.py`, `core/pipeline.py::_encoder_args_for` | Runtime invalid command | Capability-specific arg builder or disable until verified |
| P3 | Encoder cache | Key excludes GPU/driver, TTL 7 days | `core/encoder.py:27,112` | Stale availability after driver/device change | Include devices/driver signature; invalidate on execution failure |
| P3 | Concat/sanitizer timeout | Fixed 3600 seconds | `core/segmenter.py:812-827`; `core/sanitizer.py:36-40` | Valid 3h+ job killed | Configurable, progress/stall-based timeout |
| P3 | Sanitizer format | Temp always `.sanitized.mp4`, AV1 not rejected | `core/sanitizer.py:54-98` | Container mismatch/codec change | Preserve target suffix; codec capability policy; preferably remove from quality-first default path |
| P3 | Benchmark memory | Measures `RUSAGE_SELF`, excludes FFmpeg child RSS | `tools/benchmark.py:114` | Misleading RAM metric | Process-tree RSS/CPU/GPU sampling and cold/warm controls |
| P3 | Seam tool | Compares output to itself shifted by 1 s/frame | `tools/seam_test.py:24-32` | Motion mislabeled seam artifact | Compare decoded source/output around known boundaries with temporal registration |
| P4 | Dual pipelines | Legacy full-file and segmented args differ | `core/pipeline.py` command builders | Behavior/test drift | Share mapping/encoder/tail policy helpers; no second pipeline |
| P4 | Web version | **RESOLVED:** hardcoded 0.9.0 | `web/app.py::create_app` | Incorrect diagnostics | Uses package `__version__` |
| P4 | Profile duplication | Platform YAML copies same values | `src/yt_uniquifier/profiles/*.yaml` | Magic-number drift | Generated inheritance/composition or validated shared fragments |
| P5 | Documentation | README chapters/subtitles and architecture claims stale | `README.md:35`, `CLAUDE.md:65` | Operator trusts behavior that fails | Update only after fixes; add verified capability matrix |

## Release blocker policy

Все P0 и P1 correctness items должны быть закрыты тестом, который сначала
воспроизводит defect на текущем коде. P2 quality items могут выпускаться позже только
при явной маркировке limitations. Любой `NOT VERIFIED` HDR/long-form/HW сценарий
остаётся release blocker для соответствующей заявленной capability, но не для
CLI-only SDR/x264 subset.
