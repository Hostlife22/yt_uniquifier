"""zscale-based HDR transfer roundtrip wrap for color-domain transforms.

Color transforms (eq, noise, blend) operate in the pixel-value domain. For SDR
this is fine — values are roughly linear with light. For HDR (PQ/HLG) pixel
values are highly nonlinear (PQ is perceptual-quantizer log curve, HLG is
hybrid log-gamma). Running `eq=brightness=0.01` directly on a PQ stream
breaks the curve: 0.01 in PQ-units is a huge brightness shift at the bottom
of the range and invisible near the top.

The fix: convert through planar float RGB into a linear-light domain via
`zscale=transfer=linear:matrix=gbr`, apply the ops, then explicitly convert
back to the original HDR transfer/matrix and 10-bit YUV. Requires ffmpeg built
with zimg (`--enable-libzimg`).

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
    # Pre-zscale even-dim guard: zscale rejects odd dimensions on
    # yuv420p10le (chroma subsampling = 4:2:0 → both axes must be even)
    # with "code 1027: image dimensions must be divisible by subsampling
    # factor". Geometric transforms upstream (video.crop_resize at any
    # max_strength > 0, video.rotate at non-multiple-of-90 angles) can
    # produce odd dims that the final scale tail catches for the encoder
    # but reaches zscale uncorrected. Found 2026-05-31 on
    # medium_hdr × synth_hdr10 × libx265 once a real zimg ffmpeg
    # became available.
    # Transfer-only conversion in a subsampled YUV matrix is not a valid
    # linear-light working space: neutral/highlight chroma can become strongly
    # green/orange after the return trip. Convert through planar float RGB,
    # apply the value-domain filters there, then explicitly return to the
    # source HDR matrix and the encoder's 10-bit 4:2:0 contract.
    target_matrix = color.space if color.space in {"bt2020nc", "bt2020c"} else "bt2020nc"
    return ",".join(
        [
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            f"zscale=transfer=linear:matrix=gbr:npl={npl}",
            "format=gbrpf32le",
            inner_joined,
            # Older FFmpeg releases negotiate ``eq`` through planar YUV even
            # when its input is float RGB.  Convert the complete inner stack
            # back to the declared RGB working domain before zscale consumes
            # it; otherwise libzimg 3.0.x sees YUV carrying an RGB matrix and
            # aborts with "no path between colorspaces".
            "format=gbrpf32le",
            (
                f"zscale=transfer={target_transfer}:matrix={target_matrix}:"
                f"npl={npl}"
            ),
            "format=yuv420p10le",
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
