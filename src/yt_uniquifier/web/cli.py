"""`yt-uniq-web` — uvicorn launcher for the headless web UI.

Separate from ``cli.app`` so importing the main CLI never pulls
fastapi/uvicorn into the import graph for users who don't have
``[web]`` installed.

Env vars honoured (override CLI flags when set):
  YT_UNIQ_WEB_HOST        — default 127.0.0.1
  YT_UNIQ_WEB_PORT        — default 8080
  YT_UNIQ_WEB_WORK_DIR    — default ~/.cache/yt_uniquifier/web
  YT_UNIQ_WEB_OUTPUT_DIR  — default ./output
  YT_UNIQ_WEB_PROFILE_DIR — default per-user profile dir
  YT_UNIQ_WEB_INPUT_ROOT  — input boundary (default: current directory)
  YT_UNIQ_WEB_MAX_CONCURRENT_RUNS — shared output-dir run cap (default: 2)
  YT_UNIQ_WEB_RUN_RETENTION_SEC — terminal status retention (default: 604800)
  YT_UNIQ_WEB_MAX_RUN_RECORDS — persisted status cap (default: 1000)
  YT_UNIQ_WEB_USER        — when both set, enable basic auth
  YT_UNIQ_WEB_PASS
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _env_path(name: str, default: str | None) -> Path | None:
    val = os.environ.get(name) or default
    return Path(val).expanduser() if val else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yt-uniq-web",
        description="Headless FastAPI server for yt-uniquifier (v0.9.0 R4).",
    )
    parser.add_argument("--host", default=os.environ.get("YT_UNIQ_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("YT_UNIQ_WEB_PORT", "8080")))
    parser.add_argument("--work-dir",
                        default=os.environ.get("YT_UNIQ_WEB_WORK_DIR",
                                               str(Path.home() / ".cache" /
                                                   "yt_uniquifier" / "web")))
    parser.add_argument("--output-dir",
                        default=os.environ.get("YT_UNIQ_WEB_OUTPUT_DIR",
                                               "./output"))
    parser.add_argument("--profile-dir",
                        default=os.environ.get("YT_UNIQ_WEB_PROFILE_DIR"))
    parser.add_argument(
        "--input-root",
        default=os.environ.get("YT_UNIQ_WEB_INPUT_ROOT"),
        help="allowed input directory (default: current directory)",
    )
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError:
        sys.stderr.write(
            "uvicorn not installed. Install: pip install 'yt-uniquifier[web]'\n",
        )
        return 2

    from yt_uniquifier.web.app import WebConfig, build_app

    config = WebConfig(
        work_dir=Path(args.work_dir).expanduser(),
        output_dir=Path(args.output_dir).expanduser(),
        profile_dir=Path(args.profile_dir).expanduser() if args.profile_dir else None,
        input_root=_env_path("YT_UNIQ_WEB_INPUT_ROOT", args.input_root),
        basic_auth_user=os.environ.get("YT_UNIQ_WEB_USER"),
        basic_auth_pass=os.environ.get("YT_UNIQ_WEB_PASS"),
        max_concurrent_runs=int(
            os.environ.get("YT_UNIQ_WEB_MAX_CONCURRENT_RUNS", "2")
        ),
        run_retention_sec=int(os.environ.get("YT_UNIQ_WEB_RUN_RETENTION_SEC", "604800")),
        max_run_records=int(os.environ.get("YT_UNIQ_WEB_MAX_RUN_RECORDS", "1000")),
    )
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    app = build_app(config)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
