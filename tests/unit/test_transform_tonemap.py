"""Snapshot tests for video.tonemap_sdr."""

from __future__ import annotations

import pytest

from yt_uniquifier.core.transforms import get
from yt_uniquifier.core.transforms.base import LabelAllocator, call_build
from yt_uniquifier.core.transforms.video_tonemap import TonemapSDRParams


@pytest.mark.parametrize("alg", ["hable", "reinhard", "mobius", "aces"])
def test_each_algorithm_renders(alg: str) -> None:
    spec = get("video.tonemap_sdr")
    chain = call_build(
        spec, TonemapSDRParams(algorithm=alg),  # type: ignore[arg-type]
        LabelAllocator(), "0:v:0",
    )
    assert f"tonemap={alg}" in chain.filter_str


def test_chain_shape_default() -> None:
    spec = get("video.tonemap_sdr")
    chain = call_build(spec, TonemapSDRParams(), LabelAllocator(), "0:v:0")
    # zscale linearise → tonemap → zscale BT.709 → 8-bit
    assert chain.filter_str.startswith("zscale=t=linear:npl=1000")
    assert "tonemap=hable" in chain.filter_str
    assert "zscale=t=bt709:m=bt709:p=bt709:r=tv" in chain.filter_str
    assert chain.filter_str.endswith("format=yuv420p")


def test_peak_param_affects_npl_and_tm_peak() -> None:
    spec = get("video.tonemap_sdr")
    chain = call_build(
        spec, TonemapSDRParams(peak=4000.0),
        LabelAllocator(), "0:v:0",
    )
    assert "npl=4000" in chain.filter_str
    # tonemap peak = peak / 100 = 40.0
    assert "peak=40.0000" in chain.filter_str


def test_desat_param_propagates() -> None:
    spec = get("video.tonemap_sdr")
    chain = call_build(
        spec, TonemapSDRParams(desat=0.5),
        LabelAllocator(), "0:v:0",
    )
    assert "desat=0.5000" in chain.filter_str


def test_bounds_enforced_by_pydantic() -> None:
    with pytest.raises(Exception):  # noqa: B017
        TonemapSDRParams(peak=99.0)        # below floor 100
    with pytest.raises(Exception):  # noqa: B017
        TonemapSDRParams(desat=1.5)        # above ceil 1.0
    with pytest.raises(Exception):  # noqa: B017
        TonemapSDRParams(algorithm="custom")  # type: ignore[arg-type]
