"""Lock the accepted QA options without terminal-width-dependent help snapshots."""

from typer.main import get_command

from tests.contracts._snapshot import snapshot
from yt_uniquifier.cli.app import app


def test_qa_cli_options_are_stable() -> None:
    command = get_command(app).commands["qa"]
    snapshot("cli/qa.json", [{
        "name": param.name,
        "options": param.opts,
        "required": param.required,
        "default": param.default,
        "type": param.type.name,
        "min": getattr(param.type, "min", None),
        "max": getattr(param.type, "max", None),
        "choices": list(getattr(param.type, "choices", ())),
    } for param in command.params])
