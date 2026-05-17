from yt_uniquifier.core.transforms.base import LabelAllocator


def test_independent_counters() -> None:
    a = LabelAllocator()
    assert a.next("v") == "v1"
    assert a.next("v") == "v2"
    assert a.next("a") == "a1"
    assert a.next("a") == "a2"
    assert a.next("v") == "v3"


def test_fresh_allocator_starts_at_one() -> None:
    a1 = LabelAllocator()
    a2 = LabelAllocator()
    assert a1.next("v") == a2.next("v") == "v1"
