"""Bounded registration must align drift without rewarding tiny overlaps."""

from __future__ import annotations

import numpy as np
import pytest

from yt_uniquifier.core.qa import sscd
from yt_uniquifier.core.qa.audio_fp import align_fingerprints
from yt_uniquifier.core.qa.sscd import align_cosine_matrix


def test_audio_registration_finds_fixed_offset() -> None:
    reference = [index * 0x01010101 for index in range(20)]
    candidate = [0xAAAAAAAA, 0x55555555, *reference]

    result = align_fingerprints(
        reference,
        candidate,
        max_offset_frames=4,
        max_drift_frames=0,
    )

    assert result.available
    assert result.offset_frames == 2
    assert result.drift_frames == 0
    assert result.hamming_per_frame == 0.0
    assert result.compared_frames == 20
    assert result.coverage_ratio == pytest.approx(20 / 22)
    assert result.hamming_per_window == (0.0, 0.0, 0.0, 0.0, 0.0)


def test_audio_registration_finds_bounded_linear_drift() -> None:
    reference = [index * 0x01020408 for index in range(20)]
    candidate = [0xFFFFFFFF] * 22
    for index, value in enumerate(reference):
        mapped = round(index + 2 * index / (len(reference) - 1))
        candidate[mapped] = value

    result = align_fingerprints(
        reference,
        candidate,
        max_offset_frames=0,
        max_drift_frames=3,
    )

    assert result.available
    assert result.offset_frames == 0
    assert result.drift_frames == 2
    assert result.hamming_per_frame == 0.0


def test_audio_registration_rejects_short_matching_excerpt() -> None:
    reference = list(range(20))
    candidate = reference[:5]

    result = align_fingerprints(reference, candidate, max_offset_frames=4)

    assert not result.available
    assert result.hamming_per_frame is None
    assert result.coverage_ratio == 0.0


def test_sscd_registration_recovers_monotonic_diagonal() -> None:
    matrix = [[-0.5] * 7 for _ in range(5)]
    for row in range(5):
        matrix[row][round(row * 6 / 4)] = 1.0

    result = align_cosine_matrix(matrix, max_displacement_frames=2)

    assert result.available
    assert result.mean_similarity == 1.0
    assert result.compared_frames == 5
    assert result.coverage_ratio == pytest.approx(5 / 7)
    assert result.max_displacement_frames == 0


def test_sscd_registration_rejects_adversarial_low_overlap() -> None:
    matrix = [[-1.0] * 10 for _ in range(10)]
    matrix[4][4] = 1.0
    matrix[5][5] = 1.0

    result = align_cosine_matrix(matrix, max_displacement_frames=2)

    assert not result.available
    assert result.mean_similarity is None
    assert result.coverage_ratio < 0.60


def test_sscd_registration_never_reuses_candidate_frame() -> None:
    matrix = [[-1.0] * 6 for _ in range(6)]
    for row in range(6):
        matrix[row][3] = 1.0

    result = align_cosine_matrix(matrix, max_displacement_frames=4)

    # Only one source row can consume candidate column 3. The alignment cannot
    # report a high-confidence six-frame match by reusing it.
    assert not result.available or result.confidence < 0.50


def test_sscd_reference_embedding_cache_recovers_from_corruption(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = [tmp_path / "one.png", tmp_path / "two.png"]
    for frame in frames:
        frame.touch()
    monkeypatch.setenv("YT_UNIQ_QA_CACHE_DIR", str(tmp_path / "cache"))
    calls = 0

    def embed(_model, _frames):
        nonlocal calls
        calls += 1
        return np.asarray([[1.0, 0.0], [0.0, 1.0]])

    monkeypatch.setattr(sscd, "_embed_frames", embed)
    first = sscd._load_or_embed_reference(object(), frames, cache_key="reference")
    second = sscd._load_or_embed_reference(object(), frames, cache_key="reference")
    assert np.array_equal(first, second)
    assert calls == 1

    cache_file = next((tmp_path / "cache" / "sscd_embeddings").glob("*.npy"))
    cache_file.write_bytes(b"corrupt")
    recovered = sscd._load_or_embed_reference(object(), frames, cache_key="reference")
    assert np.array_equal(first, recovered)
    assert calls == 2


def test_sscd_long_form_alignment_never_exceeds_time_bound(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = tmp_path / "reference.mkv"
    output = tmp_path / "output.mkv"
    reference.touch()
    output.touch()
    observed_counts: list[int] = []

    class Meta:
        duration_sec = 3600.0

    monkeypatch.setattr("yt_uniquifier.core.probe.probe", lambda _path: Meta())

    def extract(_path, directory, *, frame_count, **_kwargs):
        observed_counts.append(frame_count)
        return [directory / f"{index}.png" for index in range(frame_count)]

    monkeypatch.setattr(sscd, "_extract_frames", extract)
    embeddings = np.eye(32, dtype=np.float32)
    monkeypatch.setattr(sscd, "_load_or_embed_reference", lambda *_a, **_k: embeddings)
    monkeypatch.setattr(sscd, "_embed_frames", lambda *_a, **_k: embeddings)

    result = sscd.compute_sscd_registered(
        reference,
        output,
        frame_count=32,
        max_displacement_frames=4,
        max_offset_sec=10.0,
        model_loader=lambda: object(),
    )

    # A sparse one-hour grid cannot resolve a ten-second displacement, so the
    # aligner must not silently widen that bound to four 112.5-second samples.
    assert observed_counts == [32, 32]
    assert result.available
    assert result.mean_offset_sec == pytest.approx(0.0)
    assert "interval=112.500s" in (result.note or "")
