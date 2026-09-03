"""SSCD (Self-Supervised Copy Detection) similarity metric.

v0.8.0 R4 — opt-in ML-grade QA metric. SSCD is the embedding model
released alongside Meta's VSC2022 dataset; it was used to deduplicate
the LLaMA training corpus and is the state-of-the-art for "is this
video a derivative of that one?". Marketing aside, the practical win
over our chromaprint + pHash baseline is robustness to crops, color
shifts, and frame-rate retiming. Here it is only an internal regression
and self-collision diagnostic for authorized derivatives; it does not
predict or validate a third-party rights-detection system.

The supported backend is the official TorchScript checkpoint exposed
by Meta's upstream project.  The upstream project does not publish an
ONNX checkpoint; callers that need another backend can inject a custom
``model_loader`` explicitly.

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
_MODEL_SHA256 = "9f26bd4c848cc19b73d2ae92eea6e04886f61a7b764ceb7a13aeee62e6a6db56"
_MODEL_INPUT_SIZE = 288
_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]
_INSTALL_HINT = (
    "SSCD requires the `[ml]` extra (TorchScript backend; pulls torch "
    "and torchvision). Install via `pip install yt-uniquifier[ml]`."
)

_models_dir_default = Path.home() / ".cache" / "yt_uniquifier" / "models"


@dataclass(frozen=True)
class SSCDResult:
    """One source/output similarity report.

    mean_similarity = arithmetic mean of cosines over paired frames.
    min_similarity = least-similar paired frame (useful for spotting an outlier).
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
    """Load the official SSCD TorchScript model from the verified cache."""
    try:
        import torch
    except ImportError as exc:
        raise PipelineError(_INSTALL_HINT) from exc

    cache_path = _ensure_model_cached()
    # eval-mode + grad-disabled inference is enough — these weights are
    # frozen and we never call .backward().
    # PyTorch 2.2's Intel-macOS stubs leave ``jit.load`` untyped even
    # though the returned ScriptModule has the runtime API used below.
    model = torch.jit.load(  # type: ignore[no-untyped-call]
        str(cache_path), map_location="cpu"
    )
    model.eval()
    return model


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
        actual_sha = _sha256_file(cache_path)
        if actual_sha == _MODEL_SHA256:
            return cache_path
        cache_path.unlink(missing_ok=True)
        raise PipelineError(
            f"cached SSCD model SHA-256 mismatch (got {actual_sha}, "
            f"expected {_MODEL_SHA256}). Removed the unverified cache; retry "
            "to download the official checkpoint."
        )

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

    Midpoint seeks cover the complete timeline without decoding every
    preceding frame of a multi-hour source.  Frames are resized directly
    to the square input used by the official SSCD inference recipe.
    """
    if frame_count < 1:
        raise PipelineError("sscd frame_count must be ≥ 1")
    from yt_uniquifier.core.errors import ProbeError
    from yt_uniquifier.core.probe import probe

    try:
        duration_sec = probe(source).duration_sec
    except ProbeError as exc:
        raise PipelineError(f"sscd duration probe failed for {source}: {exc}") from exc
    if duration_sec <= 0:
        raise PipelineError(f"sscd source duration is unknown for {source}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(frame_count):
        timestamp = duration_sec * (idx + 0.5) / frame_count
        output = dest_dir / f"frame_{idx:05d}.png"
        cmd = [
            ffmpeg_bin(),
            "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{timestamp:.6f}",
            "-i", str(source),
            "-map", "0:v:0",
            "-frames:v", "1",
            "-vf", f"scale={_MODEL_INPUT_SIZE}:{_MODEL_INPUT_SIZE}",
            "-an", "-sn",
            str(output),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise PipelineError(
                f"sscd frame extraction failed for {source} at {timestamp:.3f}s: "
                f"{getattr(exc, 'stderr', exc)!r}"
            ) from exc
    frames = sorted(dest_dir.glob("frame_*.png"))
    if not frames:
        raise PipelineError(f"sscd frame extraction produced 0 frames for {source}")
    return frames


# ---------------------------------------------------------------------------
# Embedding + cosine
# ---------------------------------------------------------------------------


def _embed_frames(model: Any, frames: list[Path]) -> Any:
    """Return an (N, D) tensor of L2-normalised embeddings."""
    import torch
    from PIL import Image
    from torchvision.transforms import functional as TF

    tensors = []
    for fp in frames:
        with Image.open(fp) as img:
            t = TF.to_tensor(img.convert("RGB"))
            t = TF.normalize(t, mean=_IMAGENET_MEAN, std=_IMAGENET_STD)
        tensors.append(t)
    batch = torch.stack(tensors, dim=0)
    with torch.inference_mode():
        emb = model(batch)
    # SSCD checkpoint already returns L2-normalised vectors, but a
    # downstream model swap or a re-trained variant might not — make
    # cosine math self-consistent regardless.
    emb = torch.nn.functional.normalize(emb, p=2, dim=1)
    return emb


def _pairwise_cosine(a: Any, b: Any) -> list[float]:
    """Per-row cosine between two equal-shape (N, D) tensors/arrays.

    The numpy fallback is retained for callers that inject a custom
    non-Torch model through the public ``model_loader`` seam.
    """
    if hasattr(a, "numpy") and hasattr(b, "numpy"):
        import torch
        if a.shape != b.shape:
            raise PipelineError(
                f"sscd embedding shape mismatch: {tuple(a.shape)} vs {tuple(b.shape)}"
            )
        sims = torch.sum(a * b, dim=1)
        return [float(v) for v in sims.clamp(-1.0, 1.0).tolist()]
    # Numpy path for an explicitly injected custom backend.
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
    """Map mean SSCD representation similarity to legacy display buckets.

    Bucket names are retained for compatibility. They are descriptive local
    diagnostics, not calibrated quality gates or external-system predictions.
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
