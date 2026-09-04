from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from tools import natural_corpus
from tools.natural_corpus import load_manifest


def _local_profile(tmp_path: Path) -> Path:
    profile = tmp_path / "soft.yaml"
    shutil.copy2("src/yt_uniquifier/profiles/soft.yaml", profile)
    return profile


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
                '{"vmaf_mean": 95.0, "ssim_mean": 0.99, '
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
