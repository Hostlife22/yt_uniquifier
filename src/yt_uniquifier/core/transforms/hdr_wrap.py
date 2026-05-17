"""zscale-based HDR transfer roundtrip wrap for color-domain transforms.

Color transforms (eq, noise, blend) operate in the pixel-value domain. For SDR
this is fine — values are roughly linear with light. For HDR (PQ/HLG) pixel
values are highly nonlinear (PQ is perceptual-quantizer log curve, HLG is
hybrid log-gamma). Running `eq=brightness=0.01` directly on a PQ stream
breaks the curve: 0.01 in PQ-units is a huge brightness shift at the bottom
of the range and invisible near the top.

The fix: convert to a linear-light domain via `zscale=transfer=linear`,
apply the ops, convert back via `zscale=transfer=<original>`. Requires ffmpeg
built with zimg (`--enable-libzimg`).

`needs_linear_wrap()` answers the predicate; `wrap_linear()` emits the
filter-string fragment.
"""

from __future__ import annotations

from collections.abc import Iterable

from yt_uniquifier.core.models import ColorTransfer, HDRInfo

# Color transforms whose math is meaningful only in linear light. Geometry
# (crop/scale/rotate/speed) and noise-at-pixel-level are fine in any domain.
HDR_TRANSFERS: frozenset[ColorTransfer] = frozenset({"smpte2084", "arib-std-b67"})


def needs_linear_wrap(color: HDRInfo) -> bool:
    """True iff color-domain ops over this stream need a zscale linear trip."""
    return color.is_hdr and color.transfer in HDR_TRANSFERS


def npl_for(color: HDRInfo) -> int:
    """Nominal peak luminance (cd/m²) for zscale's npl= argument.

    100 matches `npl=100` which is what zscale expects to convert PQ-encoded
    grades (mastered against a 1000 nit display) into a normalized linear
    domain. 1000 would clip highlights — 100 is the safe default that
    matches zimg recommendations.
    """
    return 100


def _transfer_for_zscale(transfer: ColorTransfer) -> str:
    """Map our enum to zscale's `t=` accepted value."""
    return {
        "smpte2084": "smpte2084",
        "arib-std-b67": "arib-std-b67",
        "bt709": "bt709",
        "bt470bg": "bt470bg",
        "smpte170m": "smpte170m",
        "iec61966-2-1": "iec61966-2-1",
    }.get(transfer, "bt709")


def wrap_linear(inner_filters: list[str], color: HDRInfo) -> str:
    """Wrap a list of inner filter strings with a zscale linear roundtrip.

    Returns a single comma-joined filter expression with no input/output
    labels — pipeline composes the labels.

    For SDR / non-HDR sources this is a no-op (returns inner joined as-is).
    """
    inner_joined = ",".join(f for f in inner_filters if f)
    if not needs_linear_wrap(color) or not inner_joined:
        return inner_joined

    target_transfer = _transfer_for_zscale(color.transfer)
    npl = npl_for(color)
    return ",".join(
        [
            f"zscale=transfer=linear:npl={npl}",
            inner_joined,
            f"zscale=transfer={target_transfer}:npl={npl}",
        ]
    )


def is_tonemap_active(transforms: Iterable[object] | None) -> bool:
    """True if any enabled transform converts HDR to SDR via tonemap.

    Loose typing because importing TransformConfig here would create a
    cycle (models has imports that reach back into transforms).
    """
    if transforms is None:
        return False
    return any(
        getattr(tc, "enabled", True) and getattr(tc, "id", "") == "video.tonemap_sdr"
        for tc in transforms
    )


def is_color_transform(transform_id: str) -> bool:
    """True if this transform's math is in the value domain (needs wrap for HDR).

    Note: `video.blend_b` is conceptually a color op, but its filter_str
    contains `;` and multiple internal labels (scale2ref + blend), so it
    can't be folded into a single comma-joined wrap. Preflight warns when
    blend_b is combined with HDR keep — caller is on their own there.
    """
    return transform_id in {"video.color_eq", "video.noise"}
