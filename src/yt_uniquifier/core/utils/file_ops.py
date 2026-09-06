"""Bounded retries for transient Windows file sharing/antivirus locks."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_sharing_lock(
    operation: Callable[[], T], *, attempts: int = 12, max_delay_sec: float = 0.1,
) -> T:
    """Retry permission failures only; permanent failures still propagate.

    Callers must keep ownership checks inside the operation when retrying a
    destructive action. Disk-full, malformed records and other errors are not
    downgraded to transient sharing failures.
    """
    if attempts < 1 or max_delay_sec <= 0:
        raise ValueError("retry bounds must be positive")
    delay = min(0.01, max_delay_sec)
    for attempt in range(attempts):
        try:
            return operation()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, max_delay_sec)
    raise AssertionError("unreachable retry state")
