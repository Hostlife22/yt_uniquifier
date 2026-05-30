import pytest
from typer.testing import CliRunner

from yt_uniquifier import __version__
from yt_uniquifier.cli.app import app

pytestmark = pytest.mark.smoke


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_command() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "yt-uniq" in result.stdout.lower() or "re-encode" in result.stdout.lower()
