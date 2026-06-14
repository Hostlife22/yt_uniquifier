# Versioning and Compatibility

Starting with **v1.0.0** (2026-06-14), `yt-uniquifier` follows
[Semantic Versioning 2.0.0](https://semver.org/) for every contract
listed in [API Contracts](api-contracts.md). The public API is
**stable**: code that imports `yt_uniquifier.core` or invokes
`yt-uniq` from a shell script can pin `yt-uniquifier~=1.0` and
expect non-breaking upgrades within the 1.x line.

## What counts as a "breaking change"

A change is **MAJOR** (1.x → 2.0) when it does one of the following
to a `stable`-labelled contract:

| Surface | Breaking action |
|---|---|
| `core.Plan`, `core.Profile`, `core.SourceMeta`, `core.EncoderCandidate` | Removing a field; renaming a field; tightening a type (e.g. `int | None` → `int`); changing default semantics |
| `core.RunEvent.kind` | Removing a `kind` literal; changing the payload schema of an existing `kind` |
| `core.RunOptions`, `core.RunSummary` | Removing a field; renaming; tightening a type |
| Plugin entry-point group `yt_uniquifier.transforms` | Changing the protocol that registered transforms must satisfy |
| CLI: `yt-uniq <subcommand>` | Removing a subcommand; removing or renaming a flag; changing the exit-code contract |
| YAML profile schema | Removing a top-level key; renaming a transform id; tightening `extra=forbid` to reject previously-accepted shapes |
| Python API entry points: `build_plan`, `run_full`, `compute_plan_hash` | Changing the call signature in a non-additive way |
| `Plan` JSON serialisation | A field that round-tripped in N.Y.Z no longer round-trips in (N+1).0.0 |

A change is **MINOR** (1.0 → 1.1) when it does any of:

- Adds a new optional field with a sensible default to a stable model
- Adds a new `RunEvent.kind` literal
- Adds a new CLI subcommand, flag, or environment variable
- Adds a new shipped profile YAML
- Adds a new transform (registered in `core/transforms/__init__.py`)
- Deprecates (but does not remove) any of the above; emits a
  `DeprecationWarning` for at least one full MINOR cycle before removal

A change is **PATCH** (1.0.0 → 1.0.1) when it is bug-fix only:
no contract surface moves, behavior under documented invariants is
restored to the documented contract.

## Stability labels

Every model field and CLI flag carries one of three labels in
[API Contracts](api-contracts.md):

- **`stable`** — covered by the SemVer commitment above.
- **`experimental`** — covered by the commitment, but a MINOR can
  remove it with a `DeprecationWarning`. Use at your own risk; pin
  to exact patch versions if you depend on experimental surface.
- **`internal`** — not covered. Anything in `_*` modules,
  `yt_uniquifier.core._internal`, or marked internal in
  [API Contracts](api-contracts.md). May change in any release.

A piece of API surface that has **no label** in
[API Contracts](api-contracts.md) is **`internal`** by default. If
you want to depend on it, open an RFC requesting promotion.

## RFC process for breaking changes

We are conservative about MAJOR bumps. Any PR that intends to break
a `stable` contract must:

1. **Open an RFC first.** Create a GitHub Discussion under category
   "RFCs" using the
   [RFC issue template](https://github.com/hostlife22/Video-Deduplicator/issues/new?template=rfc.yml).
   Title format: `RFC: <short summary>`.
2. **State the four required sections** in the template:
   - **Problem.** What current behavior is wrong, missing, or
     painful. Include concrete user-visible symptoms or use-cases.
   - **Proposal.** Exact new contract — field signatures,
     filter-string changes, CLI shape. The PR can prototype later,
     but the RFC must commit to the shape.
   - **Alternatives.** What additive (non-breaking) approaches were
     considered, and why they were rejected.
   - **Migration plan.** What users need to do to upgrade. Include
     a deprecation path if possible (warn for one MINOR, remove in
     the next MAJOR).
3. **Open for 7 days minimum.** RFCs cannot be accepted before 7
   calendar days have passed since the Discussion was opened. This
   gives users in different timezones a chance to weigh in.
4. **Maintainer sign-off.** A maintainer comments
   `accepted` or `rejected` once consensus is clear. An accepted
   RFC unblocks a PR labeled `breaking-change`.
5. **CHANGELOG entry.** Every accepted breaking change gets a
   dedicated bullet under the `### Removed` or `### Changed`
   heading of the next MAJOR release block, including a link back
   to the RFC.

Trivial breaking changes (e.g. dropping support for Python 3.10
when it reaches end-of-life) still require an RFC, but
maintainer-authored RFCs may use a shortened 3-day comment window.

## Supported versions

| Version line | Status | Bug fixes | Security fixes | Until |
|---|---|---|---|---|
| **1.0.x** | Active | yes | yes | TBD — see GitHub Releases |
| 0.9.x | End-of-life | no | no | 2026-06-14 |
| 0.8.x and earlier | End-of-life | no | no | superseded |

Security fixes for the active line are released as PATCH versions
within 30 calendar days of a confirmed CRITICAL or HIGH disclosure.
See [SECURITY.md](https://github.com/hostlife22/Video-Deduplicator/blob/main/SECURITY.md)
for the disclosure policy.

## Python version policy

`yt-uniquifier` supports the two newest stable Python releases. As
of v1.0.0 that means **Python 3.11 and 3.12**. When Python 3.13
becomes the second-newest release, support for 3.11 will be dropped
in the **next MINOR**, accompanied by a `DeprecationWarning` for one
release cycle. This is treated as a breaking change for downstream
packagers; an RFC is required but uses the 3-day shortened window.

## Profile YAML versioning

The shipped profiles (`src/yt_uniquifier/profiles/*.yaml`) follow a
parallel `profile_schema_version` field. Bumping that field counts
as a MAJOR change for the YAML schema even within a MINOR Python
release — i.e. a 1.x install will refuse to load a profile written
for the 2.x schema and vice versa. This protects users who share
profiles via [the marketplace](marketplace.md) from silent
misinterpretation.

## How to depend on yt-uniquifier safely

| Use case | Recommended pin |
|---|---|
| Application that calls `yt-uniq` from a shell script | `yt-uniquifier~=1.0` (MINOR-stable) |
| Library that imports `yt_uniquifier.core` and uses `stable` surface only | `yt-uniquifier~=1.0` |
| Library that uses any `experimental` surface | `yt-uniquifier==1.0.*` (PATCH-stable) |
| Reproducible research artifact / paper | exact pin, e.g. `yt-uniquifier==1.0.0` |
| Editable install for development | `pip install -e ".[dev,gui]"` from a checkout |
