# Desktop installers

Build recipes + helper scripts for the per-OS desktop bundles
attached to each `v*` GitHub Release.

| OS      | Format             | Signed?       | Recipe                                                                |
|---------|--------------------|---------------|-----------------------------------------------------------------------|
| Linux   | `.AppImage`        | ✅ ready (1)  | `installers/linux/AppImageBuilder.yml`                                |
| macOS   | `.app.zip` (ditto) | ❌ unsigned   | `pyinstaller/yt-uniq-gui.spec` (Gatekeeper warns on first launch) (2) |
| Windows | `.zip` (Compress)  | ❌ unsigned   | `pyinstaller/yt-uniq-gui.spec` (SmartScreen warns on first launch) (2)|

(1) **AppImage does not require an X.509 code-signing cert** the way
    macOS / Windows binaries do. Integrity is verified via the
    `SHA256SUMS` file shipped on the same GitHub Release. Users who
    want auto-updates can pair the AppImage with
    [`AppImageUpdate`](https://github.com/AppImage/AppImageUpdate);
    the `update-information` stanza in the recipe wires that up.

(2) **macOS and Windows ship UNSIGNED in v1.0.0** because the
    project does not currently hold an Apple Developer ID (~$99/yr)
    or a Windows Code Signing certificate (~$200-400/yr). Users
    will see a Gatekeeper / SmartScreen warning the first time they
    launch — `docs/install.md` documents the per-OS bypass
    (right-click → Open on macOS, "More info → Run anyway" on
    Windows) and how to verify the download via the SHA256SUMS file
    instead. Signing will land as a v1.0.x patch release once the
    credentials are in place; the existing `.github/workflows/release.yml`
    already builds the artefacts that the signing step will sign in
    place.

## Local builds

### AppImage (Linux)

```bash
# inside a Linux host or container with Python 3.12 + apt-installed ffmpeg
pip install -e ".[dev,gui]"
pip install "pyinstaller>=6.6,<7" appimage-builder

# 1) staticffmpeg cache — same path the CI workflow uses.
mkdir -p .perf_cache/ffmpeg-static
curl -sSL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
  | tar -xJ --strip-components=1 -C .perf_cache/ffmpeg-static

# 2) PyInstaller bundle.
python -m PyInstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm

# 3) AppImage.
YT_UNIQUIFIER_VERSION=1.0.0 \
  appimage-builder --recipe installers/linux/AppImageBuilder.yml
```

### macOS / Windows bundles

The PyInstaller spec is the same for all three OSes; the release
workflow just runs it on each runner and archives the result. To
reproduce locally:

```bash
pip install -e ".[dev,gui]" && pip install "pyinstaller>=6.6,<7"
python -m PyInstaller pyinstaller/yt-uniq-gui.spec --clean --noconfirm
# macOS  → dist/yt-uniq-gui.app
# Linux  → dist/yt-uniq-gui/
# Windows→ dist/yt-uniq-gui/yt-uniq-gui.exe
```

## Future signing work (deferred to v1.0.x)

These secrets need to be added to repo Settings → Secrets and
variables → Actions before the relevant step in
`.github/workflows/release.yml` can be re-enabled:

| Secret                  | Purpose                                                   |
|-------------------------|-----------------------------------------------------------|
| `APPLE_TEAM_ID`         | Apple Developer Team ID for codesign + notarytool         |
| `APPLE_DEV_ID_CERT_P12` | Base64-encoded Developer ID Application .p12              |
| `APPLE_DEV_ID_CERT_PW`  | Passphrase for the .p12 above                             |
| `APPLE_NOTARY_API_KEY`  | Base64-encoded App Store Connect API key (`.p8`)          |
| `APPLE_NOTARY_KEY_ID`   | Key ID for the API key                                    |
| `APPLE_NOTARY_ISSUER`   | Issuer UUID for the API key                               |
| `WIN_CODESIGN_PFX`      | Base64-encoded Windows code-signing cert (`.pfx`)         |
| `WIN_CODESIGN_PW`       | Passphrase for the .pfx above                             |
| `WIN_CODESIGN_TS_URL`   | RFC 3161 timestamp URL (default `http://timestamp.digicert.com`) |
