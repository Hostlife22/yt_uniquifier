# Contributing to yt-uniquifier

Thanks for considering a contribution. This document covers the dev
loop, the commit + PR conventions, and the RFC process required for
any change that touches a **stable contract surface** (see
[`docs/versioning.md`](docs/versioning.md)).

For project scope, code style, and architectural invariants, read
[`CLAUDE.md`](CLAUDE.md) first — it is the canonical project guide.

## Quick start

```bash
git clone https://github.com/Hostlife22/yt-uniquifier.git
cd yt-uniquifier
make dev                          # .venv + pip install -e ".[dev,gui]"
make check                        # ruff + mypy --strict + full pytest
```

You need `ffmpeg` + `ffprobe` on PATH. Optional binaries (graceful
skip if absent): `fpcalc` (chromaprint), `ffmpeg` built with
`libvmaf`. macOS: `brew install ffmpeg`. Linux: `apt install ffmpeg`.
Windows: `choco install ffmpeg`.

If you do not need the GUI, `make dev-min` skips PyQt6 (cuts install
size + speeds up CI loops on headless hosts).

## Bug reports + feature requests

File via the GitHub issue templates — they collect the environment
data we need to triage without back-and-forth:

| Template                                  | When to use                                                       |
|-------------------------------------------|-------------------------------------------------------------------|
| **Bug report**                            | Anything that doesn't behave as documented.                       |
| **Feature request**                       | New functionality that does **not** change a stable contract.     |
| **RFC** (Request For Comments)            | Any change to `Plan`, `Profile`, `RunEvent`, CLI flags, GUI screens. |
| **Security report** (private)             | Suspected vulnerability. See [`SECURITY.md`](SECURITY.md).        |

## RFC process

A change is "RFC-required" when it would alter a contract listed as
**stable** in [`docs/api-contracts.md`](docs/api-contracts.md):

- Adding, removing, renaming, or retyping a field on `Plan`, `Profile`,
  `SourceMeta`, `RunEvent`, `EncoderCandidate`, `QAReport`, or their
  nested pydantic models.
- Adding, removing, or renaming a CLI subcommand or a documented flag.
- Adding, removing, or renaming a `RunEvent.kind` literal.
- Removing or renaming a shipped profile.
- Adding a new `RunEvent` payload field that downstream consumers
  might depend on.

Internal helpers, test utilities, and anything under `_*` private
names do **not** require an RFC — open a normal PR.

### Workflow

1. **Open an RFC issue** using the RFC template. The four required
   sections are *Problem*, *Proposal*, *Alternatives*, and *Migration
   plan*. Be explicit about the breaking-change classification
   (MAJOR / MINOR / PATCH per SemVer).
2. **Comment window**: minimum 7 calendar days. The author can
   request a shortening for hotfixes, but it must be explicitly
   justified in the issue.
3. **Decision**: maintainers post `LGTM`, `Block`, or `Revisit` with a
   one-paragraph rationale. Two `LGTM` and zero `Block` → ready to
   merge.
4. **Implementation PR**: must reference the RFC issue, include a
   `CHANGELOG.md` entry, update `docs/api-contracts.md`, and refresh
   the contract snapshot goldens (`python tools/regen_contract_goldens.py --apply`).
5. **MAJOR-version bumps** are batched — RFCs that imply a MAJOR
   bump wait until the next planned major (e.g. v2.0). PATCH and MINOR
   RFCs ship in the next normal release.

If you are unsure whether your change needs an RFC, open the RFC
template and we will downgrade it to a normal PR if it doesn't.

## Commit messages

Conventional format, lowercase type tags. The repo uses these in tag
filtering and the auto-generated release notes:

```
<type>(<scope>): <subject>

<body — wrap at ~72 cols, explain *why* not *what*>
```

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `ci`,
`chore`, `build`. Scopes are codebase areas — `core`, `gui`, `cli`,
`web`, `docs`, `ci`, or a more specific module (`core/segmenter`,
`gui/queue`).

The body should explain *why* the change was needed and what
alternatives were considered, especially for `fix:` commits where the
diff alone often hides the failure mode.

## Pull request checklist

Before requesting review:

- [ ] `make check` is green locally (ruff + mypy --strict + full pytest).
- [ ] If you touched `core/`: coverage on changed files ≥ 85 %
      (`pytest --cov=src/yt_uniquifier/core --cov-fail-under=80` is the
      CI gate; v1.1 raises it to 85).
- [ ] If you touched a stable contract: RFC issue linked, snapshot
      goldens regenerated, `CHANGELOG.md` entry added.
- [ ] If you added a transform: snapshot test on the generated
      `filter_complex` string (see `tests/unit/test_transforms.py`).
- [ ] If you touched the GUI: `pytest tests/gui/test_wcag_aa_compliance.py -v`
      stays green; refresh `tests/visual/__snapshots__/*.png` with
      `UPDATE_VISUAL_BASELINES=1` if QSS changed.
- [ ] PR description references issues with `Closes #N`, `Fixes #N`,
      or `Refs #N`.

## Tests

The TDD workflow is described in [`CLAUDE.md`](CLAUDE.md) — TL;DR:
write the failing test first, implement, refactor. Snapshot tests
catch contract drift; unit tests run in <10 s and require no ffmpeg;
integration tests invoke real `ffmpeg` and live behind
`@pytest.mark.integration`.

When fixing a bug, the regression test goes in **before** the fix in
the same PR. If the bug cannot be reproduced as a test (e.g. timing
flake), document the manual repro in the commit body.

## Code of conduct

Be kind. Assume good faith. If a discussion gets heated, step away
and respond when you are ready. We have neither the time nor the
appetite for harassment; maintainers will close threads (and
contributors, if necessary) that fail to respect this.

## Asking for help

- Open an issue with the **Question** label — most are answered
  within 48 h.
- For real-time chat there is no official channel yet; this may
  change post-v1.0.
- The mkdocs site at <https://hostlife22.github.io/yt_uniquifier/>
  is the authoritative reference.

Thanks again, and welcome.
