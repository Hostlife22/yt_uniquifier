"""Contract snapshot tests for the SemVer-stable public surface.

A failing test in this directory does NOT necessarily indicate a bug
— it indicates that the public API has changed. The change may be:

- **Additive** (new optional field, new RunEvent kind) → MINOR bump.
- **Breaking** (removed field, renamed, type tightened) → MAJOR bump
  AND an accepted RFC per ``docs/versioning.md``.

Either way, the workflow to acknowledge the change is:

    python tools/regen_contract_goldens.py --apply

This rewrites the golden fixtures under
``tests/fixtures/contracts/``. Commit the regenerated goldens
together with the bump and the CHANGELOG entry that explains why
the change is justified.
"""
