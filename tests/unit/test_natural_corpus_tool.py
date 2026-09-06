from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tools import natural_corpus
from tools.natural_corpus import load_manifest


def test_measured_qa_does_not_imply_quality_acceptance() -> None:
    from yt_uniquifier.core.models import QAReport

    report = QAReport(
        input_md5="source", output_md5="output", input_size_bytes=1, output_size_bytes=2,
        input_duration_sec=1, output_duration_sec=1, duration_match=True,
        phash_samples=1, phash_distance_min=0, phash_distance_mean=0,
        phash_distance_max=0, phash_similarity=1, vmaf_mean=3.7,
        vmaf_registered_mean=93.8, ssim_mean=0.89,
    )
    result = natural_corpus._qa_verdict(report.model_dump())
    assert result["status"] == "red"
    assert result["correctness"] == "valid"
    assert result["quality"] == "fail"
    assert natural_corpus._qa_verdict({})["status"] == "NOT VERIFIED"


def _local_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "soft.yaml"
    shutil.copy2("src/yt_uniquifier/profiles/soft.yaml", profile)
    return profile


def test_existing_benchmark_evidence_is_never_overwritten(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    manifest = load_manifest(
        _write_manifest(tmp_path, _manifest(Path(profile.name))), require_media=False,
    )
    results = tmp_path / "results"
    results.mkdir()
    previous = results / "benchmark.json"
    previous.write_text("previous evidence")
    with pytest.raises(ValueError, match="choose a fresh"):
        natural_corpus.run_manifest(manifest, results, with_sscd=False)
    assert previous.read_text() == "previous evidence"


def test_nonfinite_loudness_is_unavailable_not_json_infinity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(natural_corpus, "measure", lambda path: SimpleNamespace(
        input_i=float("-inf"), input_tp=float("-inf"),
    ))
    lufs, peak, note = natural_corpus._audio_metrics(tmp_path / "silence.wav")
    assert lufs is None and peak is None
    assert note is not None and "nonfinite" in note
    for invalid in (float("nan"), float("inf"), True):
        assert natural_corpus._number({"value": invalid}, "value") is None


def _manifest(profile: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "matrix": {"profiles": [str(profile)], "encoders": ["libx264"], "workers": 1},
        "cases": [
            {
                "id": "licensed-sdr",
                "source": "media/source.mkv",
                "rights_status": "licensed",
                "rights_reference": "licence-42",
                "media_class": "sdr",
                "review_cues": ["dialogue at 00:00:05"],
            }
        ],
    }


def _write_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_manifest_requires_real_media_by_default(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    manifest = _write_manifest(tmp_path, _manifest(Path(profile.name)))

    with pytest.raises(ValueError, match="source does not exist"):
        load_manifest(manifest)

    loaded = load_manifest(manifest, require_media=False)
    assert loaded.cases[0].rights_reference == "licence-42"


@pytest.mark.parametrize("rights_status", ["", "unknown", "fair-use-assumed"])
def test_manifest_rejects_missing_or_unrecognized_rights(
    tmp_path: Path, rights_status: str
) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["cases"][0]["rights_status"] = rights_status  # type: ignore[index]
    manifest = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError, match="rights_status"):
        load_manifest(manifest, require_media=False)


def test_manifest_rejects_source_path_escape(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["cases"][0]["source"] = "../private.mkv"  # type: ignore[index]
    manifest = _write_manifest(tmp_path, payload)

    with pytest.raises(ValueError, match="escapes"):
        load_manifest(manifest, require_media=False)


def test_runner_reuses_existing_benchmark_and_qa_pipelines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    source = tmp_path / "media" / "source.mkv"
    source.parent.mkdir()
    source.write_bytes(b"owned test fixture")
    manifest = load_manifest(_write_manifest(tmp_path, payload))
    commands: list[list[str]] = []

    def fake_capture(command: list[str], log: Path) -> int:
        commands.append(command)
        log.write_text("ok\n", encoding="utf-8")
        if Path(command[1]).name == "benchmark.py":
            output = Path(command[command.index("--out") + 1])
            output.write_bytes(b"encoded")
            benchmark = Path(command[command.index("--json") + 1])
            benchmark.write_text(
                '{"wall_sec": 1.0, "rss_peak_kb": 1024}\n', encoding="utf-8",
            )
        elif command[1] == "qa":
            qa = Path(command[command.index("--json") + 1])
            qa.write_text(
                '{"vmaf_mean": 95.0, "vmaf_registered_mean": 96.0, '
                '"ssim_mean": 0.99, "ssim_registered_mean": 0.995, '
                '"output_duration_sec": 1.0}\n',
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(natural_corpus, "_capture", fake_capture)
    monkeypatch.setattr(natural_corpus, "_psnr", lambda *_args: (40.0, None))
    monkeypatch.setattr(
        natural_corpus,
        "_audio_metrics",
        lambda *_args: (-14.0, -1.0, None),
    )
    results = tmp_path / "results"

    assert natural_corpus.run_manifest(manifest, results, with_sscd=True) == 0
    assert len(commands) == 2
    assert Path(commands[0][1]).name == "benchmark.py"
    assert "--accept-watermark-risk" in commands[0]
    assert "--plan-json" in commands[0]
    assert any(value.endswith("output.mp4") for value in commands[0])
    assert commands[1][1] == "qa"
    assert "--plan-json" in commands[1]
    assert "--sscd" in commands[1]
    summary = yaml.safe_load((results / "summary.json").read_text(encoding="utf-8"))
    assert summary["cells"][0]["rights_reference"] == "licence-42"
    assert (results / "summary.csv").is_file()
    assert (results / "summary.html").is_file()


def test_manifest_accepts_named_current_and_proposed_variants(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["matrix"] = {
        "workers": 2,
        "variants": [
            {"id": "current", "profile": profile.name, "encoder": "libx264"},
            {"id": "proposed", "profile": profile.name, "encoder": "libx265"},
        ],
    }

    manifest = load_manifest(
        _write_manifest(tmp_path, payload),
        require_media=False,
    )

    assert [variant.variant_id for variant in manifest.variants] == [
        "current", "proposed",
    ]
    assert manifest.variants[1].encoder == "libx265"
    assert manifest.workers == 2


def test_manifest_rejects_duplicate_variant_id(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["matrix"] = {
        "variants": [
            {"id": "current", "profile": profile.name, "encoder": "libx264"},
            {"id": "current", "profile": profile.name, "encoder": "libx265"},
        ],
    }

    with pytest.raises(ValueError, match="duplicate variant id"):
        load_manifest(_write_manifest(tmp_path, payload), require_media=False)


def test_case_can_select_compatible_named_variants(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["matrix"] = {
        "variants": [
            {"id": "sdr", "profile": profile.name, "encoder": "libx264"},
            {"id": "hdr", "profile": profile.name, "encoder": "libx265"},
        ],
    }
    payload["cases"][0]["variants"] = ["sdr"]  # type: ignore[index]

    manifest = load_manifest(_write_manifest(tmp_path, payload), require_media=False)

    assert manifest.cases[0].variant_ids == ("sdr",)


def test_case_rejects_unknown_variant(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["cases"][0]["variants"] = ["missing"]  # type: ignore[index]

    with pytest.raises(ValueError, match="unknown variants"):
        load_manifest(_write_manifest(tmp_path, payload), require_media=False)


def test_case_rejects_duplicate_variant_selection(tmp_path: Path) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    payload["cases"][0]["variants"] = ["current", "current"]  # type: ignore[index]

    with pytest.raises(ValueError, match="duplicate variants"):
        load_manifest(_write_manifest(tmp_path, payload), require_media=False)


def test_comparison_uses_current_as_baseline() -> None:
    cells = [
        {
            "case_id": "film",
            "variant_id": "proposed",
            "metrics": {"vmaf": 94.0, "wall_sec": 8.0},
        },
        {
            "case_id": "film",
            "variant_id": "current",
            "metrics": {"vmaf": 90.0, "wall_sec": 10.0},
        },
    ]

    comparison = natural_corpus._comparisons(cells)

    assert comparison == [{
        "case_id": "film",
        "baseline_variant": "current",
        "candidate_variant": "proposed",
        "deltas": {"vmaf": 4.0, "wall_sec": -2.0},
    }]


def test_video_only_case_treats_audio_metrics_as_not_applicable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _local_profile(tmp_path)
    payload = _manifest(Path(profile.name))
    source = tmp_path / "media" / "source.mkv"
    source.parent.mkdir()
    source.write_bytes(b"open video-only fixture")
    manifest = load_manifest(_write_manifest(tmp_path, payload))

    def fake_capture(command: list[str], log: Path) -> int:
        log.write_text("ok\n", encoding="utf-8")
        if Path(command[1]).name == "benchmark.py":
            Path(command[command.index("--out") + 1]).write_bytes(b"encoded")
            Path(command[command.index("--json") + 1]).write_text(
                '{"wall_sec": 1.0, "rss_peak_kb": 100}\n', encoding="utf-8",
            )
        else:
            Path(command[command.index("--json") + 1]).write_text(
                '{"vmaf_mean": 95.0, "vmaf_registered_mean": 96.0, '
                '"ssim_mean": 0.99, "ssim_registered_mean": 0.995, '
                '"output_duration_sec": 1.0}\n',
                encoding="utf-8",
            )
        return 0

    monkeypatch.setattr(natural_corpus, "_capture", fake_capture)
    monkeypatch.setattr(
        natural_corpus,
        "probe",
        lambda _path: SimpleNamespace(duration_sec=1.0, audio=[]),
    )
    monkeypatch.setattr(natural_corpus, "_psnr", lambda *_args: (40.0, None))
    monkeypatch.setattr(
        natural_corpus,
        "_audio_metrics",
        lambda *_args: pytest.fail("video-only case must not measure audio"),
    )

    results = tmp_path / "results"
    assert natural_corpus.run_manifest(manifest, results, with_sscd=False) == 0
    summary = yaml.safe_load((results / "summary.json").read_text(encoding="utf-8"))
    metrics = summary["cells"][0]["metrics"]
    assert metrics["complete"] is True
    assert metrics["lufs_i"] is None
    assert metrics["true_peak_dbtp"] is None


def test_metric_policy_uses_registered_domain_and_skips_hdr_vmaf() -> None:
    assert natural_corpus._required_metric_names(
        media_class="sdr", keep_hdr=False, source_has_audio=True,
    ) == (
        "registered_ssim",
        "psnr_db",
        "output_size_bytes",
        "duration_sec",
        "wall_sec",
        "rss_peak_kb",
        "registered_vmaf",
        "lufs_i",
        "true_peak_dbtp",
    )
    assert "registered_vmaf" not in natural_corpus._required_metric_names(
        media_class="hdr10", keep_hdr=True, source_has_audio=False,
    )
    assert "vmaf" not in natural_corpus._required_metric_names(
        media_class="hdr10", keep_hdr=False, source_has_audio=False,
    )
