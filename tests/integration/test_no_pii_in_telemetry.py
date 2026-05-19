"""Telemetry redaction (AL-006 / SEC-001 in telemetry / TASK-141).

The structural property: no 🟡 or 🔴 field from
``specs/008-api-contracts.md §0.1`` may appear in any App Insights
event the agent emits. The two enforcement layers:

  1. `src/observability/events.py` rejects forbidden dimension names
     at emission time.
  2. This test runs an end-to-end submit_answer + erasure flow and
     asserts the recorded events contain none of the forbidden field
     names anywhere — top-level or nested.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.observability.events import (
    FORBIDDEN_EVENT_DIMENSIONS,
    RecordingEmitter,
)

from ._tools_fakes import build_fake_search
from .conftest import FakeCosmosRepository, make_session_doc

NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
PRINCIPAL = Principal(entra_oid="alice-oid")


@pytest.mark.asyncio
async def test_submit_answer_telemetry_has_no_forbidden_fields() -> None:
    repo = FakeCosmosRepository()
    emitter = RecordingEmitter()
    deps = ToolDeps(
        repo=repo,
        search=build_fake_search(count=3, language="en"),  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
    )
    session = make_session_doc(n=3).model_copy(
        update={
            "user_id": "alice-oid",
            "shuffled_ids": [f"azure-networking-{i:03d}-en" for i in range(3)],
            "started_at": NOW,
            "question_started_at": NOW,
        }
    )
    stored = await repo.create_session(session)
    tools = build_tools(deps)
    await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        PRINCIPAL,
    )

    for name, properties in emitter.events:
        raw = json.dumps(properties)
        for forbidden in FORBIDDEN_EVENT_DIMENSIONS:
            assert forbidden not in raw, (
                f"event {name!r} carries forbidden token {forbidden!r}: {raw}"
            )


@pytest.mark.asyncio
async def test_erasure_telemetry_has_no_forbidden_fields() -> None:
    from src.data.erasure import ErasureService
    from tests.integration.test_gdpr_erasure import (
        FakeArchive,
        FakeErasureRepo,
        FakeGroups,
        FakeKeyVault,
        SUPPORT_OID,
        TARGET_USER,
        _seed_repo,
    )
    from src.data.erasure import SUPPORT_GROUP_NAME

    repo = FakeErasureRepo()
    _seed_repo(repo)
    emitter = RecordingEmitter()
    service = ErasureService(
        repo=repo,  # type: ignore[arg-type]
        archive=FakeArchive(locked_for={TARGET_USER: ["snap-1"]}),  # type: ignore[arg-type]
        groups=FakeGroups(members={SUPPORT_GROUP_NAME: {SUPPORT_OID}}),  # type: ignore[arg-type]
        keyvault=FakeKeyVault({"erasure-pseudonym-salt": "salt-value"}),  # type: ignore[arg-type]
        emitter=emitter,
        clock=lambda: NOW,
    )
    await service.erase_user(
        user_id=TARGET_USER, requested_by_oid=SUPPORT_OID, ticket_ref="TICKET-PII"
    )

    for name, properties in emitter.events:
        raw = json.dumps(properties)
        for forbidden in FORBIDDEN_EVENT_DIMENSIONS:
            assert forbidden not in raw, (
                f"event {name!r} carries forbidden token {forbidden!r}: {raw}"
            )


def test_grading_event_dimension_set_does_not_overlap_forbidden() -> None:
    """Static check — the documented grading_event dimension set MUST
    not intersect the forbidden field set. A documentation drift that
    added e.g., `expected` to the grading_event spec would fail here."""

    from src.observability.events import _REQUIRED_DIMENSIONS, AgentEvent  # type: ignore[attr-defined]

    grading_dims = _REQUIRED_DIMENSIONS[AgentEvent.GRADING_EVENT]
    assert not (grading_dims & FORBIDDEN_EVENT_DIMENSIONS)
