"""Top-level conftest (TASK-175 / TASK-173).

Shared fixtures for the 009-testing pack:

  * :func:`supported_languages` — the live AppConfig
    ``languages:supported`` allowlist (falls back to the build-time
    constant when AppConfig isn't reachable). Used by the multilingual
    matrix so adding a language at runtime surfaces a per-language
    column in CI without code change.

  * :func:`requires_cosmos_emulator` — pytest marker that skips real-
    Cosmos tests when ``COSMOS_EMULATOR_ENDPOINT`` is unset. Mirrors
    the same gate in ``tests/integration/conftest.py``.

  * :func:`injection_corpus` — YAML fixture loader for TEST-023's
    adversarial corpus (``tests/fixtures/injection_corpus.yaml``).

  * :func:`anyio_backend` — pins async tests to ``asyncio`` so we do
    not depend on ``pytest-anyio``.
"""

from __future__ import annotations

import os
import pathlib
from typing import Any

import pytest

from src.data.models import SUPPORTED_LANGUAGES

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"

# ---------------------------------------------------------------------------
# Multilingual matrix fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def supported_languages() -> tuple[str, ...]:
    """Return the live language allowlist, ordered.

    Priority:
      1. Env var ``FLINT_SUPPORTED_LANGUAGES`` (CSV) — lets CI override
         per pipeline tier.
      2. AppConfig ``languages:supported`` (when wired via
         :func:`src.data.language_allowlist.configure`).
      3. Build-time constant :data:`src.data.models.SUPPORTED_LANGUAGES`.

    The matrix parametrises against this fixture, NOT against a
    hard-coded constant in each test file (FORBIDDEN ACTIONS).
    """

    env_override = os.environ.get("FLINT_SUPPORTED_LANGUAGES")
    if env_override:
        return tuple(
            sorted({code.strip().lower() for code in env_override.split(",") if code.strip()})
        )

    try:
        from src.data.language_allowlist import current_allowlist

        live = current_allowlist()
        if live:
            return tuple(sorted(live))
    except Exception:
        pass

    return tuple(sorted(SUPPORTED_LANGUAGES))


# ---------------------------------------------------------------------------
# Real-Cosmos gate
# ---------------------------------------------------------------------------


def cosmos_emulator_available() -> bool:
    return bool(
        os.environ.get("COSMOS_EMULATOR_ENDPOINT")
        or os.environ.get("COSMOS_TEST_ENDPOINT")
    )


requires_cosmos_emulator = pytest.mark.skipif(
    not cosmos_emulator_available(),
    reason=(
        "set COSMOS_EMULATOR_ENDPOINT or COSMOS_TEST_ENDPOINT for tests that "
        "must exercise the real `ifMatch` etag concurrency primitive"
    ),
)


# ---------------------------------------------------------------------------
# Injection corpus loader (TEST-023)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def injection_corpus() -> list[dict[str, Any]]:
    """Load the multilingual injection corpus from YAML.

    Each row: ``{id, language, encoding, payload, expected_response_class}``.
    Missing file → empty list (the test that depends on it skips).
    """

    import yaml  # local import: keeps the fixture cheap when unused

    path = FIXTURES_DIR / "injection_corpus.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("payloads", []))


# ---------------------------------------------------------------------------
# Async backend pin
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
