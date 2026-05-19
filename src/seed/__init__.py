"""Seed / one-shot loader entrypoints (002 task pack).

`seed_index` walks the authored question tree and upserts into the AI Search
`questions` index. `reconcile_topics` updates Cosmos `topics.counts` against
the post-reindex facet counts. Both run under `uami-indexer-*` only — never
the runtime agent identity.
"""

__all__ = ["seed_index", "reconcile_topics"]
