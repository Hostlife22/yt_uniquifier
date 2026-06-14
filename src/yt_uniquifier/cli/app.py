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
from yt_uniquifier.cli.cmd_worker import worker_cmd

app = typer.Typer(
    no_args_is_help=True,
    help=(
        "Re-encode owned/licensed video with controlled micro-transforms "
        "for YouTube re-upload. Intended for legitimate use (your own content, "
        "fair-use derivatives, re-cuts). Not for evading rights-holder detection."
    ),
)


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
