#!/usr/bin/env bash
# v1.1.0 Task 8: ad-hoc codesign for the PyInstaller .app bundle.
#
# Why: full Apple notarization needs the $99/yr Developer Program and
# is intentionally out of scope (see specs/v1.0.1-to-v1.3-roadmap.plan.md
# § v1.1.0 Task 8). An ad-hoc signature (`codesign --sign -`) does NOT
# pass Gatekeeper on its own — the user still has to right-click → Open
# the first time — but it stabilises the bundle across macOS updates so
# subsequent versions don't trip the "app is damaged and can't be
# opened" error that hits *unsigned* bundles after a few OS releases.
#
# Idempotent. Verifies its own signature after applying it so a release
# job that silently leaves an unsigned bundle is loud at build time
# rather than at user-install time.
#
# Usage: installers/macos/codesign-adhoc.sh dist/yt-uniq-gui.app

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <path/to/yt-uniq-gui.app>" >&2
  exit 2
fi

APP_PATH="$1"

if [ ! -d "$APP_PATH" ]; then
  echo "error: $APP_PATH is not a directory (expected a .app bundle)" >&2
  exit 1
fi

if [ "$(uname)" != "Darwin" ]; then
  echo "warning: ad-hoc codesign is a no-op on $(uname); skipping" >&2
  exit 0
fi

if ! command -v codesign >/dev/null 2>&1; then
  echo "error: codesign not on PATH — Xcode CLI tools not installed?" >&2
  exit 1
fi

echo "[codesign-adhoc] signing $APP_PATH with anonymous identity (-)…"
# --deep walks every embedded framework / dylib so QtCore.framework et
# al. all carry the same ad-hoc signature. --force overwrites any prior
# signature so this script is idempotent.
codesign --deep --force --sign - "$APP_PATH"

echo "[codesign-adhoc] verifying signature…"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

# spctl --assess will FAIL because we have no notarization; that is
# expected. We only want to confirm codesign itself is happy.
echo "[codesign-adhoc] done. Note: spctl will still reject this bundle"
echo "  on first launch — users follow the right-click → Open bypass"
echo "  in docs/install.md."
