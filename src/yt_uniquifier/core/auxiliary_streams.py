"""Internal policy model for non-A/V/S media streams.

``SourceMeta`` keeps these values in a private attribute so attachment/data
support can be added without changing its stable serialized API contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from yt_uniquifier.core.models import SourceMeta

AuxiliaryKind = Literal["attachment", "data", "attached_pic"]


@dataclass(frozen=True)
class AuxiliaryStream:
    index: int
    kind: AuxiliaryKind
    codec: str
    codec_tag: str
    filename: str | None = None
    mimetype: str | None = None
    language: str | None = None
    title: str | None = None
    timecode: str | None = None


def get_auxiliary_streams(source: SourceMeta) -> tuple[AuxiliaryStream, ...]:
    """Return internally-probed auxiliary streams, or none for manual models."""
    return source._auxiliary_streams


def set_auxiliary_streams(
    source: SourceMeta,
    streams: tuple[AuxiliaryStream, ...],
) -> None:
    """Attach probe-only topology without altering serialized SourceMeta."""
    source._auxiliary_streams = streams


def unsupported_auxiliary_streams(
    streams: tuple[AuxiliaryStream, ...] | list[AuxiliaryStream],
    output_container: str,
) -> tuple[AuxiliaryStream, ...]:
    """Return streams the selected muxer cannot preserve losslessly.

    Matroska supports attachment streams but not FFmpeg data streams. MOV
    supports its native ``tmcd`` timecode track. MP4 does not reliably accept
    a copied ``tmcd`` stream across supported FFmpeg versions, so it is rejected
    instead of being silently dropped or causing a late mux failure.
    """
    unsupported: list[AuxiliaryStream] = []
    for stream in streams:
        if stream.kind == "attachment":
            supported = output_container == "mkv"
        elif stream.kind == "data":
            supported = (
                output_container == "mov"
                and stream.codec_tag.lower() == "tmcd"
            )
        else:
            supported = (
                output_container == "mp4"
                and stream.codec.lower() in {"mjpeg", "png"}
            )
        if not supported:
            unsupported.append(stream)
    return tuple(unsupported)
