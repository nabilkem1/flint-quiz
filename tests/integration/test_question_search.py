"""Integration tests for the two-method AI Search client (002 TASK-029).

Asserts:

* `language` filter narrows the result set (FR-005, NFR-011).
* `get_question_view` returns a `QuestionView` that **literally has no**
  `correct_answer` field (SEC-001, ADR-005).
* `get_answer_key` returns an `AnswerKey` whose `correct` set matches the
  authored value (SEC-002).
* AST check: in `src/agent/tools.py`, the symbol `get_answer_key` may be
  referenced only inside the body of `submit_answer` (TEST-006).

The tests run against an in-memory fake `SearchClient` rather than the live
service so they can run in CI without provisioning. The real-service
counterpart is covered by `tests/e2e/` (010 task pack).
"""

import ast
import asyncio
import dataclasses
import json
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from src.data.models import AnswerKey, QuestionView
from src.data.question_search import (
    ANSWER_KEY_FIELDS,
    QUESTION_VIEW_FIELDS,
    QuestionSearch,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SEED_ROOT = REPO_ROOT / "src" / "seed" / "questions"


# ---------------------------------------------------------------------------
# In-memory fake of the AI Search client surface we depend on
# ---------------------------------------------------------------------------


class _FakeAsyncIterable:
    def __init__(self, items: list[dict[str, Any]], *, count: int | None = None) -> None:
        self._items = items
        self._count = count if count is not None else len(items)

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def gen() -> AsyncIterator[dict[str, Any]]:
            for item in self._items:
                yield item

        return gen()

    async def get_count(self) -> int:
        return self._count

    async def get_facets(self) -> dict[str, Any]:  # pragma: no cover - unused
        return {}


@dataclasses.dataclass
class FakeSearchClient:
    """Duck-typed `SearchClient` whose state is a dict of pre-loaded documents."""

    documents: dict[str, dict[str, Any]]

    async def get_document(
        self,
        key: str,
        selected_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        if key not in self.documents:
            raise KeyError(key)
        doc = self.documents[key]
        if selected_fields is None:
            return dict(doc)
        return {k: v for k, v in doc.items() if k in selected_fields}

    async def search(
        self,
        search_text: str = "*",
        filter: str | None = None,
        select: list[str] | None = None,
        top: int = 50,
        facets: list[str] | None = None,
        include_total_count: bool = False,
    ) -> _FakeAsyncIterable:
        items = list(self.documents.values())
        if filter:
            items = _apply_filter(items, filter)
        if select:
            items = [{k: v for k, v in d.items() if k in select} for d in items]
        return _FakeAsyncIterable(items[:top], count=len(items))


def _apply_filter(items: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
    """Trivial OData-`eq`-only filter parser sufficient for these tests."""
    clauses = [c.strip() for c in expr.split(" and ")]
    for clause in clauses:
        field, _, value = clause.partition(" eq ")
        field = field.strip()
        value = value.strip().strip("'")
        items = [d for d in items if str(d.get(field)) == value]
    return items


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_seed_to_index() -> dict[str, dict[str, Any]]:
    """Load the authored seed tree into the index document shape."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(SEED_ROOT.rglob("*.json")):
        authored = json.loads(path.read_text(encoding="utf-8"))
        language = authored["language"]
        doc_id = f"{authored['logical_id']}-{language}"
        out[doc_id] = {
            "id": doc_id,
            "logical_id": authored["logical_id"],
            "topic": authored["topic"],
            "language": language,
            f"text_{language}": authored["text"],
            "options": authored["options"],
            "correct_answer": list(authored["correct_answer"]),
            "difficulty": authored["difficulty"],
            "tags": authored["tags"],
            "category": authored["category"],
            f"explanation_{language}": authored["explanation"],
            "score_weight": authored["score_weight"],
        }
    return out


@pytest.fixture
def fake_index() -> FakeSearchClient:
    return FakeSearchClient(documents=_load_seed_to_index())


@pytest.fixture
def search(fake_index: FakeSearchClient) -> QuestionSearch:
    return QuestionSearch(fake_index)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "fr", "es"])
def test_search_topic_language_filter_returns_only_that_language(
    search: QuestionSearch, language: str
) -> None:
    """FR-005 + NFR-011 — `search_topic` MUST refuse to return cross-language IDs."""
    ids = asyncio.run(search.search_topic("azure-networking", language))  # type: ignore[arg-type]
    assert ids, f"no results for language={language}"
    for logical_id in ids:
        assert str(logical_id).startswith("az-net-"), logical_id
    # Cross-language contamination would surface as a duplicate `logical_id`
    # since the document key is `<logical_id>-<lang>`; we strip language
    # already in the projection so dupes here mean cross-contamination.
    assert len(set(ids)) == len(ids), "duplicate logical IDs — cross-language leak"


@pytest.mark.parametrize("question_id", ["az-net-vpn-001-en", "az-net-vpn-001-fr", "az-net-vpn-001-es"])
def test_get_question_view_has_no_correct_answer(
    search: QuestionSearch, question_id: str
) -> None:
    """SEC-001 / ADR-005 — the LLM-safe projection cannot contain the key."""
    view: QuestionView = asyncio.run(search.get_question_view(question_id))  # type: ignore[arg-type]
    serialized = view.model_dump()
    # Property 1: the model has no such field declared.
    assert "correct_answer" not in QuestionView.model_fields
    # Property 2: the projected dict does not carry it.
    assert "correct_answer" not in serialized
    # Property 3: a JSON dump never contains the literal token, in any case.
    payload = view.model_dump_json()
    assert "correct_answer" not in payload
    # Property 4: the projection allowlist literally does not contain the token.
    assert "correct_answer" not in QUESTION_VIEW_FIELDS


def test_get_answer_key_returns_correct_set(search: QuestionSearch) -> None:
    """SEC-002 — `get_answer_key` is the only sanctioned path to the key."""
    ak: AnswerKey = asyncio.run(search.get_answer_key("az-net-vpn-001-en"))  # type: ignore[arg-type]
    assert isinstance(ak, AnswerKey)
    assert ak.correct == frozenset({"B"})
    # No JSON serializer on AnswerKey. Pydantic's `model_dump_json` would
    # exist on a BaseModel; the dataclass has nothing of the kind.
    assert not hasattr(ak, "model_dump_json")
    assert not hasattr(ak, "model_dump")
    assert not hasattr(ak, "__json__")
    # __repr__ masks the correct set so a logging mistake cannot leak it.
    assert "<redacted>" in repr(ak)
    assert "B" not in repr(ak)


def test_answer_key_fields_allowlist_excludes_text_and_options() -> None:
    """The complementary allowlist contract — `get_answer_key` selects only
    the key + scoring weight."""
    assert "correct_answer" in ANSWER_KEY_FIELDS
    assert "text" not in ANSWER_KEY_FIELDS
    assert "options" not in ANSWER_KEY_FIELDS
    assert "explanation" not in ANSWER_KEY_FIELDS


# ---------------------------------------------------------------------------
# AST check (TEST-006 / SEC-001 / TASK-027)
# ---------------------------------------------------------------------------


class _GetAnswerKeyReferenceVisitor(ast.NodeVisitor):
    """Walk a Python AST and record every reference to `get_answer_key`
    annotated with the enclosing-function name (or "<module>" if outside).

    The contract: the only allowed referencing function is `submit_answer`.
    Imports of the symbol at module scope are also allowed (the function
    needs the name to call it).
    """

    def __init__(self) -> None:
        self.references: list[tuple[str, int]] = []
        self._stack: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "get_answer_key":
            scope = self._stack[-1] if self._stack else "<module>"
            self.references.append((scope, node.lineno))

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        # Catch `something.get_answer_key` access too.
        if node.attr == "get_answer_key":
            scope = self._stack[-1] if self._stack else "<module>"
            self.references.append((scope, node.lineno))
        self.generic_visit(node)


def test_get_answer_key_only_referenced_inside_submit_answer() -> None:
    """SEC-001 / TEST-006 — AST check on `src/agent/tools.py`.

    This is the load-bearing test for the 'one canonical security boundary'.
    Any function in `tools.py` (other than `submit_answer`) referencing
    `get_answer_key` would expose the key to the LLM context — the test
    fails the build before that can ship.

    `tools.py` may not exist yet in the 002 task pack (it is authored in 005);
    in that case this test is xfail-skipped with a clear marker so future
    contributors are told it will activate as soon as `tools.py` lands.
    """
    tools_path = REPO_ROOT / "src" / "agent" / "tools.py"
    if not tools_path.exists():
        pytest.xfail(
            f"{tools_path} does not yet exist (authored in 005-tools); AST check "
            "will activate as soon as the file is added"
        )
    tree = ast.parse(tools_path.read_text(encoding="utf-8"))
    visitor = _GetAnswerKeyReferenceVisitor()
    visitor.visit(tree)
    bad = [
        (scope, line)
        for scope, line in visitor.references
        if scope not in ("<module>", "submit_answer")
    ]
    assert not bad, (
        f"`get_answer_key` referenced outside `submit_answer` in {tools_path}: {bad}"
    )


def test_get_answer_key_module_docstring_contract() -> None:
    """The `get_answer_key` method docstring is a contract surface for
    static-analysis tooling (TASK-027). Verify it warns server-only callers."""
    from src.data import question_search

    doc = question_search.QuestionSearch.get_answer_key.__doc__ or ""
    assert "Server-only" in doc, doc
    assert "src/agent/tools.py" in doc, doc
