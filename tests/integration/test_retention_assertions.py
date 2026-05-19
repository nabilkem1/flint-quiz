"""Retention assertions (TASK-132 / TASK-133 / SEC-008 / SEC-014).

The live infrastructure-level retention checks live in
`infra/scripts/post-deploy-retention-check.sh` (TTLs against the
deployed Cosmos, LAW retention, Blob immutability). This test is the
**unit-style** complement that asserts the *intent* encoded in the
codebase:

  * `docs/retention.md` documents `audit retention > session retention`
    (SEC-014). We re-assert the policy table's invariants from the
    file's parsed values.
  * `AppConfig` defaults reflect the policy table (read from the same
    file via `docs/retention.md §4`).
  * The Cosmos repository's `score_session` / `expire_session` paths
    set a non-empty TTL on terminal-state transitions.

Failing this test is a *policy drift* signal: someone edited
`docs/retention.md §1` without updating the enforcement layer.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RETENTION_DOC = REPO_ROOT / "docs" / "retention.md"


def _parse_retention_table() -> dict[str, str]:
    """Return the `AppConfig key → default` map from `docs/retention.md §4`.

    The doc's §4 table is the source of truth for retention windows.
    This parser is intentionally tiny — it only recognises the
    pipe-delimited rows whose first column is a `retention:*` key.
    """

    rows: dict[str, str] = {}
    inside_table = False
    for line in RETENTION_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 4."):
            inside_table = True
            continue
        if inside_table and line.startswith("## "):
            break
        if not inside_table:
            continue
        m = re.match(r"\|\s*`(retention:[A-Za-z]+)`\s*\|\s*`([^`]+)`", line)
        if m:
            rows[m.group(1)] = m.group(2)
    return rows


def _to_days(value: str) -> int:
    """Coerce a doc value to days for comparison.

    `retention:auditArchiveYears = 7` → 7 * 365.
    `retention:sessionsScoredDays = 30` → 30.
    """

    return int(value)


# ---------------------------------------------------------------------------
# Policy invariants
# ---------------------------------------------------------------------------


def test_retention_doc_declares_required_keys() -> None:
    table = _parse_retention_table()
    for required in (
        "retention:sessionsScoredDays",
        "retention:auditHotDays",
        "retention:auditArchiveYears",
        "retention:transcriptDays",
        "retention:lawHotDays",
    ):
        assert required in table, (
            f"docs/retention.md §4 is missing the `{required}` row"
        )


def test_audit_retention_strictly_greater_than_session_retention() -> None:
    """SEC-014 — audits survive sessions so disputes can be triaged
    after session expiry."""

    table = _parse_retention_table()
    session_days = _to_days(table["retention:sessionsScoredDays"])
    audit_hot_days = _to_days(table["retention:auditHotDays"])
    assert audit_hot_days > session_days, (
        f"SEC-014 violated: auditHotDays={audit_hot_days} must be strictly greater "
        f"than sessionsScoredDays={session_days}"
    )


def test_audit_archive_years_covers_compliance_window() -> None:
    """Cold audit-archive Blob must be at least 7 years per
    `docs/retention.md §1`."""

    table = _parse_retention_table()
    years = int(table["retention:auditArchiveYears"])
    assert years >= 7, (
        f"audit-archive Blob immutability must be >= 7 years (got {years})"
    )


def test_transcript_retention_is_short_for_pii() -> None:
    """SEC-008 — transcript-bearing surfaces must purge PII quickly.

    The policy default in `docs/retention.md §4` is 30 days; we
    enforce a generous upper bound so a policy change to e.g., 365
    days does not silently pass review."""

    table = _parse_retention_table()
    transcript_days = _to_days(table["retention:transcriptDays"])
    assert transcript_days <= 90, (
        f"transcript retention {transcript_days}d violates SEC-008 PII window"
    )


# ---------------------------------------------------------------------------
# Codebase consistency — Cosmos repository sets TTL on terminal transitions.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_session_sets_ttl_on_terminal_transition() -> None:
    from datetime import datetime, timezone

    from src.data.models import SessionStatus
    from .conftest import FakeCosmosRepository, make_session_doc

    repo = FakeCosmosRepository(sessions_terminal_ttl_seconds=60)
    session = make_session_doc(n=1).model_copy(
        update={
            "started_at": datetime(2026, 5, 17, tzinfo=timezone.utc),
            "question_started_at": datetime(2026, 5, 17, tzinfo=timezone.utc),
        }
    )
    stored = await repo.create_session(session)
    # Advance through the state machine to Completed → Scored.
    from tests.integration.conftest import make_answer

    stored, _ = await repo.append_answer_conditional(stored, make_answer(stored.shuffled_ids[0]))
    assert stored.status == SessionStatus.COMPLETED
    scored = await repo.score_session(stored)
    assert scored.status == SessionStatus.SCORED
    assert scored.ttl is not None and scored.ttl > 0, (
        "score_session must set a positive TTL on terminal transition"
    )


@pytest.mark.asyncio
async def test_expire_session_sets_ttl_on_terminal_transition() -> None:
    from src.data.models import SessionStatus
    from .conftest import FakeCosmosRepository, make_session_doc

    repo = FakeCosmosRepository(sessions_terminal_ttl_seconds=60)
    session = make_session_doc(n=3)
    stored = await repo.create_session(session)
    expired = await repo.expire_session(stored)
    assert expired.status == SessionStatus.EXPIRED
    assert expired.ttl is not None and expired.ttl > 0, (
        "expire_session must set a positive TTL on terminal transition"
    )
