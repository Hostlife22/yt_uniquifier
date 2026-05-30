#!/usr/bin/env bash
# Manual exploratory GUI smoke: launch yt-uniq-gui with a generated tiny
# sample input so the operator can walk through docs/manual_gui_checklist.md
# without having to source their own clip.
#
# Usage: scripts/manual_gui_smoke.sh [path/to/existing.mp4]
#
# If no input is provided, a 5-second testsrc2 clip is generated in a
# temp dir and shown next to the launched GUI window.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/yt-uniq-gui ]]; then
    echo "yt-uniq-gui not installed. Run: make dev" >&2
    exit 1
fi

INPUT="${1:-}"
if [[ -z "$INPUT" ]]; then
    TMP_DIR="$(mktemp -d -t ytu-manual.XXXXXX)"
    INPUT="$TMP_DIR/sample.mp4"
    echo "Generating sample clip at $INPUT ..."
    ffmpeg -hide_banner -loglevel error -y \
        -f lavfi -i "testsrc2=size=640x360:rate=30:duration=5" \
        -f lavfi -i "sine=frequency=440:duration=5" \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
        -c:a aac -shortest "$INPUT"
fi

echo
echo "==================== Manual GUI smoke ===================="
echo "Sample input: $INPUT"
echo "Walk through docs/manual_gui_checklist.md."
echo "Press Ctrl+C in this terminal to close logs when done."
echo "=========================================================="
echo

exec .venv/bin/yt-uniq-gui
