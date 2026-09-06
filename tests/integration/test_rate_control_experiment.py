"""Exercise the paired experiment and independent assessment on a real tiny clip."""

import json
from pathlib import Path

import pytest
import yaml

from tools.rate_control_experiment import assess_existing, run


@pytest.mark.integration
def test_paired_experiment_and_assessment(
    tmp_path: Path, tiny_clip: Path, isolated_cache: Path,
) -> None:
    import shutil

    source = tmp_path / "source.mp4"
    shutil.copy2(tiny_clip, source)
    profile = tmp_path / "profile.yaml"
    profile.write_text("name: controlled\ntransforms: []\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump({
        "schema_version": 1,
        "matrix": {"profiles": [profile.name], "encoders": ["libx264"], "workers": 1},
        "cases": [{"id": "owned", "source": source.name, "rights_status": "owned",
                   "rights_reference": "generated pytest tiny_clip", "media_class": "sdr"}],
    }), encoding="utf-8")
    results = tmp_path / "results"
    run(manifest, results, seconds=0.5, repeats=1, start_sec=0.25, vbv_multipliers=(2, 4))
    assess_existing(results)
    report = json.loads((results / "results.json").read_text())
    assert report["complete"] and len(report["rows"]) == 4
    assert {row["policy"] for row in report["rows"]} == {
        "source_cap", "crf_only", "cap_x2", "cap_x4",
    }
    assert all(row["start_sec"] == 0.25 for row in report["rows"])
    assert report["calibration"]["proposed_production_thresholds"] is None
    evidence = json.loads((results / "assessment.json").read_text())
    assert all(item["frame_pts_contract"] == "passed" for item in evidence["decoded_contracts"])
    assert "<video" in (results / "review.html").read_text()
    assert (results / "results.csv").is_file()
    with pytest.raises(FileExistsError):
        run(manifest, results, seconds=0.5, repeats=1)
