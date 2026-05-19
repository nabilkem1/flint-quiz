"""Reconcile Cosmos `topics.counts` against AI Search facet counts.

Final step of the reindex pipeline (002 TASK-028). Runs after the loader
writes documents and the AI Search indexer sync window has elapsed, so the
facet query reflects the post-reindex state.

Steps:
    1. Query AI Search for `(topic, language)` facet counts.
    2. For each `topic_id` in the Cosmos `topics` container, replace its
       `counts` map with the freshly observed counts via `ifMatch(_etag)`.
    3. Emit `seed_loader.topic_mismatch` warning if the index has a topic
       that `topics` does not, or vice versa. **Never silently** create or
       delete topic catalog rows — that authority lives in 003 TASK-043.

Identity:
    Uses a separate Cosmos `Data Contributor` scope on `topics` only
    (least privilege — never the runtime agent identity).

Refs: audit P2.15, §4.6 (`list_topics` must not lie about availability).
"""

import dataclasses
import logging
from typing import Any

logger = logging.getLogger("seed.reconcile_topics")


@dataclasses.dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """Reconciliation summary — emitted at end of run."""

    topics_reconciled: int
    topic_mismatch_count: int
    facet_pairs: int


async def reconcile(
    *,
    search_client: Any,
    cosmos_endpoint: str,
    cosmos_database: str,
    credential: Any,
    topics_container: str = "topics",
) -> ReconcileOutcome:
    """Walk index facets → update Cosmos `topics.counts` with `ifMatch(_etag)`.

    `search_client` is an already-open `azure.search.documents.aio.SearchClient`.
    `credential` is the same `DefaultAzureCredential` used by the seed loader
    — in production it resolves to `uami-indexer-*`, which holds a custom
    Cosmos DB role scoped to the `topics` container only.
    """
    from src.data.question_search import QuestionSearch

    qs = QuestionSearch(search_client)
    counts = await qs.facet_topic_language()
    facet_pairs = sum(len(per_lang) for per_lang in counts.values())

    # Open Cosmos with the same credential; locally-scoped import keeps the
    # SDK dependency out of seed_index.py's import path when running without
    # Cosmos (e.g. unit tests of just the AI Search loader).
    from azure.cosmos.aio import CosmosClient

    topics_reconciled = 0
    mismatch_count = 0

    async with CosmosClient(url=cosmos_endpoint, credential=credential) as cosmos:
        db = cosmos.get_database_client(cosmos_database)
        container = db.get_container_client(topics_container)

        # Enumerate the existing topic rows to detect index/topics drift.
        existing_topics: dict[str, dict[str, Any]] = {}
        async for item in container.read_all_items():
            existing_topics[item["id"]] = item

        index_topics = set(counts.keys())
        topic_rows = set(existing_topics.keys())

        # Index has a topic catalog row does not have (or vice versa). Never
        # silently create or delete catalog rows; emit a warning event and
        # skip the count update for that topic. The catalog's lifecycle is an
        # authoring decision (003 TASK-043).
        only_in_index = index_topics - topic_rows
        only_in_catalog = topic_rows - index_topics
        for topic_id in sorted(only_in_index | only_in_catalog):
            mismatch_count += 1
            logger.warning(
                "seed_loader.topic_mismatch",
                extra={
                    "topic_id": topic_id,
                    "present_in_index": topic_id in index_topics,
                    "present_in_catalog": topic_id in topic_rows,
                },
            )

        for topic_id in sorted(index_topics & topic_rows):
            current = existing_topics[topic_id]
            new_counts = counts[topic_id]
            # Skip the write if counts are already correct — minimizes Cosmos
            # RU spend on idempotent re-runs.
            if current.get("counts") == new_counts:
                continue
            etag = current.get("_etag")
            current["counts"] = new_counts
            access_condition = None
            if etag:
                # `if_match_etag` is the supported kwarg name for the async
                # cosmos client; surface the etag as the precondition so a
                # racing writer (the topics-authoring path) wins or loses
                # deterministically rather than silently overwriting.
                access_condition = etag
            try:
                if access_condition is not None:
                    await container.replace_item(
                        item=topic_id,
                        body=current,
                        etag=access_condition,
                        match_condition="IfMatch",
                    )
                else:
                    await container.replace_item(item=topic_id, body=current)
            except Exception as exc:  # pragma: no cover - network errors
                logger.warning(
                    "seed_loader.topic_write_failed",
                    extra={"topic_id": topic_id, "error": str(exc)},
                )
                continue
            topics_reconciled += 1
            logger.info(
                "seed_loader.topic_reconciled",
                extra={"topic_id": topic_id, "counts": new_counts},
            )

    return ReconcileOutcome(
        topics_reconciled=topics_reconciled,
        topic_mismatch_count=mismatch_count,
        facet_pairs=facet_pairs,
    )


__all__ = ["ReconcileOutcome", "reconcile"]
