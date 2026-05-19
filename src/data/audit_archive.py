"""Audit two-stage archive job (TASK-051, ADR-006).

Daily-cadence job that:

1. Queries the Cosmos ``audit`` container for rows approaching their hot TTL
   — the predicate ``_ts + ttl - (30 * 86400) <= now()`` archives each row
   **30 days before Cosmos deletes it**, leaving slack for re-runs.

2. Writes each row to the immutable Blob container ``audit-archive`` using
   the path ``{sessionId}/{auditId}.json``. The blob body is the
   ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` canonical form
   of the Cosmos document so byte-equality is a deterministic property of
   the data, not the wall-clock.

3. Computes ``sha256`` of the canonical bytes and stores it as both the
   blob's ``content_md5``-companion metadata (``contentHash``) and a guard
   against duplicate archiving. Re-running the job for an already-archived
   row is a no-op (blob exists + same hash) and emits a single
   ``audit_archive.skipped`` log line.

Identity: ``DefaultAzureCredential`` end-to-end. Caller is the agent UAMI
with Cosmos Data Contributor on ``audit`` and Storage Blob Data Contributor
on ``audit-archive`` (SEC-004).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.cosmos import exceptions as cosmos_exceptions
from azure.cosmos.aio import CosmosClient
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient

from src.common.exceptions import FlintUpstreamError

logger = logging.getLogger(__name__)

# Default archive lead time: 30 days before Cosmos deletes the row. Matches
# the risk-mitigation note in TASK-051 ("archive 30 days before Cosmos
# delete"). Configurable so a short-TTL integration test can shrink it.
_DEFAULT_ARCHIVE_LEAD_SECONDS: int = 30 * 24 * 3600


def _canonical_json(doc: dict[str, Any]) -> bytes:
    """Return a byte-stable JSON encoding of `doc` for hash + storage.

    ``sort_keys=True`` makes dict ordering irrelevant; ``separators`` removes
    every whitespace. Two equal-content documents always produce identical
    bytes — that's the property the byte-equivalence acceptance criterion
    relies on (TASK-051 AC).
    """

    return json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _blob_name(session_id: str, audit_id: str) -> str:
    return f"{session_id}/{audit_id}.json"


class AuditArchiveJob:
    """Idempotent Cosmos → immutable Blob archive."""

    def __init__(
        self,
        *,
        cosmos_endpoint: str,
        blob_account_url: str,
        database_name: str = "flint-quiz",
        audit_container: str = "audit",
        archive_container: str = "audit-archive",
        archive_lead_seconds: int = _DEFAULT_ARCHIVE_LEAD_SECONDS,
        credential: DefaultAzureCredential | None = None,
    ) -> None:
        self._archive_lead_seconds = archive_lead_seconds
        self._archive_container = archive_container
        self._credential = credential or DefaultAzureCredential()
        self._cosmos = CosmosClient(cosmos_endpoint, credential=self._credential)
        self._audit = self._cosmos.get_database_client(database_name).get_container_client(
            audit_container
        )
        self._blob = BlobServiceClient(account_url=blob_account_url, credential=self._credential)

    async def close(self) -> None:
        await self._cosmos.close()
        await self._blob.close()
        await self._credential.close()

    async def __aenter__(self) -> "AuditArchiveJob":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def run_once(self, *, now_epoch: int | None = None) -> dict[str, int]:
        """Archive every row whose `_ts + ttl - lead <= now()`.

        Returns a dict of counters: ``{"archived": int, "skipped": int,
        "scanned": int}`` so callers (Function host, ops dashboard) can
        chart progress without parsing log lines.
        """

        now = now_epoch if now_epoch is not None else int(time.time())
        cutoff_ts = now - 1  # safety: never archive future-dated rows
        archived = skipped = scanned = 0

        # Cross-partition is required: archiving spans every session. This
        # job runs once a day, so the cost is bounded — and Cosmos document
        # TTL field is exposed as `c.ttl`, while `_ts` is a Cosmos system
        # field accessible via `c._ts`.
        query = (
            "SELECT * FROM c "
            "WHERE IS_DEFINED(c.ttl) "
            "AND (c._ts + c.ttl - @lead) <= @now "
            "AND c._ts <= @cutoff"
        )
        parameters = [
            {"name": "@lead", "value": self._archive_lead_seconds},
            {"name": "@now", "value": now},
            {"name": "@cutoff", "value": cutoff_ts},
        ]

        try:
            async for doc in self._audit.query_items(query=query, parameters=parameters):
                scanned += 1
                if await self._archive_row(doc):
                    archived += 1
                else:
                    skipped += 1
        except cosmos_exceptions.CosmosHttpResponseError as exc:
            raise FlintUpstreamError(f"audit query failed: {exc.message}") from exc

        logger.info(
            "audit_archive.tick",
            extra={"scanned": scanned, "archived": archived, "skipped": skipped},
        )
        return {"scanned": scanned, "archived": archived, "skipped": skipped}

    async def _archive_row(self, doc: dict[str, Any]) -> bool:
        """Write one Cosmos audit row to the archive container.

        Returns ``True`` if a new blob was written, ``False`` if an
        identical blob already exists (idempotent no-op).
        """

        audit_id = doc.get("id")
        session_id = doc.get("sessionId")
        if not audit_id or not session_id:
            logger.warning(
                "audit_archive.skip_malformed",
                extra={"audit_id": audit_id, "session_id": session_id},
            )
            return False

        canonical = _canonical_json(doc)
        content_hash = hashlib.sha256(canonical).hexdigest()
        blob_name = _blob_name(str(session_id), str(audit_id))
        container_client = self._blob.get_container_client(self._archive_container)
        blob_client = container_client.get_blob_client(blob_name)

        # Idempotency: check for an existing blob and compare its stored
        # content hash. If the hash matches, this is a deliberate re-run —
        # skip silently. If it differs, fail loud: the same audit ID
        # produced different content, which is a data-integrity bug.
        try:
            existing = await blob_client.get_blob_properties()
        except ResourceNotFoundError:
            existing = None

        if existing is not None:
            stored_hash = (existing.metadata or {}).get("contentHash")
            if stored_hash == content_hash:
                return False
            raise FlintUpstreamError(
                f"audit row {audit_id} already archived with a different hash — "
                "data-integrity violation"
            )

        try:
            await blob_client.upload_blob(
                canonical,
                overwrite=False,
                metadata={"contentHash": content_hash, "sessionId": str(session_id)},
            )
        except ResourceExistsError:
            # Race with another archiver: re-check the hash and treat as no-op.
            props = await blob_client.get_blob_properties()
            stored_hash = (props.metadata or {}).get("contentHash")
            if stored_hash == content_hash:
                return False
            raise FlintUpstreamError(
                f"audit row {audit_id} concurrently archived with a different hash"
            )
        return True


__all__ = ["AuditArchiveJob"]
