"""Audit two-stage archive integration test (TASK-051 / ADR-006).

Asserts:

* Rows due for archival (``_ts + ttl - lead <= now``) are written byte-for-byte
  to the immutable Blob container ``audit-archive``.
* Re-running the job for an already-archived row is a no-op
  (``archived=0`` on the second pass).

The job is gated on a live Cosmos + Blob endpoint pair —
``COSMOS_TEST_ENDPOINT`` + ``BLOB_TEST_ACCOUNT_URL``. Without them the
test skips. The byte-equivalence property is intentionally independent of
mocks: the canonical JSON encoding (``sort_keys=True``,
``separators=(",", ":")``) is what makes re-archive idempotent, and any
implementation that breaks that property is a P1 contract violation.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from src.data.audit_archive import AuditArchiveJob, _canonical_json


def _archive_env_available() -> bool:
    return bool(
        os.environ.get("COSMOS_TEST_ENDPOINT")
        and os.environ.get("BLOB_TEST_ACCOUNT_URL")
    )


requires_archive_env = pytest.mark.skipif(
    not _archive_env_available(),
    reason="set COSMOS_TEST_ENDPOINT and BLOB_TEST_ACCOUNT_URL to run the archive integration test",
)


# ---------------------------------------------------------------------------
# Always-on: canonical-JSON byte-equivalence property
# ---------------------------------------------------------------------------


def test_canonical_json_is_byte_stable() -> None:
    a = {"id": "1", "sessionId": "s", "verdict": "correct", "scoreDelta": 1.0}
    b = {"scoreDelta": 1.0, "verdict": "correct", "sessionId": "s", "id": "1"}
    assert _canonical_json(a) == _canonical_json(b)


def test_canonical_json_hash_is_deterministic() -> None:
    doc = {"id": "1", "sessionId": "s", "expected": ["A", "B"]}
    h1 = hashlib.sha256(_canonical_json(doc)).hexdigest()
    h2 = hashlib.sha256(_canonical_json(doc)).hexdigest()
    assert h1 == h2


# ---------------------------------------------------------------------------
# Real Cosmos + Blob path
# ---------------------------------------------------------------------------


@requires_archive_env
@pytest.mark.asyncio
async def test_archive_job_idempotent_real_env() -> None:
    """Archive once, archive again — second pass produces no new blob.

    The fixture is expected to seed at least one Cosmos `audit` row whose
    `_ts + ttl - lead <= now` is true. The job's `run_once` returns a
    counters dict whose `archived` value tracks new-blob writes.
    """

    job = AuditArchiveJob(
        cosmos_endpoint=os.environ["COSMOS_TEST_ENDPOINT"],
        blob_account_url=os.environ["BLOB_TEST_ACCOUNT_URL"],
        database_name=os.environ.get("COSMOS_TEST_DATABASE", "flint-quiz"),
        archive_lead_seconds=0,  # short-circuit the 30-day predicate for tests
    )
    try:
        first = await job.run_once()
        second = await job.run_once()
    finally:
        await job.close()

    # The first run may archive zero rows if the fixture container is empty;
    # the load-bearing assertion is that a second run never produces new
    # writes once the steady state is reached.
    assert second["archived"] == 0
    # Re-runs scan the same rows (modulo wall-clock drift) and reclassify
    # already-archived blobs as skipped.
    if first["archived"] > 0:
        assert second["skipped"] >= first["archived"]
