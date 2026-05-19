"""Reconciliation integration test (002 TASK-028 / TASK-029).

Adds a new question to the desired set, re-runs the seed loader against a
fake AI Search + fake Cosmos, and asserts that `topics.counts[topic][lang]`
increments within one loader run.

This is the contract test for the audit P2.15 fix: `list_topics` (the
runtime API) cannot lie about per-language availability because reindex
always reconciles the catalog counts against the index facet counts.
"""

import asyncio
import copy
import dataclasses
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SEED_ROOT = REPO_ROOT / "src" / "seed" / "questions"


# ---------------------------------------------------------------------------
# In-memory fakes (richer than test_question_search.py — covers upserts +
# faceted queries)
# ---------------------------------------------------------------------------


class _Iter:
    def __init__(self, items: list[dict[str, Any]], *, count: int | None = None,
                 facets: dict[str, Any] | None = None) -> None:
        self._items = items
        self._count = count if count is not None else len(items)
        self._facets = facets or {}

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for item in self._items:
                yield item

        return gen()

    async def get_count(self) -> int:
        return self._count

    async def get_facets(self) -> dict[str, Any]:
        return self._facets


@dataclasses.dataclass
class FakeSearchClient:
    documents: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)

    async def __aenter__(self) -> "FakeSearchClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def upload_documents(self, documents: list[dict[str, Any]]) -> None:
        for doc in documents:
            self.documents[doc["id"]] = copy.deepcopy(doc)

    async def delete_documents(self, documents: list[dict[str, Any]]) -> None:
        for doc in documents:
            self.documents.pop(doc["id"], None)

    async def get_document(
        self, key: str, selected_fields: list[str] | None = None
    ) -> dict[str, Any]:
        if key not in self.documents:
            raise KeyError(key)
        doc = self.documents[key]
        if selected_fields is None:
            return dict(doc)
        return {k: v for k, v in doc.items() if k in selected_fields}

    @staticmethod
    def _apply_filter(items: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
        clauses = [c.strip() for c in expr.split(" and ")]
        for clause in clauses:
            field, _, value = clause.partition(" eq ")
            field = field.strip()
            value = value.strip().strip("'")
            items = [d for d in items if str(d.get(field)) == value]
        return items

    async def search(
        self,
        search_text: str = "*",
        filter: str | None = None,
        select: list[str] | None = None,
        top: int = 1000,
        facets: list[str] | None = None,
        include_total_count: bool = False,
    ) -> _Iter:
        items = list(self.documents.values())
        if filter:
            items = self._apply_filter(items, filter)
        if select:
            items = [{k: v for k, v in d.items() if k in select} for d in items]
        facet_payload: dict[str, Any] = {}
        if facets:
            for facet_expr in facets:
                field = facet_expr.split(",", 1)[0]
                bucket: dict[str, int] = {}
                for d in self.documents.values():
                    val = str(d.get(field))
                    bucket[val] = bucket.get(val, 0) + 1
                facet_payload[field] = [
                    {"value": k, "count": v} for k, v in sorted(bucket.items())
                ]
        return _Iter(items[:top], count=len(items), facets=facet_payload)


@dataclasses.dataclass
class FakeCosmosContainer:
    items: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)

    def read_all_items(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for item in list(self.items.values()):
                yield copy.deepcopy(item)

        return gen()

    async def replace_item(
        self,
        item: str,
        body: dict[str, Any],
        etag: str | None = None,
        match_condition: str | None = None,
    ) -> None:
        if etag is not None and self.items.get(item, {}).get("_etag") != etag:
            raise RuntimeError("etag mismatch")
        new_etag = f"v{int(self.items.get(item, {}).get('_etag', 'v0')[1:]) + 1}"
        body = copy.deepcopy(body)
        body["_etag"] = new_etag
        self.items[item] = body


@dataclasses.dataclass
class FakeCosmosDatabase:
    container_by_name: dict[str, FakeCosmosContainer]

    def get_container_client(self, name: str) -> FakeCosmosContainer:
        return self.container_by_name[name]


@dataclasses.dataclass
class FakeCosmosClient:
    database_by_name: dict[str, FakeCosmosDatabase]

    async def __aenter__(self) -> "FakeCosmosClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def get_database_client(self, name: str) -> FakeCosmosDatabase:
        return self.database_by_name[name]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_authored_records() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(SEED_ROOT.rglob("*.json")):
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def _make_new_question_pack(logical_id: str = "az-net-zzz-099") -> list[dict[str, Any]]:
    """Make three new translations for a single logical question."""
    return [
        {
            "logical_id": logical_id,
            "topic": "azure-networking",
            "language": lang,
            "text": f"({lang}) What does NSG stand for?",
            "options": [
                {"key": "A", "text": "Network Storage Group"},
                {"key": "B", "text": "Network Security Group"},
                {"key": "C", "text": "Naming Service Gateway"},
                {"key": "D", "text": "Native Security Guard"},
            ],
            "correct_answer": ["B"],
            "difficulty": "easy",
            "tags": ["nsg"],
            "category": "networking",
            "explanation": f"({lang}) Network Security Group filters at L3/L4.",
            "score_weight": 1.0,
        }
        for lang in ("en", "fr", "es")
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reindex_increments_topics_counts_for_added_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.seed import seed_index
    from src.seed.reconcile_topics import reconcile

    # Skip the indexer-sync delay — the fake search client is synchronous so
    # the post-write state is immediately visible.
    monkeypatch.setattr(seed_index, "INDEXER_SYNC_SECONDS", 0)

    async def run() -> tuple[int, dict[str, dict[str, int]]]:
        search_client = FakeSearchClient()

        # Step 1: load baseline (90 authored records) into a fresh index.
        baseline = _load_authored_records()
        await seed_index.run_seed(
            desired_records=baseline,
            search_client=search_client,
            confirm_deletes=False,
        )

        # Step 2: seed Cosmos `topics` with the initial counts. Pretend the
        # catalog was authored with the counts from the first reindex.
        topics_container = FakeCosmosContainer(
            items={
                "azure-networking": {
                    "id": "azure-networking",
                    "_etag": "v1",
                    "labels": {"en": "Azure Networking", "fr": "Réseau Azure", "es": "Redes de Azure"},
                    "counts": {"en": 10, "fr": 10, "es": 10},
                },
                "azure-storage": {
                    "id": "azure-storage",
                    "_etag": "v1",
                    "labels": {"en": "Azure Storage", "fr": "Stockage Azure", "es": "Almacenamiento de Azure"},
                    "counts": {"en": 10, "fr": 10, "es": 10},
                },
                "azure-security": {
                    "id": "azure-security",
                    "_etag": "v1",
                    "labels": {"en": "Azure Security", "fr": "Sécurité Azure", "es": "Seguridad de Azure"},
                    "counts": {"en": 10, "fr": 10, "es": 10},
                },
            }
        )
        cosmos_db = FakeCosmosDatabase(container_by_name={"topics": topics_container})
        cosmos_client = FakeCosmosClient(database_by_name={"flint": cosmos_db})

        # Step 3: extend the authored set with one new logical question
        # (3 new records, one per language) and re-run the loader.
        extended = baseline + _make_new_question_pack()
        await seed_index.run_seed(
            desired_records=extended,
            search_client=search_client,
            confirm_deletes=False,
        )

        # Patch the Cosmos SDK import inside reconcile_topics with our fake.
        import src.seed.reconcile_topics as mod

        class _CosmosClientCtx:
            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                self._client = cosmos_client

            async def __aenter__(self) -> FakeCosmosClient:
                return self._client

            async def __aexit__(self, *_args: Any) -> None:
                return None

        # The reconcile function does a local `from azure.cosmos.aio import CosmosClient`
        # so we patch via sys.modules.
        import sys
        import types

        fake_azure = types.ModuleType("azure.cosmos.aio")
        fake_azure.CosmosClient = _CosmosClientCtx  # type: ignore[attr-defined]
        sys.modules["azure.cosmos.aio"] = fake_azure

        outcome = await reconcile(
            search_client=search_client,
            cosmos_endpoint="https://fake-cosmos",
            cosmos_database="flint",
            credential=None,
        )

        return outcome.topics_reconciled, {
            tid: dict(item["counts"]) for tid, item in topics_container.items.items()
        }

    topics_reconciled, final_counts = asyncio.run(run())

    # azure-networking went from 10/lang → 11/lang (one new question per language).
    assert final_counts["azure-networking"] == {"en": 11, "fr": 11, "es": 11}
    # Other topics stay at 10/lang.
    assert final_counts["azure-storage"] == {"en": 10, "fr": 10, "es": 10}
    assert final_counts["azure-security"] == {"en": 10, "fr": 10, "es": 10}
    # At least azure-networking row was rewritten this run (the storage /
    # security rows were already correct so they were skipped to save RU —
    # see reconcile_topics for the no-op short-circuit).
    assert topics_reconciled >= 1


def test_loader_is_idempotent_over_unchanged_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """TASK-026 — re-running over an unchanged tree produces zero writes."""
    from src.seed import seed_index

    monkeypatch.setattr(seed_index, "INDEXER_SYNC_SECONDS", 0)

    async def run() -> tuple[int, int]:
        search_client = FakeSearchClient()
        records = _load_authored_records()

        # First run: populates an empty index.
        first = await seed_index.run_seed(
            desired_records=records,
            search_client=search_client,
            confirm_deletes=False,
        )
        first_total = sum(s.added + s.updated + s.deleted for s in first)

        # Second run over the same records: should produce zero writes.
        second = await seed_index.run_seed(
            desired_records=records,
            search_client=search_client,
            confirm_deletes=False,
        )
        second_total = sum(s.added + s.updated + s.deleted for s in second)
        return first_total, second_total

    first_total, second_total = asyncio.run(run())
    assert first_total == 90, "first run should add all 90 records"
    assert second_total == 0, "idempotent re-run should produce zero writes"


def test_mass_delete_requires_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """TASK-028 — refusing to delete > 10 % of the index without --confirm."""
    from src.seed import seed_index

    monkeypatch.setattr(seed_index, "INDEXER_SYNC_SECONDS", 0)

    async def run() -> None:
        search_client = FakeSearchClient()
        baseline = _load_authored_records()
        await seed_index.run_seed(
            desired_records=baseline,
            search_client=search_client,
            confirm_deletes=False,
        )
        # Remove the entire azure-storage topic (30 records — 33 %) from the
        # desired set. Loader should refuse without --confirm.
        shrunk = [r for r in baseline if r["topic"] != "azure-storage"]
        with pytest.raises(RuntimeError, match="refusing to delete"):
            await seed_index.run_seed(
                desired_records=shrunk,
                search_client=search_client,
                confirm_deletes=False,
            )
        # With --confirm it goes through.
        summaries = await seed_index.run_seed(
            desired_records=shrunk,
            search_client=search_client,
            confirm_deletes=True,
        )
        deleted_total = sum(s.deleted for s in summaries)
        assert deleted_total == 30

    asyncio.run(run())
