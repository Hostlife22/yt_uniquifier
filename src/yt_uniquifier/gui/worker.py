"""Backwards-compat shim. Real implementation in workers/run_worker.py.

Pre-v0.5 code imported `from yt_uniquifier.gui.worker import Worker`.
Keep that import path working until external integrations migrate.
"""

from yt_uniquifier.gui.workers.run_worker import RunWorker as Worker

__all__ = ["Worker"]
