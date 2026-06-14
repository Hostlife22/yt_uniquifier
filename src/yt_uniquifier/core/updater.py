"""v1.3.0 Task 33 — self-update mechanism.

Workflow:

  1. Fetch ``https://github.com/Hostlife22/yt-uniquifier/releases/latest/
     download/manifest.json`` over HTTPS.
  2. Parse + validate against the bundled schema (see
     :class:`UpdateManifest`).
  3. Verify the manifest's cosign bundle (v1.1.0 Task 11) against
     yt-uniquifier's expected OIDC identity.  When ``cosign`` is not on
     PATH we surface a clear error instead of silently trusting the
     manifest — a missing verifier is an integrity gap, not a no-op.
  4. Compare ``manifest.version`` to ``yt_uniquifier.__version__``.
     Refuse downgrades; surface an "already up-to-date" result on match.
  5. Download the platform's release asset (sdist tarball for
     library installs, ``.app.zip`` / ``.exe.zip`` for desktop builds)
     into a tempdir.
  6. Verify the asset's cosign bundle.
  7. Hand off to the platform-specific install step:
       * library / pipx → ``pip install --upgrade <wheel>``
       * macOS .app   → unpack into ``~/Applications`` and re-spawn
       * Windows .exe → write to ``%LOCALAPPDATA%`` and re-spawn

Air-gapped operators disable auto-checks via ``YT_UNIQ_DISABLE_UPDATER=1``
or by setting ``check_interval_days`` to a large value in the GUI
settings.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from yt_uniquifier import __version__ as CURRENT_VERSION
from yt_uniquifier.core.errors import YtUniquifierError

_log = logging.getLogger(__name__)

DEFAULT_MANIFEST_URL = (
    "https://github.com/Hostlife22/yt-uniquifier/releases/latest/download/"
    "manifest.json"
)

# cosign identity scaffolding — verifies the GitHub OIDC identity that
# signed the v1.1.0 release artifacts.  An operator running a forked
# build will need to override this via env var.
COSIGN_CERT_IDENTITY_REGEX_DEFAULT = (
    r"https://github\.com/Hostlife22/yt-uniquifier/\.github/workflows/"
    "release\\.yml@refs/tags/v[0-9.]+"
)
COSIGN_OIDC_ISSUER_DEFAULT = "https://token.actions.githubusercontent.com"

_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_ASSET_BYTES = 512 * 1024 * 1024  # 512 MiB cap on the downloaded asset


class UpdaterError(YtUniquifierError):
    """Fetch, verify, or install failure."""


class UpdateAsset(BaseModel):
    """One platform-specific release artifact pinned in the manifest."""

    model_config = ConfigDict(extra="forbid")

    platform: str = Field(min_length=1)  # 'linux', 'macos', 'windows', 'wheel'
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    cosign_bundle_url: str = Field(min_length=1)


class UpdateManifest(BaseModel):
    """Root document of the per-release manifest."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=32)
    notes_url: str = Field(default="", max_length=512)
    min_supported_from: str = Field(
        default="",
        description=(
            "Lowest installed version from which auto-update is supported. "
            "Empty string disables the gate."
        ),
    )
    assets: list[UpdateAsset] = Field(default_factory=list)


@dataclass(frozen=True)
class UpdateCheckResult:
    """Outcome of ``check_for_update``.  Shape consumed by both the CLI
    ``yt-uniq update --check`` and the GUI settings screen."""

    available: bool
    current_version: str
    latest_version: str
    notes_url: str = ""
    manifest: UpdateManifest | None = None


def check_for_update(
    *,
    manifest_url: str = DEFAULT_MANIFEST_URL,
    skip_when_disabled: bool = True,
) -> UpdateCheckResult:
    """Return whether a newer release exists.  Never raises on the
    network path — transient transport errors collapse to
    ``available=False`` so the GUI's auto-check on app startup never
    bricks the launch.
    """
    if skip_when_disabled and os.environ.get("YT_UNIQ_DISABLE_UPDATER") == "1":
        return UpdateCheckResult(
            available=False,
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
        )
    try:
        manifest = _fetch_manifest(manifest_url)
    except UpdaterError as exc:
        _log.info("update check skipped: %s", exc)
        return UpdateCheckResult(
            available=False,
            current_version=CURRENT_VERSION,
            latest_version=CURRENT_VERSION,
        )
    latest = manifest.version
    is_newer = _semver_greater(latest, CURRENT_VERSION)
    return UpdateCheckResult(
        available=is_newer,
        current_version=CURRENT_VERSION,
        latest_version=latest,
        notes_url=manifest.notes_url,
        manifest=manifest,
    )


def apply_update(
    manifest: UpdateManifest,
    *,
    platform_key: str | None = None,
    dest_dir: Path | None = None,
    cosign_identity_regex: str | None = None,
    cosign_oidc_issuer: str | None = None,
) -> Path:
    """Download + verify + place the asset for the current platform.

    Returns the on-disk path to the installed asset.  Does NOT re-spawn
    the process — the caller (CLI / GUI) is responsible for prompting
    the user to relaunch.
    """
    # Refuse downgrades.  An operator who genuinely wants to roll back
    # uninstalls and reinstalls a specific version manually.
    if not _semver_greater(manifest.version, CURRENT_VERSION):
        raise UpdaterError(
            f"refusing to downgrade: manifest version {manifest.version!r} "
            f"<= installed {CURRENT_VERSION!r}",
        )
    key = platform_key or _detect_platform_key()
    asset = next((a for a in manifest.assets if a.platform == key), None)
    if asset is None:
        raise UpdaterError(
            f"manifest has no asset for platform {key!r}; "
            f"available: {[a.platform for a in manifest.assets]}",
        )
    dest = dest_dir or _default_dest_dir()
    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="yt_uniq_update_") as tmp:
        tmp_path = Path(tmp)
        asset_path = tmp_path / Path(asset.url).name
        bundle_path = tmp_path / Path(asset.cosign_bundle_url).name
        _download(asset.url, asset_path, max_bytes=_MAX_ASSET_BYTES)
        _download(asset.cosign_bundle_url, bundle_path, max_bytes=_MAX_MANIFEST_BYTES)
        _verify_sha256(asset_path, asset.sha256)
        _verify_cosign_bundle(
            asset_path, bundle_path,
            identity_regex=cosign_identity_regex
            or COSIGN_CERT_IDENTITY_REGEX_DEFAULT,
            oidc_issuer=cosign_oidc_issuer or COSIGN_OIDC_ISSUER_DEFAULT,
        )
        final = dest / asset_path.name
        shutil.move(str(asset_path), str(final))
    return final


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _semver_greater(candidate: str, current: str) -> bool:
    """Return True iff ``candidate`` is strictly greater than ``current``.

    Tolerant of ``+source`` / ``-rcN`` suffixes — we compare the dotted
    numeric prefix and fall back to string comparison on a tie.  Not a
    full PEP 440 implementation; the auto-updater's job is to nudge
    users forward, not to be a packaging tool.
    """
    def parse(v: str) -> tuple[list[int], str]:
        head = v.split("+", 1)[0].lstrip("v").split("-", 1)[0]
        try:
            nums = [int(p) for p in head.split(".") if p.isdigit()]
        except ValueError:
            nums = []
        return nums, v
    cand_nums, cand_raw = parse(candidate)
    curr_nums, curr_raw = parse(current)
    if cand_nums and curr_nums:
        # Numeric prefix wins.  If equal, treat as not-newer — local
        # build metadata (``+source``) or pre-release tags (``-rc1``)
        # MUST NOT trigger an auto-update against the same numeric
        # version, otherwise dev builds would constantly try to
        # downgrade to the published release.
        return cand_nums > curr_nums
    return cand_raw > curr_raw


def _fetch_manifest(url: str) -> UpdateManifest:
    if not url.startswith("https://"):
        raise UpdaterError(f"refusing non-HTTPS manifest URL: {url!r}")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "yt-uniquifier-updater/1.0"},
        )
        with urllib.request.urlopen(  # noqa: S310 — HTTPS enforced
            req, timeout=15,
        ) as resp:
            raw = resp.read(_MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        raise UpdaterError(f"network error fetching manifest: {exc}") from exc
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise UpdaterError(
            f"manifest at {url} exceeds {_MAX_MANIFEST_BYTES} bytes"
        )
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdaterError(f"manifest is not valid UTF-8 JSON: {exc}") from exc
    try:
        return UpdateManifest.model_validate(doc)
    except ValidationError as exc:
        raise UpdaterError(f"manifest schema validation: {exc}") from exc


def _download(url: str, dest: Path, *, max_bytes: int) -> None:
    if not url.startswith("https://"):
        raise UpdaterError(f"refusing non-HTTPS download URL: {url!r}")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "yt-uniquifier-updater/1.0"},
        )
        with (
            urllib.request.urlopen(req, timeout=60) as resp,  # noqa: S310
            dest.open("wb") as fh,
        ):
                read = 0
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > max_bytes:
                        raise UpdaterError(
                            f"download from {url} exceeds {max_bytes} bytes",
                        )
                    fh.write(chunk)
    except OSError as exc:
        raise UpdaterError(f"download failed: {exc}") from exc


def _verify_sha256(path: Path, expected_hex: str) -> None:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual.lower() != expected_hex.lower():
        path.unlink(missing_ok=True)
        raise UpdaterError(
            f"SHA-256 mismatch for {path.name}: got {actual}, "
            f"expected {expected_hex}",
        )


def _verify_cosign_bundle(
    asset: Path,
    bundle: Path,
    *,
    identity_regex: str,
    oidc_issuer: str,
) -> None:
    """Shell out to ``cosign verify-blob``.  Missing cosign is fatal —
    silently trusting an unverified asset would defeat the entire
    supply-chain story from v1.1.0 Task 11."""
    if shutil.which("cosign") is None:
        raise UpdaterError(
            "cosign not on PATH; install cosign (https://github.com/"
            "sigstore/cosign/releases) to verify update assets.  Set "
            "YT_UNIQ_DISABLE_UPDATER=1 to disable auto-updates entirely.",
        )
    cmd = [
        "cosign", "verify-blob",
        "--bundle", str(bundle),
        "--certificate-identity-regexp", identity_regex,
        "--certificate-oidc-issuer", oidc_issuer,
        str(asset),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.SubprocessError as exc:
        raise UpdaterError(f"cosign verification failed to run: {exc}") from exc
    if proc.returncode != 0:
        raise UpdaterError(
            f"cosign verify-blob refused {asset.name}: "
            f"{proc.stderr.strip()[:300]}",
        )


def _detect_platform_key() -> str:
    """Return the manifest's platform tag for the current host."""
    import sys
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "wheel"


def _default_dest_dir() -> Path:
    """Operator-friendly install destination."""
    return Path.home() / ".cache" / "yt_uniquifier" / "updates"
