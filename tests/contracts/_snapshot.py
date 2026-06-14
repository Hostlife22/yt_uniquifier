"""Shared snapshot helper for contract tests.

A snapshot is a JSON file under ``tests/fixtures/contracts/`` that
records the SemVer-stable shape of one public surface (a model
schema, a YAML's dumped form, a literal's members, a dataclass'
field signatures). The helper:

- Compares a freshly-computed value against the golden file.
- When ``YT_UNIQ_REGEN_CONTRACT_GOLDENS=1`` is set in the
  environment, rewrites the golden on disk instead. This is what
  ``tools/regen_contract_goldens.py`` uses; CI never sets it.
- Surfaces a single, actionable failure message that points back
  at the regen helper, the versioning policy, and the RFC process.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONTRACTS_DIR = Path(__file__).parent.parent / "fixtures" / "contracts"
REGEN_ENV = "YT_UNIQ_REGEN_CONTRACT_GOLDENS"


def _normalise(value: Any) -> str:
    """Stable, diff-friendly JSON representation.

    sort_keys + indent=2 means a small contract change produces a
    minimal diff. Trailing newline matches what most editors write.
    """
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def snapshot(relative_path: str, value: Any) -> None:
    """Compare ``value`` against the golden at ``relative_path``.

    The path is resolved under ``tests/fixtures/contracts/`` so
    callers can pass ``"profile.schema.json"`` or
    ``"profiles/soft.json"`` without repeating the prefix.

    Raises:
        AssertionError: if the golden file is missing or differs.
    """
    golden_path = CONTRACTS_DIR / relative_path
    serialised = _normalise(value)

    if os.environ.get(REGEN_ENV) == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(serialised, encoding="utf-8")
        return

    if not golden_path.exists():
        raise AssertionError(
            f"Missing golden fixture: {golden_path.relative_to(Path.cwd())}.\n"
            f"Run `python tools/regen_contract_goldens.py --apply` to "
            f"create it. If you are introducing a new public surface, "
            f"this is expected; document the addition in CHANGELOG.md "
            f"under the next MINOR.",
        )

    actual = serialised
    expected = golden_path.read_text(encoding="utf-8")
    if actual == expected:
        return

    raise AssertionError(
        f"Contract drift detected at {golden_path.relative_to(Path.cwd())}.\n"
        f"A diff in this file means the SemVer-stable public surface\n"
        f"has changed. See docs/versioning.md for what counts as\n"
        f"breaking.\n"
        f"\n"
        f"If the change is intentional:\n"
        f"  1. Confirm whether it is additive (MINOR) or breaking (MAJOR).\n"
        f"  2. For breaking changes, an accepted RFC is required first\n"
        f"     — see docs/versioning.md#rfc-process-for-breaking-changes.\n"
        f"  3. Regenerate goldens:\n"
        f"       python tools/regen_contract_goldens.py --apply\n"
        f"  4. Add a CHANGELOG.md entry under the next release block.\n"
        f"  5. Commit the bumped pyproject + CHANGELOG + regenerated\n"
        f"     fixtures together.\n"
        f"\n"
        f"--- expected ({len(expected)} bytes)\n"
        f"+++ actual   ({len(actual)} bytes)\n"
        f"\n"
        f"First 400 bytes of expected:\n{expected[:400]!r}\n"
        f"\n"
        f"First 400 bytes of actual:\n{actual[:400]!r}",
    )
