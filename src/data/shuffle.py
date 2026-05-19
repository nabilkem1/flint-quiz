"""Reproducible-shuffle helper for `start_quiz` (TASK-049, NFR-003).

Contract:

    seed = compute_seed(session_id)              # 16 hex chars (first 16 of sha256)
    shuffled_ids = derive_shuffled_ids(seed, candidates)

Both `seed` and `shuffled_ids` are persisted on the `SessionDoc` so the
session is fully reconstructible from `(session_id, seed, candidate_ids)`
— audit replay must reproduce the persisted ordering byte-for-byte.

If the RNG algorithm ever changes, bump `SHUFFLE_ALGO_VERSION` and persist
the version on the session row. Silently changing the algorithm would break
reproducibility for sessions already in flight; that is a P1 contract
violation.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence

# Version label persisted alongside `seed` if/when the algorithm changes.
# Stays at 1 for v1. Changing the implementation MUST bump this and persist
# `shuffle_algo_version` on the session row so old sessions remain replayable.
SHUFFLE_ALGO_VERSION: int = 1

_SEED_HEX_LEN: int = 16  # 64 bits — plenty of entropy for ordering, short to store


def compute_seed(session_id: str) -> str:
    """Return the first 16 hex chars of `sha256(session_id)`.

    The seed is fully determined by `session_id` — no server nonce is mixed
    in. Reproducibility from `session_id` alone is required for audit replay
    (008-api §1.5.7).
    """

    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return digest[:_SEED_HEX_LEN]


def seed_to_int(seed: str) -> int:
    """Parse the 16-hex-char seed into the integer used to seed `random.Random`."""

    if len(seed) < _SEED_HEX_LEN:
        raise ValueError(f"seed must be at least {_SEED_HEX_LEN} hex chars (got {len(seed)})")
    return int(seed[:_SEED_HEX_LEN], 16)


def derive_shuffled_ids(seed: str, candidates: Sequence[str]) -> list[str]:
    """Derive the deterministic shuffled order from `seed`.

    Uses `random.Random(seed_int).shuffle` over a copy of `candidates`. The
    Python `random` module's shuffle is documented to be deterministic for a
    given seed across releases; if that ever changes upstream, the
    `SHUFFLE_ALGO_VERSION` bump path applies.
    """

    rng = random.Random(seed_to_int(seed))
    ordered = list(candidates)
    rng.shuffle(ordered)
    return ordered


__all__ = [
    "SHUFFLE_ALGO_VERSION",
    "compute_seed",
    "derive_shuffled_ids",
    "seed_to_int",
]
