# Security policy

The canonical security policy ships in the repository root as
[`SECURITY.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/SECURITY.md)
so that GitHub auto-detects it and surfaces the
**Security → Report a vulnerability** button.

## Release inventory scope (v1.6.0 preparation)

`tools/bundle_sbom.py` inventories the actual native bundle/extracted AppImage,
not the packages installed in an unrelated runner. Four platform-specific
CycloneDX 1.5 documents include regular-file SHA-256 hashes, symlink targets and
package names/versions from bundled Python METADATA. Each document identifies the
archive hash and source commit; the release workflow verifies bindings and cosign
signatures. The older `sbom.cdx.json` remains explicitly build-environment evidence.

This uses CycloneDX's [file/component integrity representation](https://cyclonedx.org/guides/sbom/relationships/).
File coverage does not establish a complete dependency graph: opaque PYZ/static
binary contents, external OS libraries, and full license attribution remain
unverified. Cosign authenticates workflow provenance, not Apple notarization or
Windows Authenticode. Manual Docker qualification uses a unique candidate tag,
not `latest`/`edge`, when registry attestation verification is required.

## Reporting a vulnerability

Use one of these channels:

1. **GitHub Private Vulnerability Reporting** — on the repository
   page, **Security → Report a vulnerability**, or open
   [the direct link](https://github.com/hostlife22/Video-Deduplicator/security/advisories/new).
2. **Email** — `sen.serafim.dev2gmail.com` with subject prefix
   `[yt-uniquifier security]`.

**Please do not open a public issue for a suspected vulnerability.**

## Response timeline

| Severity | Acknowledge | Fix shipped |
|---|---|---|
| **CRITICAL** | within 1 business day | within 14 calendar days |
| **HIGH**     | within 3 business days | within 30 calendar days |
| **MEDIUM**   | within 5 business days | within 60 calendar days |
| **LOW**      | best-effort | next MINOR release |

## Supported versions

Security fixes land on the most recent MAJOR.MINOR line.

| Version | Security fixes |
|---|---|
| **1.3.x** | ✅ active |
| 1.2.x and earlier | ❌ end-of-life |

See the full policy — scope, disclosure timeline, hall of fame —
in [`SECURITY.md`](https://github.com/hostlife22/Video-Deduplicator/blob/main/SECURITY.md).

For the SemVer commitment and how breaking changes are proposed,
see [Versioning](versioning.md).
