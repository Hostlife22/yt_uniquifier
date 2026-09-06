from pathlib import Path

from tools.benchmark import _DiskUsageSampler


def test_disk_sampler_deduplicates_nested_roots_and_ignores_symlinks(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    data = work / "segment"
    data.write_bytes(b"abc")
    (tmp_path / "alias").symlink_to(data)
    sampler = _DiskUsageSampler([tmp_path, work, tmp_path])
    sampler._sample()
    assert sampler.peak_bytes == 3
    data.unlink()
    sampler._sample()
    assert sampler.peak_bytes == 3


def test_disk_sampler_tracks_growth_and_stops(tmp_path: Path) -> None:
    sampler = _DiskUsageSampler([tmp_path], interval_sec=0.01)
    sampler.start()
    (tmp_path / "out").write_bytes(b"output")
    sampler.stop()
    assert sampler.peak_bytes == 6
    assert not sampler._thread.is_alive()
