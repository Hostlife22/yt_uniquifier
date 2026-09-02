# Security Policy

`yt-uniquifier` is a desktop / CLI / headless-web tool that wraps
`ffmpeg` and processes user-supplied video files. It does not, in
its default configuration, expose a network service or process
untrusted input from third parties — but the `[web]` extra ships a
FastAPI app intended for LAN-trust deployments, and the headless
batch worker reads files from a shared directory that may be
multi-tenant. Take this scope into account when assessing impact.

## Supported versions

Security fixes land on the most recent MAJOR.MINOR line.

| Version           | Security fixes | End-of-life               |
| ----------------- | -------------- | ------------------------- |
| **1.3.x**         | ✅ active      | TBD — see GitHub Releases |
| 1.2.x and earlier | ❌ no          | superseded                |

For older users: please upgrade to the 1.3 line. See
[docs/versioning.md](docs/versioning.md) for the support policy in
general.

## Reporting a vulnerability

**Please do not open a public issue for a suspected vulnerability.**

Use one of the two channels below:

1. **Preferred — GitHub Private Vulnerability Reporting.** On the
   repository page, click **Security → Report a vulnerability** (or
   open [the direct link](https://github.com/hostlife22/Video-Deduplicator/security/advisories/new)).
   This creates a private advisory the maintainer can triage
   without disclosure.
2. **Email.** Send a report to
   **`sen.serafim.dev2gmail.com`** with subject prefix
   `[yt-uniquifier security]`. PGP encryption is welcome but not
   required; if you want a key, request one in the first message
   and a key will be published before any sensitive material is
   exchanged.

Include in your report:

- The affected version(s) — output of `yt-uniq --version` is enough.
- A clear description of the issue and the impact you can
  demonstrate (RCE, data exposure, denial-of-service, etc.).
- A minimal reproducer if possible (a profile YAML, a CLI
  invocation, or an HTTP request to the `[web]` server).
- Your preferred credit name for the eventual advisory, or "anonymous".

## Response timeline

| Severity                                                      | Triage (acknowledge)   | Fix shipped             |
| ------------------------------------------------------------- | ---------------------- | ----------------------- |
| **CRITICAL** (RCE, sandbox escape, arbitrary file write)      | within 1 business day  | within 14 calendar days |
| **HIGH** (privilege escalation, auth bypass, secret exposure) | within 3 business days | within 30 calendar days |
| **MEDIUM** (DoS, partial information disclosure)              | within 5 business days | within 60 calendar days |
| **LOW** (defense-in-depth, hardening)                         | best-effort            | next MINOR release      |

Severity is assigned using [CVSS v3.1](https://www.first.org/cvss/v3-1/specification-document)
qualitative levels. If we disagree on severity, we will explain why
in the triage response and welcome a counter-argument.

## Disclosure policy

We follow [coordinated disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure).
By default:

- **90 days** from your initial report, or **7 days** after a
  patched release ships, whichever comes first — at which point we
  will publish a GitHub Security Advisory and credit you (unless
  you requested anonymity).
- If we cannot ship a fix within the timeline above, we will tell
  you so before the disclosure window closes and propose a
  mitigation (e.g. a config flag, a docs warning) you can share.
- If you intend to publish before our advisory, please give us 7
  days' written notice via the original reporting channel so we
  can prepare a coordinated CHANGELOG entry.

## Scope

In scope:

- The `yt-uniquifier` Python package (`src/yt_uniquifier/**`).
- The shipped CLI (`yt-uniq`, `yt-uniq-gui`, `yt-uniq-web`).
- The shipped Docker image (`docker/Dockerfile`).
- The signed installers published on GitHub Releases.
- The shipped profile YAMLs (`src/yt_uniquifier/profiles/*.yaml`).
- The profile marketplace catalogue we host or bundle.

Out of scope:

- Third-party profiles installed via `yt-uniq profile install <URL>`
  from a non-trusted source. The marketplace docs ([marketplace.md](docs/marketplace.md))
  describe the SHA-pinning and HTTPS-only guarantees we provide;
  beyond those, you are trusting the upstream author.
- Vulnerabilities in `ffmpeg`, `fpcalc`, `whisper-cpp`, or any
  binary dependency. Please report those upstream.
- Misconfiguration of the `[web]` server outside the published
  hardening guidance in [docs/web.md](docs/web.md) — e.g.
  exposing the server to the public internet without basic-auth
  or behind no reverse proxy.
- Brute-force / credential-stuffing against the `[web]` basic-auth
  gate. We document that it is a thin LAN-trust gate, not a
  hardened auth system; production-grade auth belongs in front of
  it via a reverse proxy.

## Past advisories

There are no published advisories for `yt-uniquifier` as of v1.3.0.
A past CVE that affected a transitive dependency was mitigated in
v0.5.5 (A9) by pinning `Pillow>=10.3.1,<11` against CVE-2024-28219;
no `yt-uniquifier`-specific advisory was issued because the
vulnerable code path was not reachable from our usage of `Pillow`
via `imagehash`.

## Hall of fame

Researchers who reported valid security issues will be listed
here, with their permission. Contributions welcome.

_No entries yet — be the first._
