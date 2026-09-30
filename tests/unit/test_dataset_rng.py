"""Random draws that are the same on every platform and Python version."""

import pytest

from opensumma.datasets.rng import Rng


def test_a_seed_and_stream_always_give_the_same_draws() -> None:
    # Pinned: a change here means every generated dataset changes.
    rng = Rng(42, "test")
    assert [rng.integer(1, 1000) for _ in range(5)] == [598, 738, 872, 449, 13]
    assert rng.skewed(0, 999) == 505
    assert rng.choice("abcdef") == "f"
    assert rng.weighted("xyz", (1, 2, 3)) == "z"
    assert rng.sample(range(10), 4) == [8, 7, 9, 5]
    assert rng.shuffled("abcde") == ["a", "d", "c", "e", "b"]


def test_streams_are_independent() -> None:
    alone = Rng(42, "sales")
    expected = [alone.integer(0, 10**6) for _ in range(3)]

    other = Rng(42, "bills")
    [other.integer(0, 10**6) for _ in range(100)]
    again = Rng(42, "sales")
    assert [again.integer(0, 10**6) for _ in range(3)] == expected
    assert [Rng(43, "sales").integer(0, 10**6) for _ in range(3)] != expected


def test_draws_stay_in_range_and_cover_it() -> None:
    rng = Rng(1, "range")
    integers = {rng.integer(3, 7) for _ in range(500)}
    assert integers == {3, 4, 5, 6, 7}
    skewed = [rng.skewed(0, 99) for _ in range(2000)]
    assert min(skewed) >= 0 and max(skewed) <= 99
    assert sum(value < 50 for value in skewed) > sum(value >= 50 for value in skewed)
    assert rng.integer(5, 5) == 5
    with pytest.raises(ValueError):
        rng.integer(6, 5)


def test_choices_respect_weights_and_samples_are_distinct() -> None:
    rng = Rng(2, "choices")
    assert {rng.weighted("ab", (0, 1)) for _ in range(50)} == {"b"}
    sample = rng.sample(range(20), 20)
    assert sorted(sample) == list(range(20))
    assert sorted(rng.shuffled("hello")) == sorted("hello")
    with pytest.raises(ValueError):
        rng.sample(range(3), 4)
    with pytest.raises(ValueError):
        rng.choice([])
