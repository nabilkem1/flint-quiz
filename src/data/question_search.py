"""AI Search client — the answer-leakage boundary.

This module exposes the **only** sanctioned read paths into the `questions`
index:

* `QuestionSearch.get_question_view` — LLM-safe projection. The literal string
  `correct_answer` does not appear in the `selected_fields` allowlist; the
  result document therefore cannot contain it. Returns a `QuestionView`
  (Pydantic model, `extra="forbid"`) built field-by-field — even a widened
  projection cannot smuggle additional fields through the model.
* `QuestionSearch.get_answer_key` — server-only path. **Called from
  `submit_answer` only.** AST lint (`tasks/007 TASK-125`) blocks any other
  call site. Returns `AnswerKey`, a dataclass with no JSON serializer.
* `QuestionSearch.search_topic` — returns per-language doc keys (the seeded
  shuffle in `start_quiz` operates on the ID list, never on full documents;
  the keys are later resolved via `get_question_view`).

The per-language fields `text_<lang>` / `explanation_<lang>` are an
implementation detail of the index schema (002 TASK-021). The application
layer pivots on the record's `language` field to pick the populated one when
constructing `QuestionView`; the spec (008-api §1.5.4) exposes a single
`text` field.

Refs: SEC-001, SEC-002, ADR-005, 008-api-contracts §3.3, tasks/002 TASK-027.
"""

import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from src.data.models import (
    SUPPORTED_LANGUAGES,
    AnswerKey,
    Difficulty,
    LanguageCode,
    LogicalId,
    Option,
    QuestionId,
    QuestionView,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from azure.core.credentials_async import AsyncTokenCredential
    from azure.search.documents.aio import SearchClient

logger = logging.getLogger(__name__)

# Allowlist passed to AI Search for the LLM-safe path. The literal string
# `correct_answer` MUST NOT appear here (SEC-001 enforcement point — see
# tasks/002 TASK-027 and 008-api §3.3.1). The per-language `text_*` /
# `explanation_*` fields are an implementation detail of the multi-language
# analyzer setup (TASK-021); we expose a single `text` field on QuestionView.
QUESTION_VIEW_FIELDS: tuple[str, ...] = (
    "id",
    "logical_id",
    "topic",
    "language",
    "text_en",
    "text_fr",
    "text_es",
    "options",
    "difficulty",
)

# Allowlist for the server-only path. The complementary set: `correct_answer`
# and `score_weight`, with `id` for traceability. Never widen.
ANSWER_KEY_FIELDS: tuple[str, ...] = ("id", "correct_answer", "score_weight")


@runtime_checkable
class _SearchClientLike(Protocol):
    """Protocol covering the subset of `SearchClient` we depend on.

    Tests substitute an in-memory fake; production wires the real
    `azure.search.documents.aio.SearchClient`. Constructor injection per
    docs/coding-standards.md §1.11.
    """

    async def get_document(  # pragma: no cover - protocol
        self, key: str, selected_fields: list[str] | None = ...
    ) -> dict[str, Any]: ...

    def search(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - protocol
        ...


def _resolve_text(doc: dict[str, Any], language: str) -> str:
    """Pick the populated `text_<lang>` field for a record.

    The seed loader writes exactly one of `text_en` / `text_fr` / `text_es`
    based on the record's `language`. If the document is well-formed, the
    matching field is non-empty.
    """
    key = f"text_{language}"
    value = doc.get(key)
    if not value:
        # Defensive: fall back to whichever per-language field is populated.
        # This should never trigger on a well-formed record but guards against
        # an authoring drift slipping past the seed loader.
        for fallback in ("text_en", "text_fr", "text_es"):
            if doc.get(fallback):
                logger.warning(
                    "question_search.text_field_fallback",
                    extra={
                        "question_id": doc.get("id"),
                        "expected_field": key,
                        "fallback_field": fallback,
                    },
                )
                return str(doc[fallback])
        raise ValueError(
            f"document {doc.get('id')!r} has no populated text field for language {language!r}"
        )
    return str(value)


class QuestionSearch:
    """Two-method AI Search client. The 'one canonical security boundary' for SEC-001.

    Construct with an `azure.search.documents.aio.SearchClient` already bound
    to the `questions` index and authenticated via `DefaultAzureCredential`
    resolved to the agent UAMI (which has `Search Index Data Reader`, SEC-005).
    """

    def __init__(self, search_client: _SearchClientLike) -> None:
        self._search = search_client

    async def get_question_view(self, question_id: QuestionId) -> QuestionView:
        """Fetch a single question and return the LLM-safe projection.

        Implementation note (SEC-001):
            `selected_fields` is the load-bearing layer. The string
            ``"correct_answer"`` does not appear in this method. The
            explicit `QuestionView(...)` construction is the second layer:
            the Pydantic model has `extra="forbid"`, so a future widening of
            the projection cannot leak additional fields through this method.

        Returns:
            `QuestionView` containing only `question_id`, `text`, `options`,
            `difficulty` (008-api §1.5.4). All fields are 🟢 (LLM-OK).
        """
        doc = await self._search.get_document(
            key=str(question_id),
            selected_fields=list(QUESTION_VIEW_FIELDS),
        )
        language = str(doc.get("language") or "")
        text = _resolve_text(doc, language)
        options = [Option(**opt) for opt in doc["options"]]
        difficulty: Difficulty = doc["difficulty"]
        return QuestionView(
            question_id=doc["id"],
            text=text,
            options=options,
            difficulty=difficulty,
        )

    async def get_answer_key(self, question_id: QuestionId) -> AnswerKey:
        """Server-only. Never exposed via src/agent/tools.py.

        Called from `submit_answer` only. AST lint (tasks/007 TASK-125) blocks
        any other call site. The returned `AnswerKey` is a frozen dataclass
        with no JSON serializer — even a logging mistake on this path cannot
        leak the key (the `__repr__` is masked).

        Refs: SEC-002, ADR-005, 008-api §3.3.2.
        """
        doc = await self._search.get_document(
            key=str(question_id),
            selected_fields=list(ANSWER_KEY_FIELDS),
        )
        return AnswerKey(
            question_id=doc["id"],
            correct=frozenset(doc["correct_answer"]),
            score_weight=float(doc["score_weight"]),
        )

    async def search_topic(
        self,
        topic: str,
        language: LanguageCode,
        difficulty: Difficulty | None = None,
        top: int = 200,
    ) -> list[LogicalId]:
        """Filtered ID draw for `start_quiz`. Returns per-language doc keys
        (e.g. ``az-net-vpn-001-en``) — these are the values `start_quiz`
        feeds into the shuffle and later passes to `get_question_view`,
        which calls `get_document(key=...)`. The bare `logical_id` (no
        language suffix) is the authoring-side identifier and is NOT a
        valid index key.

        The seeded shuffle (NFR-003, tasks/003 TASK-049) operates on the ID
        list — AI Search never sees the seed and never sorts by it.

        008-api §3.4 query pattern. Refuses unsupported languages (SEC-010).
        """
        if str(language) not in SUPPORTED_LANGUAGES:
            raise ValueError(f"language {language!r} not in allowlist")

        # Build `$filter` server-side so the language predicate is mandatory.
        # A missing language filter would silently cross-contaminate result
        # sets across languages — FR-005 / NFR-011 fail fast.
        filter_clauses = [
            f"topic eq '{topic}'",
            f"language eq '{language}'",
        ]
        if difficulty is not None:
            filter_clauses.append(f"difficulty eq '{difficulty}'")
        filter_expr = " and ".join(filter_clauses)

        results: list[LogicalId] = []
        async for hit in await self._search.search(
            search_text="*",
            filter=filter_expr,
            select=["id"],
            top=top,
        ):
            results.append(LogicalId(hit["id"]))
        return results

    async def facet_topic_language(self) -> dict[str, dict[str, int]]:
        """Two-pass facet query for `(topic, language)` cross-tab counts.

        AI Search's `$facet` is single-field. We enumerate the per-language
        facet then re-query for each (topic, language) pair the index has
        seen; the result is the `{topic: {language: count}}` map used by
        `seed.reconcile_topics` (002 TASK-028).
        """
        # Pass 1: enumerate the topics + languages via single-field facets.
        response = await self._search.search(
            search_text="*",
            facets=["topic,count:1000", "language,count:1000"],
            select=["topic", "language"],
            top=0,
            include_total_count=False,
        )
        topics: set[str] = set()
        languages: set[str] = set()
        facets: dict[str, Any] = {}
        try:
            facets = await response.get_facets()
        except AttributeError:  # pragma: no cover - SDK shape variance
            facets = {}
        for entry in facets.get("topic", []):
            topics.add(str(entry["value"]))
        for entry in facets.get("language", []):
            languages.add(str(entry["value"]))

        # Pass 2: for each (topic, language) ask for the count directly.
        counts: dict[str, dict[str, int]] = {}
        for topic in sorted(topics):
            counts[topic] = {}
            for language in sorted(languages):
                resp = await self._search.search(
                    search_text="*",
                    filter=f"topic eq '{topic}' and language eq '{language}'",
                    top=0,
                    include_total_count=True,
                )
                total = await resp.get_count()
                counts[topic][language] = int(total or 0)
        return counts

    async def list_all_ids(self) -> set[str]:
        """Return every `id` in the index. Used by the reindex diff (TASK-028).

        Caps the request to a generous page size; AI Search returns up to
        `top=1000` per page. For multi-thousand-record indexes the loader
        would page; v1 seed is ≥90 documents so a single page suffices.
        """
        ids: set[str] = set()
        async for hit in await self._search.search(
            search_text="*",
            select=["id"],
            top=1000,
        ):
            ids.add(str(hit["id"]))
        return ids


def build_search_client(
    endpoint: str,
    index_name: str,
    credential: "AsyncTokenCredential",
) -> "SearchClient":
    """Construct a real `SearchClient` for the `questions` index.

    Wrapped here so callers do not import `azure.search.documents.aio`
    directly — the dependency lives in the data layer per the layer map.
    The SDK import is deferred to call time so the module can be loaded in
    test environments without the Azure SDK installed.
    """
    from azure.search.documents.aio import SearchClient  # noqa: PLC0415 - lazy

    return SearchClient(endpoint=endpoint, index_name=index_name, credential=credential)


__all__ = [
    "ANSWER_KEY_FIELDS",
    "QUESTION_VIEW_FIELDS",
    "QuestionSearch",
    "build_search_client",
]
