"""Lock the explicit ``__all__`` of ``yt_uniquifier`` and ``yt_uniquifier.core``.

These two ``__all__`` lists are the *registry* of what we
guarantee. If something is removed from a list, it is no longer
covered by the SemVer contract — that is a MAJOR change.

If you intentionally promote an internal helper to the public
surface, add it here AND document the promotion in
``docs/api-contracts.md`` under its stability label.
"""

from __future__ import annotations

import yt_uniquifier
import yt_uniquifier.core
from tests.contracts._snapshot import snapshot


def test_top_level_all_is_stable() -> None:
    snapshot("public_surface/yt_uniquifier__all__.json", sorted(yt_uniquifier.__all__))


def test_core_all_is_stable() -> None:
    snapshot("public_surface/yt_uniquifier_core__all__.json", sorted(yt_uniquifier.core.__all__))
