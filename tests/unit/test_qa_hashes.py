from __future__ import annotations

import hashlib
import os
from pathlib import Path

from yt_uniquifier.core.qa.hashes import md5_file


def test_md5_matches_hashlib(tmp_path: Path) -> None:
    data = os.urandom(10 * 1024 * 1024 + 17)  # not a chunk boundary
    p = tmp_path / "blob.bin"
    p.write_bytes(data)
    assert md5_file(p) == hashlib.md5(data).hexdigest()  # noqa: S324


def test_md5_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.bin"
    p.touch()
    assert md5_file(p) == "d41d8cd98f00b204e9800998ecf8427e"


def test_md5_streaming_chunks_same_result(tmp_path: Path) -> None:
    data = b"x" * 200_000
    p = tmp_path / "x.bin"
    p.write_bytes(data)
    a = md5_file(p, chunk_size=1024)
    b = md5_file(p, chunk_size=4 * 1024 * 1024)
    assert a == b
