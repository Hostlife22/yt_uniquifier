"""Headless FastAPI web UI for yt-uniquifier (v0.9.0 R4 / F13).

Surface deliberately narrow: enough to run an encode, install a
community profile, and view the QA report from a browser. Heavy GUI
features (calibrate, batch, queue) stay in the Qt desktop client.

The web layer is a *thin shell* — every endpoint builds a Plan +
RunOptions exactly like the CLI/GUI shells do and hands them to
``core.orchestrator.run_full``. No business logic here.

Auth: basic auth only when both ``YT_UNIQ_WEB_USER`` and
``YT_UNIQ_WEB_PASS`` env vars are set. Default bind is
``127.0.0.1`` so a fresh install never accidentally exposes the
filesystem to the LAN.
"""

from yt_uniquifier.web.app import build_app

__all__ = ["build_app"]
