#!/usr/bin/env python3
"""Regenerate the contract goldens under ``tests/fixtures/contracts/``.

Workflow:

    python tools/regen_contract_goldens.py --apply

This sets ``YT_UNIQ_REGEN_CONTRACT_GOLDENS=1`` in the environment
and re-runs the contract test suite. Each test that uses
``tests.contracts._snapshot.snapshot`` writes its current value to
the golden file instead of asserting equality. The tests still
"pass" — what changes is the on-disk fixtures, which you then
commit.

Safety: ``--apply`` is required. A bare invocation only shows you
what *would* change (effectively a dry-run by re-running the
suite with goldens still in place).

When to use:

- You intentionally added a new field, RunEvent kind, or shipped
  profile (MINOR bump).
- You intentionally removed or renamed one (MAJOR bump, RFC
  required first — see ``docs/versioning.md``).
- A pydantic upgrade legitimately changed the JSON-schema shape
  in a non-breaking way (PATCH — usually a pin tightening too).

In all cases, the regenerated goldens MUST be committed alongside
the pyproject version bump AND a CHANGELOG.md entry describing
the change. The CI gate refuses to merge contract drift without a
matching CHANGELOG entry under an unreleased version block.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "tests" / "contracts"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts"


def _run_suite(*, regen: bool) -> int:
    env = os.environ.copy()
    if regen:
        env["YT_UNIQ_REGEN_CONTRACT_GOLDENS"] = "1"
    cmd = [sys.executable, "-m", "pytest", "tests/contracts", "-q"]
    completed = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite the goldens on disk. Without this flag the "
        "current goldens are checked normally and the script exits "
        "with the pytest return code.",
    )
    args = parser.parse_args()

    if not args.apply:
        print(
            "[regen] dry-run — running contract suite without "
            "regeneration. Add --apply to overwrite goldens.\n",
            file=sys.stderr,
        )
        return _run_suite(regen=False)

    print(
        "[regen] APPLYING — overwriting goldens under "
        f"{FIXTURES_DIR.relative_to(REPO_ROOT)}.\n"
        "[regen] Confirm afterwards:\n"
        "[regen]   * pyproject.toml version is bumped per SemVer\n"
        "[regen]   * CHANGELOG.md has an entry for the change\n"
        "[regen]   * docs/api-contracts.md reflects new surface\n"
        "[regen]   * for breaking changes, an accepted RFC exists\n",
        file=sys.stderr,
    )
    rc = _run_suite(regen=True)
    if rc != 0:
        print(
            f"[regen] pytest exited {rc} — see output above. The "
            "goldens may still have been partially written; review "
            "`git status tests/fixtures/contracts/` before committing.",
            file=sys.stderr,
        )
    else:
        print(
            "[regen] done. Review the diff with:\n"
            "[regen]   git diff -- tests/fixtures/contracts/",
            file=sys.stderr,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
