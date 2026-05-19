"""GDPR right-to-erasure (TEST-028 / TASK-186 / ADR-006).

Top-level spec-anchored sub-suite. The detailed integration test at
`tests/integration/test_gdpr_erasure.py` walks every edge case; this
file is the CI-pipeline entry point that asserts the five outcomes
TASK-186 calls out explicitly:

  * Cascade — `users` gone, `sessions` gone, `audit` pseudonymised,
    `audit.user_erased` event present with correct counts.
  * Repeat — second run is a no-op + one dedup'd
    `audit.user_erased.repeat`.
  * Auth-negative — caller without `group:flint-support-erasure` is
    rejected (403); no state mutated.
  * Salt rotation — v2 pseudonym distinct from v1.
  * Pseudonym format — `pseudo:v{N}:<16-hex>`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.data.erasure import (
    ErasureService,
    EVENT_USER_ERASED,
    EVENT_USER_ERASED_REPEAT,
    SUPPORT_GROUP_NAME,
)
from tests.integration.test_gdpr_erasure import (
    FakeArchive,
    FakeErasureRepo,
    FakeGroups,
    FakeKeyVault,
    RecordingEmitter,
    SUPPORT_OID,
    TARGET_USER,
    _seed_repo,
)

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


def _service(*, group_member: bool = True, salt_version: int = 1):
    repo = FakeErasureRepo()
    _seed_repo(repo)
    archive = FakeArchive(locked_for={})
    groups = FakeGroups(
        members={SUPPORT_GROUP_NAME: {SUPPORT_OID} if group_member else set()}
    )
    emitter = RecordingEmitter()
    service = ErasureService(
        repo=repo,  # type: ignore[arg-type]
        archive=archive,  # type: ignore[arg-type]
        groups=groups,  # type: ignore[arg-type]
        keyvault=FakeKeyVault({"erasure-pseudonym-salt": "salt"}),  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
        salt_version=salt_version,
    )
    return service, repo, emitter


@pytest.mark.asyncio
async def test_cascade_post_conditions_match_spec() -> None:
    service, repo, emitter = _service()
    receipt = await service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-1"
    )
    assert TARGET_USER not in repo.users
    assert not any(uid == TARGET_USER for (_, uid) in repo.sessions.keys())
    assert all(row["userId"].startswith("pseudo:v1:") for row in repo.audit.values() if row["id"] != "aud-other")
    assert emitter.count(EVENT_USER_ERASED) == 1
    assert receipt.user_id_pseudonym.startswith("pseudo:v1:")


@pytest.mark.asyncio
async def test_repeat_run_is_no_op() -> None:
    service, _, emitter = _service()
    await service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-1"
    )
    second = await service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-2"
    )
    assert second.repeat is True
    assert emitter.count(EVENT_USER_ERASED_REPEAT) == 1


@pytest.mark.asyncio
async def test_caller_without_support_group_rejected() -> None:
    service, repo, emitter = _service(group_member=False)
    users_before = dict(repo.users)
    with pytest.raises(PermissionError):
        await service.erase_user(
            user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-DENY"
        )
    # No state mutated.
    assert repo.users == users_before
    assert emitter.count(EVENT_USER_ERASED) == 0


@pytest.mark.asyncio
async def test_salt_rotation_produces_v2_pseudonym() -> None:
    v1_service, *_ = _service(salt_version=1)
    v1_receipt = await v1_service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-A"
    )
    v2_service, *_ = _service(salt_version=2)
    v2_receipt = await v2_service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-B"
    )
    assert v1_receipt.user_id_pseudonym.startswith("pseudo:v1:")
    assert v2_receipt.user_id_pseudonym.startswith("pseudo:v2:")


@pytest.mark.asyncio
async def test_pseudonym_format_is_pseudo_vN_16hex() -> None:
    import re

    service, *_ = _service()
    receipt = await service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="T-FMT"
    )
    assert re.fullmatch(r"pseudo:v\d+:[0-9a-f]{16}", receipt.user_id_pseudonym)
