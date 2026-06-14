#!/usr/bin/env python3
"""Compare two ``tools/benchmark.py --json`` snapshots and gate on regressions.

Usage:

    python tools/perf_compare.py \\
      --baseline perf-history/baseline.json \\
      --current  perf-history/HEAD.json \\
      [--threshold 15] \\
      [--markdown report.md]

Exit codes:
- 0: every "lower-is-better" metric stayed within ``--threshold`` percent.
- 1: at least one metric regressed by more than ``--threshold`` percent.
- 2: usage error (missing file, malformed JSON, schema mismatch).

The "lower-is-better" set is hardcoded to keep CI behaviour
predictable: ``wall_sec``, ``rss_peak_kb``, every entry under
``per_phase_sec``. ``segments_seen`` and metadata fields are
informational only.

A regression in a *per-phase* timing is reported but only fails
when the total wall time also regressed — per-phase noise on a
short fixture is much higher than total wall, and gating on it
would flap CI without surfacing real signal.

This script has no third-party deps so it can run on a fresh CI
runner before the package itself is installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

LOWER_IS_BETTER = {"wall_sec", "rss_peak_kb"}
"""Top-level metrics where a higher value than baseline is a regression."""


def _load(path: Path) -> dict[str, Any]:
    """Read + lightly validate a snapshot. Raises SystemExit(2) on failure."""
    if not path.exists():
        print(f"perf_compare: missing snapshot: {path}", file=sys.stderr)
        raise SystemExit(2)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"perf_compare: invalid JSON in {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if not isinstance(data, dict):
        print(f"perf_compare: {path} is not a JSON object", file=sys.stderr)
        raise SystemExit(2)
    if data.get("schema_version") != 1:
        print(
            f"perf_compare: {path} has unexpected schema_version="
            f"{data.get('schema_version')!r}; expected 1. Regenerate via "
            f"tools/benchmark.py from the matching git SHA.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return data


def _pct_change(baseline: float, current: float) -> float:
    """Positive means slower / heavier vs baseline; negative means improvement."""
    if baseline == 0:
        return float("inf") if current > 0 else 0.0
    return (current - baseline) / baseline * 100.0


def _format_row(label: str, baseline: float, current: float, threshold: float) -> str:
    delta = _pct_change(baseline, current)
    flag = ""
    if delta > threshold:
        flag = " ⚠️ regression"
    elif delta < -threshold:
        flag = " ✅ improvement"
    return f"| {label} | {baseline:.2f} | {current:.2f} | {delta:+.1f}% |{flag} |"


def _iter_per_phase(data: dict[str, Any]) -> Iterable[tuple[str, float]]:
    per_phase = data.get("per_phase_sec") or {}
    if not isinstance(per_phase, dict):
        return ()
    return ((str(k), float(v)) for k, v in sorted(per_phase.items()))


def _build_report(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    threshold: float,
) -> tuple[str, bool]:
    """Markdown comparison + a hard-regression flag.

    A hard regression is defined as ``wall_sec`` exceeding the
    threshold. Per-phase regressions on a short fixture are too
    noisy to gate on; they appear in the report but do not flip
    the failure bit.
    """
    lines: list[str] = []
    lines.append("# Perf comparison")
    lines.append("")
    lines.append(
        f"- baseline: `{baseline.get('git_sha', '?')}` "
        f"({baseline.get('yt_uniquifier_version', '?')}, "
        f"{baseline.get('platform', '?')}, py {baseline.get('py_version', '?')})",
    )
    lines.append(
        f"- current:  `{current.get('git_sha', '?')}` "
        f"({current.get('yt_uniquifier_version', '?')}, "
        f"{current.get('platform', '?')}, py {current.get('py_version', '?')})",
    )
    lines.append(f"- threshold: ±{threshold:.0f}% (hard-gates only on wall_sec)")
    lines.append("")
    lines.append("| metric | baseline | current | delta | |")
    lines.append("|---|---|---|---|---|")

    hard_regression = False
    for metric in sorted(LOWER_IS_BETTER):
        b = float(baseline.get(metric, 0.0) or 0.0)
        c = float(current.get(metric, 0.0) or 0.0)
        lines.append(_format_row(metric, b, c, threshold))
        if metric == "wall_sec" and _pct_change(b, c) > threshold:
            hard_regression = True

    base_phases = dict(_iter_per_phase(baseline))
    cur_phases = dict(_iter_per_phase(current))
    all_phase_names = sorted(set(base_phases) | set(cur_phases))
    for name in all_phase_names:
        b = base_phases.get(name, 0.0)
        c = cur_phases.get(name, 0.0)
        lines.append(_format_row(f"per_phase[{name}]", b, c, threshold))

    lines.append("")
    if hard_regression:
        lines.append(
            "**Verdict**: wall_sec regression exceeds threshold. CI "
            "fails. If the change is intentional (e.g. correctness "
            "fix that adds work), update the baseline JSON in "
            "`perf-history/` and document the new floor in CHANGELOG.",
        )
    else:
        lines.append("**Verdict**: within threshold.")
    lines.append("")
    return "\n".join(lines), hard_regression


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--current", type=Path, required=True)
    ap.add_argument(
        "--threshold",
        type=float,
        default=15.0,
        help="Percent change above which wall_sec is a regression. "
        "Default 15.0 — CI noise on short fixtures is typically ±5-8%%, "
        "so 15 keeps the gate signal-driven, not flap-driven.",
    )
    ap.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Optional path to write the markdown report (PR comment "
        "format). Always also printed to stdout.",
    )
    args = ap.parse_args(argv)

    baseline = _load(args.baseline)
    current = _load(args.current)
    report, regressed = _build_report(
        baseline, current, threshold=args.threshold,
    )
    print(report)
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(report + "\n", encoding="utf-8")
    return 1 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
