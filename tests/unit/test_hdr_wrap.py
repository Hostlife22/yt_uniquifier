"""Unit tests for HDR linear roundtrip wrap."""

from __future__ import annotations

from yt_uniquifier.core.models import HDRInfo
from yt_uniquifier.core.transforms.hdr_wrap import (
    is_color_transform,
    needs_linear_wrap,
    npl_for,
    wrap_linear,
)


def _hdr(transfer: str = "smpte2084") -> HDRInfo:
    return HDRInfo(
        is_hdr=True, transfer=transfer, primaries="bt2020",
        space="bt2020nc", bit_depth=10,
    )


def _sdr() -> HDRInfo:
    return HDRInfo(
        is_hdr=False, transfer="bt709", primaries="bt709",
        space="bt709", bit_depth=8,
    )


# ---- needs_linear_wrap -----------------------------------------------------

def test_needs_wrap_pq() -> None:
    assert needs_linear_wrap(_hdr("smpte2084")) is True


def test_needs_wrap_hlg() -> None:
    assert needs_linear_wrap(_hdr("arib-std-b67")) is True


def test_no_wrap_sdr() -> None:
    assert needs_linear_wrap(_sdr()) is False


def test_no_wrap_bt709_marked_hdr() -> None:
    """An is_hdr=True flag with bt709 transfer is a contradiction; trust transfer."""
    color = HDRInfo(is_hdr=True, transfer="bt709", primaries="bt709",
                    space="bt709", bit_depth=10)
    assert needs_linear_wrap(color) is False


# ---- wrap_linear -----------------------------------------------------------

def test_wrap_pq_inserts_zscale_roundtrip() -> None:
    out = wrap_linear(["eq=brightness=0.01", "noise=alls=3:allf=t+u"], _hdr("smpte2084"))
    assert out.startswith(
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,zscale=transfer=linear:npl=100,"
    )
    assert out.endswith(",zscale=transfer=smpte2084:npl=100")
    assert "eq=brightness=0.01" in out
    assert "noise=alls=3:allf=t+u" in out


def test_wrap_prefixes_even_dim_guard() -> None:
    """zscale on yuv420p10le rejects odd dims (chroma subsampling 4:2:0).

    Regression: 2026-05-31 — medium_hdr × HDR10 × libx265 crashed at
    segment 0 with "code 1027: image dimensions must be divisible by
    subsampling factor" because video.crop_resize produced odd dims
    that reached zscale before the chain tail's even-dim guard fired.
    wrap_linear must inject `scale=trunc(iw/2)*2:trunc(ih/2)*2` as the
    first element of the wrapped chain so zscale always sees even dims.
    """
    out = wrap_linear(["eq=contrast=1.02"], _hdr("smpte2084"))
    # Even-dim guard must precede zscale's linear-light entry.
    even_guard_idx = out.find("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    zscale_linear_idx = out.find("zscale=transfer=linear")
    assert even_guard_idx == 0
    assert even_guard_idx < zscale_linear_idx


def test_wrap_hlg_returns_to_hlg() -> None:
    out = wrap_linear(["eq=contrast=1.02"], _hdr("arib-std-b67"))
    assert out.endswith(",zscale=transfer=arib-std-b67:npl=100")


def test_wrap_sdr_is_identity() -> None:
    out = wrap_linear(["eq=brightness=0.01", "noise=alls=3:allf=t+u"], _sdr())
    assert "zscale" not in out
    assert out == "eq=brightness=0.01,noise=alls=3:allf=t+u"


def test_wrap_empty_returns_empty() -> None:
    assert wrap_linear([], _hdr()) == ""
    assert wrap_linear([""], _hdr()) == ""


def test_npl_default_is_100() -> None:
    assert npl_for(_hdr()) == 100
    assert npl_for(_sdr()) == 100


# ---- is_color_transform ----------------------------------------------------

def test_is_color_transform_classification() -> None:
    assert is_color_transform("video.color_eq") is True
    assert is_color_transform("video.noise") is True
    # blend_b explicitly excluded — see hdr_wrap.py docstring.
    assert is_color_transform("video.blend_b") is False
    assert is_color_transform("video.crop_resize") is False
    assert is_color_transform("video.rotate") is False
    assert is_color_transform("video.speed") is False
    assert is_color_transform("audio.eq") is False
