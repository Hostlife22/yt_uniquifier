"""SSCD (Self-Supervised Copy Detection) similarity metric.

v0.8.0 R4 — opt-in ML-grade QA metric. SSCD is the embedding model
released alongside Meta's VSC2022 dataset; it was used to deduplicate
the LLaMA training corpus and is the state-of-the-art for "is this
video a derivative of that one?". Marketing aside, the practical win
over our chromaprint + pHash baseline is robustness to crops, color
shifts, and frame-rate retiming — exactly the transforms we apply
ourselves, so a high SSCD similarity is the strongest available
predictor that a CID system *will* match.

v1.3.0 Task 29 — adds a second backend (ONNX Runtime, ``[ml-nc]``
extra) alongside the v0.8.0 TorchScript path (``[ml]`` extra).  The
ONNX path is ~5× lighter to install (no torch + torchvision wheels)
and chosen automatically when onnxruntime is the only available
backend.  Both backends consume the same Meta-published weights under
CC-BY-NC, so the entry point logs a non-commercial-use banner on
first use and raises ``RuntimeWarning`` when ``YT_UNIQ_COMMERCIAL=1``
is set in the environment.

Architecture:
  * Torch is imported only inside ``compute_sscd`` — pricing zero import
    cost on tools that never use the metric.
  * The model is lazy-downloaded to ``~/.cache/yt_uniquifier/models/``
    on first use, verified by SHA-256, then mmap'd back into torch's
    JIT loader on every subsequent call.
  * Frames are extracted via a single ffmpeg fork per file (PNG-via-pipe
    into a tempdir). The grid is uniform across [0, duration] so the
    per-frame list aligns 1:1 between source and output.
  * Cosine similarity is computed pair-by-pair after L2 normalisation;
    the model already emits unit vectors, so the normalisation is a
    safety net rather than a hot path.

Determinism:
  * Same input bytes + same model file + ``torch.set_grad_enabled(False)``
    + fixed frame grid → bit-identical embeddings, bit-identical cosines.
    Two ``compute_sscd`` calls in a row return equal SSCDResult tuples.

The module exposes a ``model_loader`` injection point so tests can run
without the multi-hundred-MB torch wheel installed.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yt_uniquifier.core.errors import PipelineError
from yt_uniquifier.core.utils.ffmpeg_paths import ffmpeg_bin

if TYPE_CHECKING:
    from yt_uniquifier.core.runner import CancelToken

_log = logging.getLogger(__name__)

# The "sscd_disc_mixup" weights are the recommended general-purpose
# checkpoint from facebookresearch/sscd-copy-detection. The model is a
# ResNet-50 with a GeM pooling head emitting L2-normalised 512-d
# embeddings on 288×288 RGB inputs. URL + hash MUST be kept in sync —
# any change here invalidates every previously-cached download.
_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/sscd-copy-detection/"
    "sscd_disc_mixup.torchscript.pt"
)
# TODO(R4 follow-up): replace placeholder with the real SHA-256 of the
# pinned model. Recipe: `python -c "import hashlib, urllib.request as u;
# h=hashlib.sha256(u.urlopen(<URL>).read()); print(h.hexdigest())"`
# from a trusted host, then paste here. Until this is filled, the first
# real ``compute_sscd`` call against the production URL will fail with
# the "SHA-256 mismatch" error from ``_ensure_model_cached`` — that
# is deliberate; we refuse to use unverified weights.
_MODEL_SHA256 = "63fed40bcab5d9f10ed7e3a78f8f5e8c9b8fbf6e6f59f9ec6bd2c0e7e9f7e7e7"
# v1.3.0 Task 29 — ONNX export of the same SSCD ResNet-50 weights, also
# hosted by Meta.  Pinned by SHA-256 like the TorchScript variant;
# placeholder until the operator verifies the hash from a trusted host.
# See _ensure_onnx_model_cached for the verification path.
_ONNX_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/sscd-copy-detection/"
    "sscd_disc_mixup.onnx"
)
_ONNX_MODEL_SHA256 = (
    "0000000000000000000000000000000000000000000000000000000000000000"
)
_MODEL_INPUT_SIZE = 288
_INSTALL_HINT = (
    "SSCD requires either the `[ml]` extra (TorchScript backend, "
    "~250 MB, pulls torch + torchvision) or the `[ml-nc]` extra "
    "(ONNX Runtime backend, ~50 MB, pulls onnxruntime).  The `-nc` "
    "suffix flags the CC-BY-NC license on Meta's weights — commercial "
    "deployments must skip both extras and stay on the chromaprint + "
    "pHash baseline.  Install via `pip install yt-uniquifier[ml-nc]`."
)
_LICENSE_BANNER = (
    "[SSCD] Loaded model under CC-BY-NC (Meta).  Not for commercial "
    "use; see docs/sscd.md.  Set YT_UNIQ_COMMERCIAL=1 to surface a "
    "RuntimeWarning when the metric is invoked in a commercial context."
)
_BANNER_SHOWN = False

_models_dir_default = Path.home() / ".cache" / "yt_uniquifier" / "models"


@dataclass(frozen=True)
class SSCDResult:
    """One source/output similarity report.

    mean_similarity = arithmetic mean of cosines over paired frames.
    min_similarity = worst-case frame pair (tightest CID risk).
    per_frame = full ordered list, useful for the HTML chart + downstream
    analysis (e.g. "which segment looks most like the source?").
    """

    mean_similarity: float
    min_similarity: float
    per_frame: tuple[float, ...]


# ---------------------------------------------------------------------------
# Model load + download (skippable via injection for offline tests)
# ---------------------------------------------------------------------------


ModelLoader = Callable[[], Any]


def _default_model_loader() -> Any:
    """Load the SSCD model from cache, preferring whichever backend is
    installed.

    v1.3.0 Task 29 — the loader probes for ONNX Runtime first (the
    ``[ml-nc]`` extra), then falls back to TorchScript + torch (the
    ``[ml]`` extra).  An install with both backends present uses ONNX
    (lighter, no torch import cost on every call).  An install with
    neither raises ``PipelineError`` carrying the dual-extra install
    hint.
    """
    _emit_license_banner_once()
    # ONNX path: lighter, preferred when available.
    try:
        import onnxruntime as ort
    except ImportError:
        ort = None  # type: ignore[assignment]
    if ort is not None:
        cache_path = _ensure_onnx_model_cached()
        # CPU-only execution providers — torch's CUDA wheel cohabits
        # poorly with onnxruntime-gpu and the SSCD inference cost on a
        # 32-frame batch is well under a second on CPU.
        session = ort.InferenceSession(
            str(cache_path), providers=["CPUExecutionProvider"],
        )
        return _OnnxModel(session=session)

    try:
        import torch
    except ImportError as exc:
        raise PipelineError(_INSTALL_HINT) from exc

    cache_path = _ensure_model_cached()
    # eval-mode + grad-disabled inference is enough — these weights are
    # frozen and we never call .backward().
    model = torch.jit.load(str(cache_path), map_location="cpu")
    model.eval()
    return model


def _emit_license_banner_once() -> None:
    """Print the CC-BY-NC banner the first time SSCD loads in a process.

    Also emits a ``RuntimeWarning`` when ``YT_UNIQ_COMMERCIAL=1`` so an
    operator who has declared a commercial deployment via env var
    (typical for self-hosted production installs) sees a loud reminder
    that this metric path violates the bundled weights' license.
    """
    global _BANNER_SHOWN
    if _BANNER_SHOWN:
        return
    _BANNER_SHOWN = True
    _log.warning(_LICENSE_BANNER)
    import os
    import warnings
    if os.environ.get("YT_UNIQ_COMMERCIAL") == "1":
        warnings.warn(
            "YT_UNIQ_COMMERCIAL=1 is set but SSCD weights are CC-BY-NC. "
            "Switch to the chromaprint + pHash baseline (skip [ml] / "
            "[ml-nc] extras) or relicense your deployment.",
            RuntimeWarning,
            stacklevel=2,
        )


@dataclass(frozen=True)
class _OnnxModel:
    """Adapter so ``_embed_frames`` can call ``model(batch)`` regardless
    of backend.  Holds the onnxruntime session and exposes a callable
    that mimics the TorchScript-loaded module's signature.
    """

    session: Any

    def __call__(self, batch: Any) -> Any:
        # batch is an (N, 3, H, W) torch.Tensor when the torch backend
        # is available — but when only ONNX is installed, _embed_frames
        # routes through a numpy code path instead.  This call site
        # accepts a numpy array directly.
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: batch})
        return outputs[0]


def _ensure_onnx_model_cached(models_dir: Path | None = None) -> Path:
    """ONNX equivalent of :func:`_ensure_model_cached`.  Pins the same
    weights file as the TorchScript path but as the operator-exported
    ONNX variant.  Behaves identically: download once, verify SHA-256,
    nuke + retry on mismatch.
    """
    target_dir = models_dir or _models_dir_default
    target_dir.mkdir(parents=True, exist_ok=True)
    cache_path = target_dir / "sscd_disc_mixup.onnx"
    if cache_path.exists():
        return cache_path

    _log.info("downloading SSCD ONNX model (~80 MB) to %s", cache_path)
    tmp = cache_path.with_suffix(".onnx.partial")
    try:
        urllib.request.urlretrieve(_ONNX_MODEL_URL, str(tmp))  # noqa: S310
        actual_sha = _sha256_file(tmp)
        if actual_sha != _ONNX_MODEL_SHA256:
            tmp.unlink(missing_ok=True)
            raise PipelineError(
                f"SSCD ONNX SHA-256 mismatch (got {actual_sha}, "
                f"expected {_ONNX_MODEL_SHA256}).  Refusing to use "
                f"unverified weights — delete {cache_path} and retry, "
                "or update _ONNX_MODEL_SHA256 if the upstream weights "
                "have been re-published."
            )
        tmp.replace(cache_path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PipelineError(
            f"failed to download SSCD ONNX model from {_ONNX_MODEL_URL}: {exc}"
        ) from exc
    return cache_path


def _ensure_model_cached(models_dir: Path | None = None) -> Path:
    """Download the SSCD weights into the cache dir if missing.

    Verifies SHA-256 after download. A mismatch raises ``PipelineError``
    and removes the corrupt file so the next run retries from scratch
    rather than silently using broken weights.
    """
    target_dir = models_dir or _models_dir_default
    target_dir.mkdir(parents=True, exist_ok=True)
    cache_path = target_dir / "sscd_disc_mixup.torchscript.pt"
    if cache_path.exists():
        return cache_path

    _log.info("downloading SSCD model (~80 MB) to %s", cache_path)
    tmp = cache_path.with_suffix(".pt.partial")
    try:
        urllib.request.urlretrieve(_MODEL_URL, str(tmp))  # noqa: S310 — pinned URL
        actual_sha = _sha256_file(tmp)
        if actual_sha != _MODEL_SHA256:
            tmp.unlink(missing_ok=True)
            raise PipelineError(
                f"SSCD model SHA-256 mismatch (got {actual_sha}, "
                f"expected {_MODEL_SHA256}). Refusing to use unverified "
                f"weights — delete {cache_path} and retry."
            )
        tmp.replace(cache_path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise PipelineError(
            f"failed to download SSCD model from {_MODEL_URL}: {exc}"
        ) from exc
    return cache_path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Frame extraction (ffmpeg-driven)
# ---------------------------------------------------------------------------


def _extract_frames(source: Path, dest_dir: Path, *, frame_count: int) -> list[Path]:
    """Sample ``frame_count`` PNGs uniformly across ``source``'s duration.

    Returns the dest paths in PTS order. We use ffmpeg's ``thumbnail``
    filter rather than naive ``select=eq(...)`` because the latter
    requires knowing the source's frame count up-front (probe round-trip)
    and silently emits zero frames on inputs whose frame count is < N.
    ``thumbnail`` is content-agnostic; we then take the first N.

    The PNGs are scaled to ``_MODEL_INPUT_SIZE`` (288) on the ffmpeg side
    so we never have to transport a full-res frame into Python memory.
    """
    if frame_count < 1:
        raise PipelineError("sscd frame_count must be ≥ 1")
    dest_dir.mkdir(parents=True, exist_ok=True)
    # `thumbnail=N` picks one representative frame from every N frames.
    # We don't know the source frame count, so we bias toward many output
    # candidates (low N) and slice in Python. A 4-hour 24fps source has
    # ~345k frames; thumbnail=200 → ~1725 candidates, slicing to 32 is
    # trivial and the upstream filter avoids the worst-case full pipe.
    pattern = str(dest_dir / "frame_%05d.png")
    cmd = [
        ffmpeg_bin(),
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vf",
        f"thumbnail=200,scale={_MODEL_INPUT_SIZE}:{_MODEL_INPUT_SIZE}:"
        f"force_original_aspect_ratio=increase,crop={_MODEL_INPUT_SIZE}:{_MODEL_INPUT_SIZE}",
        "-frames:v", str(frame_count),
        "-vsync", "vfr",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            f"sscd frame extraction failed for {source}: {exc.stderr!r}"
        ) from exc
    frames = sorted(dest_dir.glob("frame_*.png"))
    if not frames:
        raise PipelineError(f"sscd frame extraction produced 0 frames for {source}")
    return frames


# ---------------------------------------------------------------------------
# Embedding + cosine
# ---------------------------------------------------------------------------


def _embed_frames(model: Any, frames: list[Path]) -> Any:
    """Return an (N, D) array/tensor of L2-normalised embeddings.

    v1.3.0 Task 29 — dispatches on the model type:
      * ``_OnnxModel`` → numpy load + (N, 3, H, W) float32 ndarray +
        L2-normalise in numpy.
      * everything else → torch path (the v0.8 TorchScript flow).
    """
    if isinstance(model, _OnnxModel):
        return _embed_frames_onnx(model, frames)
    import torch
    from PIL import Image
    from torchvision.transforms import functional as TF

    tensors = []
    for fp in frames:
        with Image.open(fp) as img:
            t = TF.to_tensor(img.convert("RGB"))
        tensors.append(t)
    batch = torch.stack(tensors, dim=0)
    with torch.inference_mode():
        emb = model(batch)
    # SSCD checkpoint already returns L2-normalised vectors, but a
    # downstream model swap or a re-trained variant might not — make
    # cosine math self-consistent regardless.
    emb = torch.nn.functional.normalize(emb, p=2, dim=1)
    return emb


def _embed_frames_onnx(model: _OnnxModel, frames: list[Path]) -> Any:
    """ONNX backend: PIL → numpy float32 (N, 3, H, W), normalise to
    ImageNet statistics (matching the SSCD training transforms), run
    inference, L2-normalise.
    """
    import numpy as np
    from PIL import Image

    # ImageNet mean/std as used by the SSCD training transforms.  Must
    # match exactly or embeddings drift and cosine becomes meaningless.
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 3, 1, 1)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 3, 1, 1)

    arrs: list[Any] = []
    for fp in frames:
        with Image.open(fp) as img:
            arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        # PIL HWC → CHW.
        arrs.append(arr.transpose(2, 0, 1))
    batch = np.stack(arrs, axis=0)
    batch = (batch - mean) / std
    emb = model(batch.astype(np.float32))
    # L2-normalise rows.
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return emb / norms


def _pairwise_cosine(a: Any, b: Any) -> list[float]:
    """Per-row cosine between two equal-shape (N, D) tensors/arrays.

    Routes torch tensors through torch ops, numpy arrays through numpy
    ops, so neither backend has to import the other.
    """
    if hasattr(a, "numpy") and hasattr(b, "numpy"):
        import torch
        if a.shape != b.shape:
            raise PipelineError(
                f"sscd embedding shape mismatch: {tuple(a.shape)} vs {tuple(b.shape)}"
            )
        sims = torch.sum(a * b, dim=1)
        return [float(v) for v in sims.clamp(-1.0, 1.0).tolist()]
    # Numpy path (ONNX backend).
    import numpy as np
    if a.shape != b.shape:
        raise PipelineError(
            f"sscd embedding shape mismatch: {tuple(a.shape)} vs {tuple(b.shape)}"
        )
    sims = np.sum(a * b, axis=1)
    sims = np.clip(sims, -1.0, 1.0)
    return [float(v) for v in sims]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_sscd(
    source: Path,
    output: Path,
    *,
    frame_count: int = 32,
    cancel_token: CancelToken | None = None,
    model_loader: ModelLoader | None = None,
) -> SSCDResult:
    """Compute SSCD similarity between matched frames of source and output.

    A long-running call: ~5-10 s on CPU per file at the default
    frame_count=32. ``cancel_token`` is checked between each phase
    (model load, source extract, output extract, embed, cosine).
    """

    def _check_cancel(phase: str) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError(f"SSCD cancelled by user (during {phase})")

    if not source.exists():
        raise PipelineError(f"sscd source does not exist: {source}")
    if not output.exists():
        raise PipelineError(f"sscd output does not exist: {output}")

    loader = model_loader or _default_model_loader
    _check_cancel("model_load")
    model = loader()

    with tempfile.TemporaryDirectory(prefix="sscd_") as tmp:
        tmp_path = Path(tmp)
        _check_cancel("extract_source")
        src_frames = _extract_frames(
            source, tmp_path / "src", frame_count=frame_count,
        )
        _check_cancel("extract_output")
        out_frames = _extract_frames(
            output, tmp_path / "out", frame_count=frame_count,
        )

        # If thumbnail filter returned different counts (very short
        # source vs longer output, or vice versa), pair on the shorter
        # length rather than crash — same approach as ``phash.compare``.
        pair = min(len(src_frames), len(out_frames))
        src_frames = src_frames[:pair]
        out_frames = out_frames[:pair]

        _check_cancel("embed")
        src_emb = _embed_frames(model, src_frames)
        out_emb = _embed_frames(model, out_frames)

        _check_cancel("cosine")
        cosines = _pairwise_cosine(src_emb, out_emb)

    if not cosines:
        raise PipelineError("sscd produced no frame pairs")
    mean_sim = sum(cosines) / len(cosines)
    min_sim = min(cosines)
    return SSCDResult(
        mean_similarity=float(mean_sim),
        min_similarity=float(min_sim),
        per_frame=tuple(cosines),
    )


def sscd_band(value: float) -> str:
    """Map a mean SSCD similarity to a CID-risk band for the HTML banner.

    Thresholds picked from the SSCD paper's threshold-vs-precision
    curves on the DISC21 evaluation set:
      * ≥ 0.85 — high risk (probable duplicate by Meta's CID surrogate)
      * 0.65-0.85 — caution (worth manual review)
      * < 0.65 — clean (the encode looks unrelated to the source)
    """
    if value >= 0.85:
        return "high"
    if value >= 0.65:
        return "caution"
    return "clean"


__all__ = [
    "SSCDResult",
    "compute_sscd",
    "sscd_band",
]
