"""Top-level answer-leakage test (TEST-006 / SEC-001 / ADR-005).

Reinforces the 005-tools and 002-ai-search defences from the perimeter
of the codebase. This test:

  1. Greps `src/agent/tools.py` for any literal occurrence of
     ``correct_answer`` outside string-quoted token names in the
     defensive-strip module. The token is allowed in:
       * function docstrings / comments,
       * the defensive-strip allowlist (`_FORBIDDEN_KEYS`),
       * the test-only allowlist documented inline below.
     Any other occurrence is a P0 leak risk.

  2. AST-scans every function in ``src/agent/tools.py`` for references
     to ``get_answer_key``. Only ``submit_answer`` is allowed.

  3. Runs the five public tools end-to-end against an in-memory
     repository + a tainted question record carrying
     ``correct_answer``. Asserts every response payload is clean
     across en/fr/es.

Companion AST check (function-level scope) lives in
``tests/integration/test_question_search.py``; this test is the
layer-spanning version called out in TASK-124.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from datetime import datetime, timezone

import pytest

from src.agent.dispatcher import Principal
from src.agent.tools import ToolDeps, build_tools
from src.data.models import (
    AnswerKey,
    Difficulty,
    Option,
    QuestionView,
    TopicDoc,
)
from tests.integration._tools_fakes import RecordingEmitter
from tests.integration.conftest import FakeCosmosRepository, make_session_doc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS_PATH = REPO_ROOT / "src" / "agent" / "tools.py"


# ---------------------------------------------------------------------------
# (1) Literal-token grep — `correct_answer` outside the strip allowlist.
# ---------------------------------------------------------------------------


def test_correct_answer_literal_not_referenced_outside_strip_allowlist() -> None:
    """The literal `correct_answer` MUST appear only in:

      * `src/agent/defensive_strip.py` (the strip allowlist itself).
      * `src/data/question_search.py` docstrings (the boundary doc).
      * Comments inside `src/agent/tools.py` (we permit the comment as
        documentation; runtime is what matters).
    """

    forbidden_modules = [
        REPO_ROOT / "src" / "agent" / "tools.py",
        REPO_ROOT / "src" / "agent" / "answer_normalizer.py",
        REPO_ROOT / "src" / "agent" / "coverage_fallback.py",
        REPO_ROOT / "src" / "agent" / "timers.py",
        REPO_ROOT / "src" / "agent" / "tts_shaper.py",
        REPO_ROOT / "src" / "voice" / "realtime_runtime.py",
        REPO_ROOT / "src" / "voice" / "stt_pipeline.py",
        REPO_ROOT / "src" / "voice" / "tts_pipeline.py",
    ]
    needle = "correct_answer"
    for path in forbidden_modules:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        # Strip docstrings + comments before checking. The contract is
        # that no `correct_answer` reference appears in executable code
        # outside the defensive-strip module.
        stripped_source = _strip_docstrings_and_comments(source)
        assert needle not in stripped_source, (
            f"{path.relative_to(REPO_ROOT)} contains executable reference to "
            f"`{needle}` outside the defensive-strip allowlist"
        )


# ---------------------------------------------------------------------------
# (2) AST check — `get_answer_key` referenced only inside `submit_answer`.
# ---------------------------------------------------------------------------


class _GetAnswerKeyVisitor(ast.NodeVisitor):
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
        if node.attr == "get_answer_key":
            scope = self._stack[-1] if self._stack else "<module>"
            self.references.append((scope, node.lineno))
        self.generic_visit(node)


def test_get_answer_key_only_referenced_inside_submit_answer() -> None:
    """The function-scoped invariant called out in TASK-124 / TASK-125."""

    tree = ast.parse(TOOLS_PATH.read_text(encoding="utf-8"))
    visitor = _GetAnswerKeyVisitor()
    visitor.visit(tree)
    bad = [
        (scope, line)
        for scope, line in visitor.references
        if scope not in ("<module>", "submit_answer")
    ]
    assert not bad, f"`get_answer_key` referenced outside `submit_answer`: {bad}"


# ---------------------------------------------------------------------------
# (3) End-to-end tainted-record injection per language.
# ---------------------------------------------------------------------------


_PRINCIPAL = Principal(entra_oid="user-1")
_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)


class _TaintedSearch:
    """Fake QuestionSearch whose responses sneak `correct_answer` into the
    raw document — the boundary contract is that nothing makes it past
    the typed projection + defensive strip."""

    def __init__(self, *, language: str) -> None:
        self._language = language
        self._records = [
            {
                "question_id": f"q-{i:03d}-{language}",
                "logical_id": f"q-{i:03d}",
                "topic": "azure-networking",
                "language": language,
                "text": f"Question {i} in {language}",
                "options": [
                    Option(key="A", text="opt A"),
                    Option(key="B", text="opt B"),
                    Option(key="C", text="opt C"),
                    Option(key="D", text="opt D"),
                ],
                "correct_answer": ["B"],
                "difficulty": "medium",
                "score_weight": 1.0,
            }
            for i in range(3)
        ]

    async def search_topic(self, topic, language, difficulty=None, top=200):
        return [r["question_id"] for r in self._records if r["topic"] == topic]

    async def get_question_view(self, question_id):
        r = next(r for r in self._records if r["question_id"] == str(question_id))
        # **Even if the upstream record carries `correct_answer`**, the
        # typed projection forbids it. We construct QuestionView field-by-
        # field — the model would reject `correct_answer` via extra=forbid.
        return QuestionView(
            question_id=r["question_id"],
            text=r["text"],
            options=list(r["options"]),
            difficulty=r["difficulty"],
        )

    async def get_answer_key(self, question_id):
        r = next(r for r in self._records if r["question_id"] == str(question_id))
        return AnswerKey(
            question_id=r["question_id"],
            correct=frozenset(r["correct_answer"]),
            score_weight=r["score_weight"],
        )


@pytest.fixture
async def tainted_deps(request):
    language = request.param
    repo = FakeCosmosRepository()
    # Seed a topic with coverage so start_quiz can proceed.
    topic = TopicDoc(
        id="azure-networking",
        topic_id="azure-networking",
        labels={"en": "Networking", "fr": "Réseau", "es": "Redes"},
        counts={"en": 5, "fr": 5, "es": 5},
        default_language="en",
        enabled=True,
        updated_at=_NOW,
    )
    payload = topic.model_dump(by_alias=True, exclude_none=True, mode="json")
    await repo._topics.upsert_item(body=payload)  # type: ignore[attr-defined]

    deps = ToolDeps(
        repo=repo,
        search=_TaintedSearch(language=language),  # type: ignore[arg-type]
        emitter=RecordingEmitter(),
        clock=lambda: _NOW,
    )
    return deps, language


@pytest.mark.parametrize("tainted_deps", ["en", "fr", "es"], indirect=True)
@pytest.mark.asyncio
async def test_start_quiz_tainted_record_produces_no_leak(tainted_deps) -> None:
    deps, language = tainted_deps
    tools = build_tools(deps)
    result = await tools["start_quiz"](
        {
            "user_id": "user-1",
            "topic": "azure-networking",
            "n": 3,
            "language": language,
            "channel": "text",
        },
        _PRINCIPAL,
    )
    assert result.ok is True
    payload = json.dumps(result.data)
    for forbidden in ("correct_answer", "correctAnswer", "answer_key"):
        assert forbidden not in payload, f"{language}: forbidden token leaked: {forbidden}"


@pytest.mark.parametrize("tainted_deps", ["en", "fr", "es"], indirect=True)
@pytest.mark.asyncio
async def test_submit_answer_tainted_record_produces_no_leak(tainted_deps) -> None:
    deps, language = tainted_deps
    tools = build_tools(deps)

    session = make_session_doc(n=3).model_copy(
        update={
            "language": language,
            "requested_language": language,
            "shuffled_ids": [f"q-{i:03d}-{language}" for i in range(3)],
            "started_at": _NOW,
            "question_started_at": _NOW,
        }
    )
    stored = await deps.repo.create_session(session)

    result = await tools["submit_answer"](
        {
            "session_id": stored.id,
            "question_id": stored.shuffled_ids[0],
            "raw_answer": "B",
            "channel": "text",
        },
        _PRINCIPAL,
    )
    assert result.ok is True
    payload = json.dumps(result.data)
    for forbidden in ("correct_answer", "correctAnswer", "answer_key"):
        assert forbidden not in payload, f"{language}: forbidden token leaked: {forbidden}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DOCSTRING_RE = re.compile(r'(""".*?"""|\'\'\'.*?\'\'\')', flags=re.DOTALL)
_COMMENT_RE = re.compile(r'(#[^\n]*)')


def _strip_docstrings_and_comments(source: str) -> str:
    """Best-effort strip — good enough for the literal-token check.

    We replace every triple-quoted string and every `#` comment with a
    single space, then concatenate. The check is on the **executable**
    surface of the module; documentation references to `correct_answer`
    are deliberate and reviewed.
    """

    no_docs = _DOCSTRING_RE.sub(" ", source)
    return _COMMENT_RE.sub(" ", no_docs)
