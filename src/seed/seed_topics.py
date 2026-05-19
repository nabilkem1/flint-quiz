"""Seed the Cosmos `topics` catalog container.

The AI Search seed loader (`src.seed.seed_index`) populates the question
index but does **not** create the catalog rows in Cosmos that
`list_topics` reads from. This script fills that gap: it walks
`src/seed/topics.json`, queries AI Search for current `(topic, language)`
facet counts, and upserts one row per topic into the `topics` container.

Usage::

    python -m src.seed.seed_topics \\
        --cosmos-endpoint $COSMOS_ENDPOINT \\
        --search-endpoint $SEARCH_ENDPOINT \\
        --database flint-quiz

Identity: `DefaultAzureCredential`. Needs `Cosmos DB Built-in Data
Contributor` on the cosmos account and `Search Index Data Reader` on the
search service. The full reconciliation flow (post-reindex counts drift
detection) lives in `src.seed.reconcile_topics` — this script seeds the
initial rows; `reconcile_topics` keeps them honest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("seed.topics")


async def _amain(
    *,
    cosmos_endpoint: str,
    search_endpoint: str,
    database: str,
    container: str,
    source: Path,
    dry_run: bool,
) -> int:
    from azure.cosmos.aio import CosmosClient
    from azure.identity.aio import DefaultAzureCredential

    from src.data.models import TopicDoc
    from src.data.question_search import QuestionSearch, build_search_client

    fixture = json.loads(source.read_text())
    topics = fixture["topics"]

    credential = DefaultAzureCredential()
    search_client = build_search_client(
        endpoint=search_endpoint, index_name="questions", credential=credential
    )
    try:
        # Live counts from AI Search — keeps the catalog in step with the
        # index without depending on the reconcile job to do it later.
        qs = QuestionSearch(search_client)
        counts = await qs.facet_topic_language()
    finally:
        await search_client.close()

    now = datetime.now(timezone.utc)
    docs: list[dict] = []
    for topic in topics:
        topic_id = topic["id"]
        topic_counts = {
            lang: int(n)
            for lang, n in counts.get(topic_id, {}).items()
            if n > 0
        }
        if not topic_counts:
            logger.warning(
                "topic.no_index_rows",
                extra={"topic_id": topic_id, "hint": "skipping — index has no docs"},
            )
            continue
        doc = TopicDoc(
            id=topic_id,
            topic_id=topic_id,
            labels=topic["labels"],
            counts=topic_counts,
            default_language=topic["default_language"],
            default_n=topic.get("default_n"),
            enabled=topic.get("enabled", True),
            updated_at=now,
        )
        docs.append(doc.model_dump(by_alias=True, exclude_none=True, mode="json"))

    if dry_run:
        for d in docs:
            print(json.dumps(d, sort_keys=True))
        return 0

    async with CosmosClient(url=cosmos_endpoint, credential=credential) as cosmos:
        db = cosmos.get_database_client(database)
        cont = db.get_container_client(container)
        for d in docs:
            await cont.upsert_item(body=d)
            logger.info(
                "topic.upserted",
                extra={
                    "topic_id": d["id"],
                    "counts": d["counts"],
                    "labels_keys": sorted(d["labels"].keys()),
                },
            )

    await credential.close()
    logger.info("seed.done", extra={"upserted": len(docs)})
    return 0


def main() -> int:
    logging.basicConfig(
        level="INFO",
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cosmos-endpoint", required=True)
    parser.add_argument("--search-endpoint", required=True)
    parser.add_argument("--database", default="flint-quiz")
    parser.add_argument("--container", default="topics")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).parent / "topics.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the documents that would be upserted; skip Cosmos writes.",
    )
    args = parser.parse_args()

    return asyncio.run(
        _amain(
            cosmos_endpoint=args.cosmos_endpoint,
            search_endpoint=args.search_endpoint,
            database=args.database,
            container=args.container,
            source=args.source,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
