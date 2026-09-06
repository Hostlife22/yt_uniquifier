"""Opt-in CLI quality gates preserve reports and the old exit behavior."""

import json

import pytest
from typer.testing import CliRunner

from tests.unit.test_qa_report import _report
from yt_uniquifier.cli import cmd_qa
from yt_uniquifier.cli.app import app
from yt_uniquifier.core.models import QACorrectness


@pytest.mark.parametrize("args,exit_code", [
    ([], 0), (["--min-vmaf", "95", "--min-ssim", ".95"], 2),
    (["--min-vmaf", "70", "--min-ssim", ".95"], 0),
    (["--min-vmaf", "nan"], 1), (["--quality-domain", "typo"], 2),
])
def test_cli_policy_exit_and_persisted_policy(monkeypatch, tmp_path, args, exit_code):
    source = tmp_path / "source.mp4"
    source.touch()
    def build(*a, **kwargs):
        return _report(
            vmaf_mean=80, ssim_mean=1, quality_policy=kwargs["quality_policy"],
            correctness=QACorrectness(
                status="passed", scope="pair_contract", full_decode_status="passed",
            ),
        )
    monkeypatch.setattr(cmd_qa, "build_report", build)
    result = CliRunner().invoke(app, ["qa", str(source), str(source), *args])
    assert result.exit_code == exit_code, result.output
    if args and args[0] == "--min-vmaf" and args[1] != "nan":
        report = json.loads(source.with_suffix(".mp4.qa.json").read_text())
        assert report["quality_policy"]["min_vmaf"] == float(args[1])
        assert source.with_suffix(".mp4.qa.html").exists()
