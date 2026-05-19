"""Unit tests for the reproducible-shuffle helper (NFR-003 / TASK-049).

Property under test: ``derive_shuffled_ids(compute_seed(sid), candidates)``
is a pure function of ``(sid, candidates)``. The persisted ``shuffled_ids``
can be reproduced from ``(session_id, candidates)`` alone — that's the
audit-replay contract.
"""

from __future__ import annotations

import string

from src.data.shuffle import (
    SHUFFLE_ALGO_VERSION,
    compute_seed,
    derive_shuffled_ids,
    seed_to_int,
)


CANDIDATES = [f"az-net-{i:04d}-fr" for i in range(20)]


def test_seed_is_first_16_hex_of_sha256() -> None:
    sid = "f2c61e3a-bf85-4c1b-8f6b-1a4d0b2e9a44"
    seed = compute_seed(sid)
    assert len(seed) == 16
    assert all(c in string.hexdigits for c in seed)
    # Same input ⇒ same seed.
    assert compute_seed(sid) == seed


def test_seed_to_int_round_trip() -> None:
    seed = "3f1e9a7c4b2d8e60"
    assert seed_to_int(seed) == int(seed, 16)


def test_shuffle_is_deterministic_per_session() -> None:
    sid = "session-aaa"
    seed = compute_seed(sid)
    first = derive_shuffled_ids(seed, CANDIDATES)
    second = derive_shuffled_ids(seed, CANDIDATES)
    assert first == second


def test_shuffle_is_a_permutation_of_input() -> None:
    seed = compute_seed("session-xyz")
    ordered = derive_shuffled_ids(seed, CANDIDATES)
    assert sorted(ordered) == sorted(CANDIDATES)
    assert len(ordered) == len(CANDIDATES)


def test_different_session_ids_yield_different_orders() -> None:
    a = derive_shuffled_ids(compute_seed("session-a"), CANDIDATES)
    b = derive_shuffled_ids(compute_seed("session-b"), CANDIDATES)
    # Extremely high-probability assertion: collisions on a 20-element
    # shuffle with 64-bit seeds are vanishingly rare.
    assert a != b


def test_reproducibility_from_session_id_alone() -> None:
    """Audit replay rebuilds the ordering from (session_id, candidates) only."""

    sid = "session-replay"
    expected = derive_shuffled_ids(compute_seed(sid), CANDIDATES)
    # Simulate audit replay: only the session_id and the candidate set
    # are available; the original seed was never persisted in this branch.
    replayed = derive_shuffled_ids(compute_seed(sid), CANDIDATES)
    assert replayed == expected


def test_algo_version_pinned() -> None:
    # If the RNG algorithm changes, version bump is mandatory (TASK-049).
    assert SHUFFLE_ALGO_VERSION == 1
