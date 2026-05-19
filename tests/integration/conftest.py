"""Shared fixtures + fakes for 003-cosmos-db integration tests.

Real-Cosmos tests are gated on ``COSMOS_EMULATOR_ENDPOINT`` (or
``COSMOS_TEST_ENDPOINT``) — the TEST-007 contract per
``specs/006-testing-strategy.md §3`` requires the real ``ifMatch`` etag
primitive, not a mock. State-machine tests use the in-memory fake below;
they don't need a live emulator to verify the transition guard.
"""

from __future__ import annotations

import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterable

import pytest

from src.data.cosmos_repository import CosmosRepository
from src.data.models import (
    Answer,
    Channel,
    SessionDoc,
    SessionStatus,
    Verdict,
)


def cosmos_emulator_available() -> bool:
    return bool(
        os.environ.get("COSMOS_EMULATOR_ENDPOINT") or os.environ.get("COSMOS_TEST_ENDPOINT")
    )


requires_cosmos = pytest.mark.skipif(
    not cosmos_emulator_available(),
    reason="set COSMOS_EMULATOR_ENDPOINT or COSMOS_TEST_ENDPOINT to run real-Cosmos integration tests",
)


# ---------------------------------------------------------------------------
# In-memory fake container + repository (for state-machine tests)
# ---------------------------------------------------------------------------


class _FakeContainer:
    """Minimal in-memory `ContainerProxy` stand-in with etag enforcement.

    Supports the subset of the SDK surface the repository touches:
    ``read_item``, ``create_item``, ``replace_item`` (honors ``etag`` +
    ``match_condition``), and ``read_all_items``. Just enough to keep the
    state-machine test honest without booting the emulator.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _new_etag() -> str:
        return f'"{uuid.uuid4()}"'

    @staticmethod
    def _key(body: dict[str, Any]) -> tuple[str, str]:
        # All four containers in this repo are partitioned by either
        # /userId, /topicId, or /sessionId — the fake just stores by
        # (id, partition value) where partition value falls back to id.
        return (body["id"], body.get("userId") or body.get("topicId") or body.get("sessionId") or body["id"])

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:
        try:
            return deepcopy(self._store[(item, partition_key)])
        except KeyError:
            from azure.cosmos import exceptions as ex

            raise ex.CosmosResourceNotFoundError(message=f"{item} not found", status_code=404)

    async def create_item(self, *, body: dict[str, Any]) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex

        key = self._key(body)
        if key in self._store:
            raise ex.CosmosResourceExistsError(message="exists", status_code=409)
        stored = deepcopy(body)
        stored["_etag"] = self._new_etag()
        stored["_ts"] = int(datetime.now(tz=timezone.utc).timestamp())
        self._store[key] = stored
        return deepcopy(stored)

    async def upsert_item(self, *, body: dict[str, Any]) -> dict[str, Any]:
        key = self._key(body)
        stored = deepcopy(body)
        stored["_etag"] = self._new_etag()
        stored["_ts"] = int(datetime.now(tz=timezone.utc).timestamp())
        self._store[key] = stored
        return deepcopy(stored)

    async def replace_item(
        self,
        *,
        item: str,
        body: dict[str, Any],
        etag: str | None = None,
        match_condition: Any = None,
    ) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex

        key = self._key(body)
        if key not in self._store:
            raise ex.CosmosResourceNotFoundError(message=f"{item} missing", status_code=404)
        if etag is not None and self._store[key].get("_etag") != etag:
            raise ex.CosmosAccessConditionFailedError(message="etag mismatch", status_code=412)
        stored = deepcopy(body)
        stored["_etag"] = self._new_etag()
        stored["_ts"] = int(datetime.now(tz=timezone.utc).timestamp())
        self._store[key] = stored
        return deepcopy(stored)

    async def read_all_items(self) -> AsyncIterator[dict[str, Any]]:
        for v in list(self._store.values()):
            yield deepcopy(v)

    async def query_items(
        self,
        *,
        query: str,
        parameters: Iterable[dict[str, Any]] = (),
        max_item_count: int = 100,
    ) -> AsyncIterator[dict[str, Any]]:
        # The state-machine and audit-archive tests don't exercise the
        # query path; the sweeper integration test wires a more capable
        # query handler explicitly. For now, yield every row — predicates
        # are the caller's responsibility on the fake.
        for v in list(self._store.values()):
            yield deepcopy(v)


class FakeCosmosRepository(CosmosRepository):
    """Repository wired to in-memory fakes — no Cosmos client constructed."""

    def __init__(self, *, sessions_terminal_ttl_seconds: int = 60) -> None:
        # Deliberately skip the parent __init__ to avoid constructing a
        # CosmosClient (which would try to resolve a token).
        self._endpoint = "https://fake/"
        self._database_name = "flint-quiz"
        self._sessions_terminal_ttl_seconds = sessions_terminal_ttl_seconds
        self._credential = None  # type: ignore[assignment]
        self._client = None  # type: ignore[assignment]
        self._sessions = _FakeContainer()  # type: ignore[assignment]
        self._users = _FakeContainer()  # type: ignore[assignment]
        self._topics = _FakeContainer()  # type: ignore[assignment]
        self._audit = _FakeContainer()  # type: ignore[assignment]

    async def close(self) -> None:  # noqa: D401 - simple override
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_doc(
    *,
    session_id: str = "sess-1",
    user_id: str = "user-1",
    n: int = 3,
    status: SessionStatus = SessionStatus.ACTIVE,
) -> SessionDoc:
    now = datetime.now(tz=timezone.utc)
    return SessionDoc(
        id=session_id,
        user_id=user_id,
        topic="azure-networking",
        language="en",
        requested_language="en",
        seed="0123456789abcdef",
        shuffled_ids=[f"q-{i:03d}-en" for i in range(n)],
        current_index=0,
        answers=[],
        score=0.0,
        max_score=float(n),
        status=status,
        started_at=now,
        question_started_at=now,
        time_limit_seconds=600,
        channel=Channel.TEXT,
    )


def make_answer(question_id: str, *, verdict: Verdict = Verdict.CORRECT) -> Answer:
    return Answer(
        question_id=question_id,
        received_raw="A",
        received_normalized="A",
        verdict=verdict,
        score_delta=1.0 if verdict == Verdict.CORRECT else 0.0,
        answered_at=datetime.now(tz=timezone.utc),
        channel=Channel.TEXT,
        latency_ms=10,
    )


