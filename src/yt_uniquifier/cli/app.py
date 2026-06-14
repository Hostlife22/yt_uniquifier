import logging
import os

import typer

from yt_uniquifier.cli.cmd_batch import batch_cmd
from yt_uniquifier.cli.cmd_calibrate import calibrate_cmd
from yt_uniquifier.cli.cmd_corpus import corpus_app
from yt_uniquifier.cli.cmd_preflight import preflight_cmd
from yt_uniquifier.cli.cmd_probe import probe_cmd
from yt_uniquifier.cli.cmd_profile import profile_app
from yt_uniquifier.cli.cmd_qa import qa_cmd
from yt_uniquifier.cli.cmd_queue import queue_app
from yt_uniquifier.cli.cmd_run import run_cmd
from yt_uniquifier.cli.cmd_subtitles import subtitles_app
from yt_uniquifier.cli.cmd_telemetry import telemetry_app
from yt_uniquifier.cli.cmd_worker import worker_cmd
from yt_uniquifier.core.plugins import drop_disabled_plugins

_log = logging.getLogger(__name__)

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Re-encode owned/licensed video with controlled micro-transforms "
        "for YouTube re-upload. Intended for legitimate use (your own content, "
        "fair-use derivatives, re-cuts). Not for evading rights-holder detection."
    ),
)


@app.callback()
def _global(
    ctx: typer.Context,
    no_plugins: bool = typer.Option(
        False,
        "--no-plugins",
        help=(
            "Disable all third-party transform plugins for this run.  "
            "Set YT_UNIQ_NO_PLUGINS=1 for fully pre-import skipping "
            "(plugin distributions are not even imported).  The CLI flag "
            "performs post-load filtering — equivalent in effect for "
            "the current process, but a malicious plugin's import-time "
            "side effects have already run."
        ),
    ),
    plugins_allowlist: str | None = typer.Option(
        None,
        "--plugins-allowlist",
        help=(
            "Comma-separated list of plugin names to keep enabled; all "
            "others are dropped.  Mutually-exclusive convenience over "
            "YT_UNIQ_PLUGINS_ALLOWLIST.  Plugin names come from the "
            "[plugin].name field of each plugin's yt_uniquifier_plugin.toml."
        ),
    ),
    unsafe_plugins: bool = typer.Option(
        False,
        "--unsafe-plugins",
        help=(
            "Disable the audit-hook sandbox that catches denylisted "
            "syscalls in plugin code.  Use only with trusted internal "
            "plugins; do not use with PyPI installs."
        ),
    ),
) -> None:
    """v1.2.0 Task 23 — plugin governance flags applied before any
    subcommand runs.  The env-var equivalents take effect before
    plugin discovery and are the right choice for production
    deployments; the CLI flags are a convenience for ad-hoc one-off
    runs and rely on post-load filtering."""
    if no_plugins:
        os.environ["YT_UNIQ_NO_PLUGINS"] = "1"
        dropped = drop_disabled_plugins(no_plugins=True)
        if dropped:
            _log.info("dropped plugins per --no-plugins: %s", ", ".join(dropped))
    elif plugins_allowlist is not None:
        os.environ["YT_UNIQ_PLUGINS_ALLOWLIST"] = plugins_allowlist
        allowed = frozenset(
            tok.strip() for tok in plugins_allowlist.split(",") if tok.strip()
        )
        dropped = drop_disabled_plugins(allowlist=allowed)
        if dropped:
            _log.info(
                "dropped plugins not in --plugins-allowlist=%s: %s",
                plugins_allowlist, ", ".join(dropped),
            )
    if unsafe_plugins:
        from yt_uniquifier.core.plugin_sandbox import disable_sandbox
        disable_sandbox()


@app.command()
def version() -> None:
    """Print version."""
    from yt_uniquifier import __version__

    typer.echo(__version__)


app.command("probe")(probe_cmd)
app.command("run")(run_cmd)
app.command("preflight")(preflight_cmd)
app.command("qa")(qa_cmd)
app.command("batch")(batch_cmd)
app.command("calibrate")(calibrate_cmd)
app.command("worker")(worker_cmd)
app.add_typer(corpus_app, name="corpus")
app.add_typer(queue_app, name="queue")
app.add_typer(profile_app, name="profile")
app.add_typer(subtitles_app, name="subtitles")
app.add_typer(telemetry_app, name="telemetry")
