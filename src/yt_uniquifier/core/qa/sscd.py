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
import os
import tempfile
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class SSCDRegistrationResult:
    """Bounded monotonic alignment of two sampled embedding timelines."""

    available: bool
    mean_similarity: float | None
    per_frame: tuple[float, ...]
    compared_frames: int
    coverage_ratio: float
    confidence: float
    mean_offset_frames: float | None
    max_displacement_frames: int | None
    note: str | None = None
    mean_offset_sec: float | None = None


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


def _extract_frames(
    source: Path,
    dest_dir: Path,
    *,
    frame_count: int,
    cancel_token: CancelToken | None = None,
) -> list[Path]:
    """Sample ``frame_count`` PNGs uniformly across ``source``'s duration.

    One FFmpeg process opens independently seekable inputs for all midpoint samples.
    This retains fast random access for multi-hour sources without the previous one
    process per frame overhead. Frames are resized directly to the square input used
    by the official SSCD inference recipe. The shared runner makes a silent extraction
    cancellable and applies the standard process-tree/stall-watchdog policy.
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

    from yt_uniquifier.core.pipeline import BuiltCommand
    from yt_uniquifier.core.runner import run as run_ffmpeg

    dest_dir.mkdir(parents=True, exist_ok=True)
    outputs = [dest_dir / f"frame_{idx:05d}.png" for idx in range(frame_count)]
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"]
    for idx in range(frame_count):
        timestamp = duration_sec * (idx + 0.5) / frame_count
        cmd.extend([
            # A single decoder thread per random-access input prevents a 32-sample
            # extraction from multiplying FFmpeg's automatic thread count.
            "-threads", "1",
            "-ss", f"{timestamp:.6f}",
            "-i", str(source),
        ])
    for idx, output in enumerate(outputs):
        cmd.extend([
            "-map", f"{idx}:v:0",
            "-frames:v", "1",
            "-vf", f"scale={_MODEL_INPUT_SIZE}:{_MODEL_INPUT_SIZE}",
            "-an", "-sn",
            str(output),
        ])
    try:
        run_ffmpeg(
            BuiltCommand(args=cmd),
            output=outputs[-1],
            cancel_token=cancel_token,
            progress_via_stdout=False,
        )
    except PipelineError as exc:
        raise PipelineError(f"sscd frame extraction failed for {source}: {exc}") from exc

    frames = sorted(dest_dir.glob("frame_*.png"))
    if len(frames) != frame_count:
        raise PipelineError(
            f"sscd frame extraction produced {len(frames)}/{frame_count} frames "
            f"for {source}"
        )
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


def _cosine_matrix(a: Any, b: Any) -> list[list[float]]:
    """Return a bounded in-memory cosine matrix for sampled embeddings."""
    import numpy as np

    left = a.detach().cpu().numpy() if hasattr(a, "detach") else np.asarray(a)
    right = b.detach().cpu().numpy() if hasattr(b, "detach") else np.asarray(b)
    if left.ndim != 2 or right.ndim != 2 or left.shape[1] != right.shape[1]:
        raise PipelineError(
            f"sscd embedding shape mismatch: {tuple(left.shape)} vs {tuple(right.shape)}"
        )
    matrix = np.clip(left @ right.T, -1.0, 1.0)
    return [[float(value) for value in row] for row in matrix.tolist()]


def align_cosine_matrix(
    similarities: list[list[float]],
    *,
    max_displacement_frames: int = 4,
    min_coverage: float = 0.60,
) -> SSCDRegistrationResult:
    """Needleman-Wunsch alignment inside a diagonal band.

    Traceback is monotonic and every row/column is consumed at most once, so
    output frames cannot be reused to manufacture a high score. Coverage below
    the floor is unavailable rather than being rewarded.
    """
    rows = len(similarities)
    columns = len(similarities[0]) if rows else 0
    if rows == 0 or columns == 0 or any(len(row) != columns for row in similarities):
        return SSCDRegistrationResult(
            False, None, (), 0, 0.0, 0.0, None, None,
            "empty or ragged SSCD similarity matrix",
        )
    if max_displacement_frames < 0:
        raise ValueError("max_displacement_frames must be non-negative")
    if not 0.0 < min_coverage <= 1.0:
        raise ValueError("min_coverage must be in (0, 1]")

    negative_infinity = float("-inf")
    gap_penalty = 0.25
    scores = [[negative_infinity] * (columns + 1) for _ in range(rows + 1)]
    moves = [[""] * (columns + 1) for _ in range(rows + 1)]
    scores[0][0] = 0.0
    for i in range(1, rows + 1):
        scores[i][0] = scores[i - 1][0] - gap_penalty
        moves[i][0] = "up"
    for j in range(1, columns + 1):
        scores[0][j] = scores[0][j - 1] - gap_penalty
        moves[0][j] = "left"

    row_span = max(rows - 1, 1)
    for i in range(1, rows + 1):
        expected = (i - 1) * (columns - 1) / row_span
        for j in range(1, columns + 1):
            candidates = [
                (scores[i - 1][j] - gap_penalty, "up"),
                (scores[i][j - 1] - gap_penalty, "left"),
            ]
            if abs((j - 1) - expected) <= max_displacement_frames:
                candidates.append((
                    scores[i - 1][j - 1] + similarities[i - 1][j - 1],
                    "diag",
                ))
            scores[i][j], moves[i][j] = max(
                candidates,
                key=lambda item: (item[0], item[1] == "diag"),
            )

    pairs: list[tuple[int, int, float]] = []
    i, j = rows, columns
    while i > 0 or j > 0:
        move = moves[i][j]
        if move == "diag":
            pairs.append((i - 1, j - 1, similarities[i - 1][j - 1]))
            i -= 1
            j -= 1
        elif move == "up":
            i -= 1
        elif move == "left":
            j -= 1
        else:
            break
    pairs.reverse()
    coverage = len(pairs) / max(rows, columns)
    if not pairs or coverage < min_coverage:
        return SSCDRegistrationResult(
            False, None, (), len(pairs), coverage, 0.0, None, None,
            f"SSCD alignment coverage {coverage:.3f} is below {min_coverage:.3f}",
        )
    values = tuple(value for _, _, value in pairs)
    offsets = [column - row * (columns - 1) / row_span for row, column, _ in pairs]
    mean_similarity = sum(values) / len(values)
    confidence = coverage * max(0.0, min(1.0, (mean_similarity - 0.2) / 0.8))
    return SSCDRegistrationResult(
        True,
        mean_similarity,
        values,
        len(pairs),
        coverage,
        confidence,
        sum(offsets) / len(offsets),
        max(round(abs(value)) for value in offsets),
    )


def _embedding_cache_dir() -> Path:
    configured = os.environ.get("YT_UNIQ_QA_CACHE_DIR")
    root = Path(configured).expanduser() if configured else (
        Path.home() / ".cache" / "yt_uniquifier" / "qa"
    )
    return root / "sscd_embeddings"


def _load_or_embed_reference(
    model: Any,
    frames: list[Path],
    *,
    cache_key: str | None,
) -> Any:
    """Load a validated reference embedding matrix or replace it atomically."""
    if cache_key is None:
        return _embed_frames(model, frames)
    import numpy as np

    digest = hashlib.sha256(
        f"{cache_key}:{_MODEL_SHA256}:{len(frames)}".encode()
    ).hexdigest()
    cache_dir = _embedding_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"{digest}.npy"
    if destination.exists():
        try:
            cached = np.load(destination, allow_pickle=False)
            if (
                cached.ndim == 2
                and cached.shape[0] == len(frames)
                and cached.shape[1] > 0
                and np.isfinite(cached).all()
            ):
                return cached
        except (OSError, ValueError):
            pass
        destination.unlink(missing_ok=True)

    embedded = _embed_frames(model, frames)
    array = embedded.detach().cpu().numpy() if hasattr(embedded, "detach") else np.asarray(embedded)
    temp = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp.open("xb") as handle:
            np.save(handle, array, allow_pickle=False)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return array


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
            source,
            tmp_path / "src",
            frame_count=frame_count,
            cancel_token=cancel_token,
        )
        _check_cancel("extract_output")
        out_frames = _extract_frames(
            output,
            tmp_path / "out",
            frame_count=frame_count,
            cancel_token=cancel_token,
        )

        # Custom or injected extractors may return different counts. Pair on
        # the shorter length rather than crash — same approach as ``phash.compare``.
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


def compute_sscd_registered(
    reference: Path,
    output: Path,
    *,
    frame_count: int = 32,
    max_displacement_frames: int = 4,
    max_offset_sec: float = 10.0,
    cancel_token: CancelToken | None = None,
    model_loader: ModelLoader | None = None,
    reference_cache_key: str | None = None,
) -> SSCDRegistrationResult:
    """Compare a transformed reference with bounded monotonic registration."""
    if frame_count < 2:
        raise PipelineError("registered SSCD frame_count must be at least two")
    if max_offset_sec < 0.0 or max_offset_sec > 30.0:
        raise PipelineError("registered SSCD max_offset_sec must be in [0, 30]")
    if not reference.exists() or not output.exists():
        raise PipelineError("registered SSCD input does not exist")

    def _check_cancel(phase: str) -> None:
        if cancel_token is not None and cancel_token.is_cancelled():
            raise PipelineError(f"SSCD registration cancelled by user (during {phase})")

    loader = model_loader or _default_model_loader
    _check_cancel("model_load")
    model = loader()
    from yt_uniquifier.core.probe import probe

    reference_duration = probe(reference).duration_sec
    output_duration = probe(output).duration_sec
    sample_interval = max(reference_duration, output_duration) / frame_count
    effective_displacement = min(
        max_displacement_frames,
        int(max_offset_sec / max(sample_interval, 0.001)),
    )
    output_frame_count = frame_count + 2 * effective_displacement
    with tempfile.TemporaryDirectory(prefix="sscd_registered_") as tmp:
        tmp_path = Path(tmp)
        _check_cancel("extract_reference")
        reference_frames = _extract_frames(
            reference,
            tmp_path / "reference",
            frame_count=frame_count,
            cancel_token=cancel_token,
        )
        _check_cancel("extract_output")
        output_frames = _extract_frames(
            output,
            tmp_path / "output",
            frame_count=output_frame_count,
            cancel_token=cancel_token,
        )
        _check_cancel("embed")
        reference_embeddings = _load_or_embed_reference(
            model,
            reference_frames,
            cache_key=reference_cache_key if model_loader is None else None,
        )
        output_embeddings = _embed_frames(model, output_frames)
        _check_cancel("align")
        matrix = _cosine_matrix(reference_embeddings, output_embeddings)
        result = align_cosine_matrix(
            matrix,
            max_displacement_frames=effective_displacement,
        )
        output_interval = output_duration / max(output_frame_count, 1)
        return replace(
            result,
            mean_offset_sec=(
                result.mean_offset_frames * output_interval
                if result.mean_offset_frames is not None
                else None
            ),
            note=(
                result.note
                or (
                    "SSCD offset is sampled-grid resolution; "
                    f"interval={output_interval:.3f}s, "
                    f"bound={effective_displacement} sample(s)"
                )
            ),
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
    "SSCDRegistrationResult",
    "SSCDResult",
    "align_cosine_matrix",
    "compute_sscd",
    "compute_sscd_registered",
    "sscd_band",
]
