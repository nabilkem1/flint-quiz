"""GDPR right-to-erasure cascade (TASK-134 / SEC-008 / GDPR Art. 17).

`erase_user` is the single entry point for the user-deletion flow. It
is **not** an agent tool surface — it is invoked by support tooling
(CLI or admin API) authenticated via Entra group membership
``group:flint-support-erasure``. The agent dispatcher will never see
this function.

Cascade order (each step idempotent, partition-bounded per resource):

  1. Hard-delete ``users.{userId}`` (single point delete, pk ``/userId``).
  2. Hard-delete every ``sessions`` row where pk = ``/userId``
     (cross-row but single-partition; no cross-partition scan).
  3. Pseudonymize ``audit`` rows where ``userId == target``: replace
     ``userId`` with ``pseudo:v{N}:{sha256(userId, kv_salt)[:16]}``.
     The salt lives in Key Vault (``erasure-pseudonym-salt``); only the
     KV wrapper reads it. ``ifMatch(_etag)`` on every replace.
  4. Acknowledge ``audit-archive`` Blob snapshots past their
     immutability lock — these retain the original ``userId`` by
     compliance design (GDPR Art. 17(3)(b)). Emit
     ``audit.erasure_archive_locked`` listing the locked snapshot IDs.
  5. Emit ``audit.user_erased`` to App Insights AND a dedicated Cosmos
     ``audit`` partition with the **pseudonymized** userId, carrying
     ``requested_by``, ``ticket_ref``, ``timestamp``, and per-resource
     counts. A second run for an already-erased user emits a single
     dedup'd ``audit.user_erased.repeat`` event.

The cascade is **deliberately not transactional across resources** —
each step is a small idempotent operation. A mid-cascade crash is
recoverable by re-running.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from src.data.keyvault_client import KeyVaultClient

logger = logging.getLogger(__name__)

# The Entra group membership the caller MUST hold. Never trusted from
# tool args (the cascade is not an agent tool); resolved from the
# caller's Entra token claims.
SUPPORT_GROUP_NAME: str = "group:flint-support-erasure"

# Key Vault secret containing the salt used in the pseudonym hash. The
# salt is rotated periodically; pseudonyms carry the version
# (`pseudo:v1:`, `pseudo:v2:`) so post-rotation rows remain distinguishable.
PSEUDONYM_SALT_SECRET: str = "erasure-pseudonym-salt"

# Version label embedded in every pseudonym. Bumped on salt rotation. The
# active version is read from Key Vault (`erasure-pseudonym-salt-version`)
# so a rotation is configuration, not code change.
DEFAULT_SALT_VERSION: int = 1

# Event names emitted on the success / repeat / locked-archive paths.
EVENT_USER_ERASED: str = "audit.user_erased"
EVENT_USER_ERASED_REPEAT: str = "audit.user_erased.repeat"
EVENT_ARCHIVE_LOCKED: str = "audit.erasure_archive_locked"


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class ErasureRepository(Protocol):
    """Cosmos surface the cascade needs.

    Production wiring binds this to `CosmosRepository` (the methods
    listed here will live there, added in 003-cosmos-db when this work
    is brought online — they are intentionally NOT added in this task
    pack per the FORBIDDEN ACTIONS).

    Tests bind an in-memory fake.
    """

    async def delete_user(self, user_id: str) -> bool: ...

    async def list_session_ids_for_user(self, user_id: str) -> list[str]: ...

    async def delete_session(self, session_id: str, user_id: str) -> bool: ...

    async def list_audit_for_user(self, user_id: str) -> list[Mapping[str, Any]]: ...

    async def replace_audit_user_id(
        self,
        audit_row: Mapping[str, Any],
        new_user_id: str,
    ) -> bool: ...

    async def write_audit_envelope(self, payload: Mapping[str, Any]) -> None: ...


class ArchiveInspector(Protocol):
    """Read-only enumeration of Blob audit-archive snapshots.

    The cascade can NOT mutate locked snapshots (compliance-required
    immutability); it only enumerates them so the receipt can list
    which userId-bearing archives survive the erasure.
    """

    async def list_locked_snapshots_for_user(
        self, user_id: str
    ) -> list[str]: ...


class GroupResolver(Protocol):
    """Verifies a caller holds a specific Entra group.

    Production binds to Microsoft Graph (`groups/<id>/transitiveMembers`);
    tests pass a dict-backed fake. The cascade NEVER accepts group
    membership from request args — it resolves from the OID claim only.
    """

    async def is_member_of(self, *, entra_oid: str, group: str) -> bool: ...


class EventEmitter(Protocol):
    def emit(self, name: str, properties: Mapping[str, Any]) -> None: ...


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErasureReceipt:
    """Audit-grade record of the cascade outcome.

    Returned to the support CLI for record-keeping. Carries counts
    only — never the original `user_id` (which is the value being
    erased). The pseudonymized userId is exposed so a follow-up
    audit query can correlate.
    """

    user_id_pseudonym: str
    salt_version: int
    requested_by: str
    ticket_ref: str
    timestamp: datetime
    users_deleted: int
    sessions_deleted: int
    audit_pseudonymized: int
    archive_locked_snapshots: tuple[str, ...]
    repeat: bool


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class ErasureService:
    """Stateful orchestrator of the cascade.

    Construct one per process. Dependencies are injected (KV, Cosmos
    surface, archive inspector, group resolver, emitter, clock) so tests
    can wire light fakes without touching Azure.
    """

    repo: ErasureRepository
    archive: ArchiveInspector
    groups: GroupResolver
    keyvault: KeyVaultClient
    emitter: EventEmitter
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(tz=timezone.utc))
    salt_version: int = DEFAULT_SALT_VERSION

    # ----- Public entrypoint -------------------------------------------

    async def erase_user(
        self,
        *,
        user_id: str,
        requested_by_oid: str,
        ticket_ref: str,
    ) -> ErasureReceipt:
        """Run the cascade. Idempotent: a repeat call on an already-erased
        user emits a single ``audit.user_erased.repeat`` event and
        otherwise touches no state.

        Raises:
            PermissionError: caller is not a member of
                ``group:flint-support-erasure``. Verified from the
                Entra OID claim, **never** from tool/CLI args.
            ValueError: ``user_id`` or ``ticket_ref`` is empty.
        """

        self._validate_inputs(user_id=user_id, ticket_ref=ticket_ref)
        await self._require_group(requested_by_oid)

        pseudonym = await self._pseudonym(user_id)
        now = self.clock()

        # Stage 1: users (idempotent — `delete_user` returns False on miss).
        users_deleted = 1 if await self.repo.delete_user(user_id) else 0

        # Stage 2: sessions. Partition-scoped enumeration + per-row delete.
        session_ids = await self.repo.list_session_ids_for_user(user_id)
        sessions_deleted = 0
        for session_id in session_ids:
            if await self.repo.delete_session(session_id, user_id):
                sessions_deleted += 1

        # Stage 3: audit (Cosmos hot). Pseudonymize each row exactly once;
        # rows whose userId no longer matches the original are skipped so
        # a repeat call is a no-op.
        audit_rows = await self.repo.list_audit_for_user(user_id)
        audit_pseudonymized = 0
        for row in audit_rows:
            if row.get("userId") != user_id:
                # Already pseudonymized in a prior run — skip.
                continue
            if await self.repo.replace_audit_user_id(row, pseudonym):
                audit_pseudonymized += 1

        # Stage 4: audit-archive immutability acknowledgement (read-only).
        locked_snapshots = tuple(await self.archive.list_locked_snapshots_for_user(user_id))
        if locked_snapshots:
            self.emitter.emit(
                EVENT_ARCHIVE_LOCKED,
                {
                    "user_id_pseudonym": pseudonym,
                    "ticket_ref": ticket_ref,
                    "snapshot_count": len(locked_snapshots),
                    "snapshot_ids": list(locked_snapshots),
                },
            )

        # Stage 5: audit-of-audit envelope.
        is_repeat = (
            users_deleted == 0
            and sessions_deleted == 0
            and audit_pseudonymized == 0
        )
        await self._emit_envelope(
            pseudonym=pseudonym,
            requested_by=requested_by_oid,
            ticket_ref=ticket_ref,
            now=now,
            users_deleted=users_deleted,
            sessions_deleted=sessions_deleted,
            audit_pseudonymized=audit_pseudonymized,
            locked_snapshots=locked_snapshots,
            repeat=is_repeat,
        )

        return ErasureReceipt(
            user_id_pseudonym=pseudonym,
            salt_version=self.salt_version,
            requested_by=requested_by_oid,
            ticket_ref=ticket_ref,
            timestamp=now,
            users_deleted=users_deleted,
            sessions_deleted=sessions_deleted,
            audit_pseudonymized=audit_pseudonymized,
            archive_locked_snapshots=locked_snapshots,
            repeat=is_repeat,
        )

    # ----- Internals ---------------------------------------------------

    def _validate_inputs(self, *, user_id: str, ticket_ref: str) -> None:
        if not user_id or not user_id.strip():
            raise ValueError("erase_user: user_id is required")
        if not ticket_ref or not ticket_ref.strip():
            raise ValueError("erase_user: ticket_ref is required (audit-of-audit)")

    async def _require_group(self, entra_oid: str) -> None:
        """Reject callers not in `group:flint-support-erasure`.

        The membership is resolved from the Entra OID claim, never from
        any args the caller sends. A negative result fires a structured
        event so post-incident review can spot brute-force attempts.
        """

        is_member = False
        try:
            is_member = await self.groups.is_member_of(
                entra_oid=entra_oid, group=SUPPORT_GROUP_NAME
            )
        except Exception:  # noqa: BLE001 — fall to denial
            logger.warning(
                "erasure.group_resolve_failed",
                extra={"oid_prefix": entra_oid[:8]},
                exc_info=True,
            )
            is_member = False

        if not is_member:
            self.emitter.emit(
                "audit.erasure_denied",
                {
                    "oid_prefix": entra_oid[:8],
                    "reason": "not_in_support_group",
                },
            )
            raise PermissionError(
                f"caller is not a member of {SUPPORT_GROUP_NAME!r}"
            )

    async def _pseudonym(self, user_id: str) -> str:
        """Compute ``pseudo:v{N}:<16-hex-chars>`` for `user_id`.

        Salt is fetched from Key Vault (TASK-122) and cached in the
        KV wrapper. The hash is SHA-256(user_id ++ salt)[:16] — first
        16 hex chars are enough entropy for non-reversibility against
        a casual lookup, while keeping the pseudonym readable in logs.
        """

        salt = await self.keyvault.get_secret(PSEUDONYM_SALT_SECRET)
        material = (user_id + salt).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:16]
        return f"pseudo:v{self.salt_version}:{digest}"

    async def _emit_envelope(
        self,
        *,
        pseudonym: str,
        requested_by: str,
        ticket_ref: str,
        now: datetime,
        users_deleted: int,
        sessions_deleted: int,
        audit_pseudonymized: int,
        locked_snapshots: Sequence[str],
        repeat: bool,
    ) -> None:
        """Emit the audit-of-audit event(s).

        Repeat calls fire **only** ``audit.user_erased.repeat`` to make
        deduplication a sql-side `count() == 1` invariant.
        """

        event_name = EVENT_USER_ERASED_REPEAT if repeat else EVENT_USER_ERASED
        properties: dict[str, Any] = {
            "user_id_pseudonym": pseudonym,
            "salt_version": self.salt_version,
            "requested_by_oid_prefix": requested_by[:8],
            "ticket_ref": ticket_ref,
            "timestamp": now.isoformat(),
            "users_deleted": users_deleted,
            "sessions_deleted": sessions_deleted,
            "audit_pseudonymized": audit_pseudonymized,
            "archive_locked_count": len(locked_snapshots),
        }
        self.emitter.emit(event_name, properties)

        # Also persist a pseudonymized envelope into the Cosmos `audit`
        # container so dispute resolution can join App Insights + Cosmos
        # by `user_id_pseudonym`.
        await self.repo.write_audit_envelope(
            {
                "id": f"erasure-{pseudonym}-{int(now.timestamp())}",
                "userId": pseudonym,
                "ticketRef": ticket_ref,
                "requestedByOidPrefix": requested_by[:8],
                "timestamp": now.isoformat(),
                "event": event_name,
                "usersDeleted": users_deleted,
                "sessionsDeleted": sessions_deleted,
                "auditPseudonymized": audit_pseudonymized,
                "archiveLockedSnapshotIds": list(locked_snapshots),
                "saltVersion": self.salt_version,
            }
        )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


async def erase_user(
    service: ErasureService,
    *,
    user_id: str,
    requested_by_oid: str,
    ticket_ref: str,
) -> ErasureReceipt:
    """Functional wrapper around :meth:`ErasureService.erase_user`."""

    return await service.erase_user(
        user_id=user_id,
        requested_by_oid=requested_by_oid,
        ticket_ref=ticket_ref,
    )


__all__ = [
    "DEFAULT_SALT_VERSION",
    "ErasureReceipt",
    "ErasureRepository",
    "ErasureService",
    "EventEmitter",
    "PSEUDONYM_SALT_SECRET",
    "SUPPORT_GROUP_NAME",
    "ArchiveInspector",
    "GroupResolver",
    "erase_user",
]
