# Hardware encoder qualification

Encoder discovery proves that FFmpeg can open a device for one exact job. It does
not prove that the resulting stream has the required profile, colour signalling,
frame cadence or random-access structure. A backend is production-qualified only
after the emitted file passes the real bitstream contract on the target device and
driver.

The manual `Hardware qualification` GitHub Actions workflow runs that contract on a
trusted self-hosted runner. It is intentionally absent from push and pull-request
triggers, so the normal CI matrix neither waits for scarce GPUs nor executes
untrusted changes on a persistent machine.

## What is checked

The workflow builds a six-second 640×360, 24 FPS BT.709 limited-range source through
the real `build_plan` and `run_full` path. An explicitly requested encoder is
required to exist and complete; it cannot turn into a skip. The output checks cover:

- exact codec, level signalling and MP4 sample-entry tag;
- Main/High profile and 8-bit 4:2:0 pixel format;
- preserved frame count, cadence and BT.709/range signalling;
- preserved 30/20/60 FPS VFR timestamp pattern and exact decoded frame count;
- HEVC Main10 HLG/BT.2020/limited-range signalling where the encoder advertises HEVC;
- fail-closed rejection of static HDR10 metadata on hardware paths that have not been
  explicitly verified to retain ST2086/MaxCLL/MaxFALL;
- two concurrent jobs when the encoder declares at least two sessions;
- H.264 High/CABAC and closed half-FPS IDR GOP, with one-to-two consecutive
  B-frames for exact-count backends or the qualified one-to-three VideoToolbox range;
- HEVC closed IDR and a maximum two-second GOP;
- AV1 keyframe intervals of at most two seconds.

`qualification-artifacts` contains JUnit XML, every generated media file and a JSON
report with hashes, FFprobe frame/stream data, FFmpeg build information and available
GPU inventory. Artifacts are retained for 30 days. A failed assertion is useful
qualification evidence: fix and qualify that vendor policy instead of weakening the
contract or marking the backend supported from encoder discovery alone.

## Runner preparation

Use a dedicated machine or ephemeral VM with the production driver, the intended
FFmpeg build, `ffprobe`, Git and a supported Python. Register it as a self-hosted
runner with the default OS label plus one narrow device label, for example:

| Device | Suggested label | Typical encoder selection |
|---|---|---|
| NVIDIA Turing (for example T4) | `gpu-nvenc-turing` | `h264_nvenc,hevc_nvenc` |
| NVIDIA Ada | `gpu-nvenc-ada` | `h264_nvenc,hevc_nvenc,av1_nvenc` |
| Intel Arc / recent iGPU | `gpu-qsv` | supported subset of `h264_qsv,hevc_qsv,av1_qsv` |
| AMD GPU | `gpu-amf` | supported subset of `h264_amf,hevc_amf,av1_amf` |
| Apple Silicon | `gpu-videotoolbox` | supported subset of the VideoToolbox encoders |

Do not request a codec the physical generation cannot encode. For example, NVIDIA T4
has NVENC H.264/HEVC but no AV1 encoder. The workflow deliberately fails if a named
encoder is absent or its real capability probe fails.

!!! warning
    Never attach this workflow to `pull_request` and never expose a long-lived
    self-hosted runner to untrusted forks. Use a non-admin runner account, isolate the
    host/network, avoid repository secrets, and rebuild ephemeral runners after jobs
    where practical.

## Run and evaluate

From the Actions UI, select **Hardware qualification**, choose the committed branch,
OS label, device label and exact comma-separated encoder list. The equivalent CLI
dispatch is:

```bash
gh workflow run hardware-qualification.yml \
  -f runner_os=Linux \
  -f runner_label=gpu-nvenc-turing \
  -f encoders=h264_nvenc,hevc_nvenc \
  -f python_version=3.12
```

The job may queue indefinitely when no online runner has all three labels
(`self-hosted`, the selected OS and the device label). A green result qualifies only
the recorded OS, FFmpeg build, device generation and driver—not an entire vendor.
Retain the artifact with the release evidence and rerun after driver, FFmpeg or
encoder-policy changes.

Locally, the same strict selection can be used without Actions:

```bash
export YT_UNIQ_HARDWARE_ENCODERS=h264_videotoolbox,hevc_videotoolbox

pytest tests/integration/test_encoder_bitstream_matrix.py -vv \
  --basetemp=.qualification/pytest-temp \
  --junitxml=.qualification/junit.xml

python tools/hardware_qualification_report.py \
  --media-root .qualification \
  --output .qualification/report.json
```

The `.qualification` directory is evidence, not a source-controlled fixture. Remove
it after archiving the report and media where your release records are stored.

On the qualified Intel Mac (Intel UHD 630 + Radeon Pro 5300M, FFmpeg 9.0.1), the
extended matrix completed with **14 passed / 36 unrequested skips** for
`h264_videotoolbox,hevc_videotoolbox`. The retained report contains 27 probed and
hashed media artifacts. This qualifies only that exact Mac/FFmpeg combination;
NVENC, QSV, AMF and Apple Silicon remain separate runner qualifications.
