# Flint Quiz — Test Suite

This directory carries the **load-bearing structure** for the agent's contracts. Tests are not optional documentation; they are the runtime enforcement of every `SEC-*`, `GOV-*`, `NFR-*` rule the spec declares.

**Pack**: `tasks/009-testing.md` (this pack). **Owner**: QA + Security.

---

## 1. Layout

```
tests/
├── conftest.py                    # session-wide fixtures (lang matrix, cosmos gate, injection corpus)
├── README.md                      # this file
├── unit/                          # pure-function tests (T0)
├── integration/                   # in-process integration (T1/T2)
│   └── conftest.py                # FakeCosmosRepository + emulator gate
├── smoke/                         # in-process end-to-end (TEST-003/004/005)
├── eval/                          # per-language correctness gate (TEST-011)
├── fixtures/                      # YAML corpora (injection)
└── test_*.py                      # top-level spec-anchored entry points
```

Top-level `test_*.py` files are the CI-pipeline entry points — they name each `TEST-NNN` ID exactly once. Integration-tier tests carry the broader matrix.

---

## 2. Verification ID → Test File → Governance ID

| Test ID  | File                                                        | Governance / Spec                                 | Pipeline tier |
|----------|--------------------------------------------------------------|---------------------------------------------------|---------------|
| TEST-003 | `tests/smoke/test_text_en.py`                                | FR-006, NFR-014                                   | T0 + T2/T6    |
| TEST-004 | `tests/smoke/test_text_fr.py`                                | FR-005, FR-006, NFR-014                           | T0 + T2/T6    |
| TEST-005 | `tests/smoke/test_voice_es.py`                               | FR-007, NFR-001, NFR-014                          | T0 + T2/T6    |
| TEST-006 | `tests/test_no_answer_leakage.py`                            | SEC-001, SEC-002, ADR-005                         | T0 PR         |
| TEST-007 | `tests/test_idempotency.py` + `tests/integration/test_conditional_write.py` | SEC-006, NFR-002, ADR-003                  | T1/T2 merge   |
| TEST-008 | `tests/test_resumption.py`                                   | FR-008                                            | T1/T2 merge   |
| TEST-009 | `tests/test_channel_switch.py`                               | FR-009                                            | T1/T2 merge   |
| TEST-010 | `tests/test_observability.py`                                | NFR-009, SEC-014                                  | T1/T2 merge   |
| TEST-011 | `tests/eval/test_foundry_evaluation.py`                      | NFR-010                                           | T2/T6 release |
| TEST-018 | `tests/test_prompt_redaction.py`                             | GOV-005, GOV-070                                  | T0 PR         |
| TEST-019 | `tests/test_tool_allowlist.py` + `tests/integration/test_dispatcher_*.py` | GOV-010, GOV-012                          | T0 PR + T1/T2 |
| TEST-020 | `tests/test_explanation_provenance.py`                       | GOV-031                                           | T0 PR         |
| TEST-021 | `tests/test_refusal_localization.py`                         | GOV-071, GOV-072                                  | T0 PR         |
| TEST-022 | `tests/test_coverage_consent.py`                             | GOV-024, GOV-025                                  | T1/T2 merge   |
| TEST-023 | `tests/test_injection_corpus.py` + `tests/test_prompt_injection.py` | GOV-060, GOV-061, SEC-007                      | T2/T6 release |
| TEST-024 | `tests/test_tts_invariants.py`                               | GOV-050, NFR-014                                  | T0 PR         |
| TEST-025 | `tests/test_prompt_hash.py` + `tests/integration/test_prompt_hash_verification.py` | GOV-003                                  | T2/T6 release |
| TEST-026 | `tests/test_session_state_machine.py` + `tests/integration/test_state_machine.py` | 008-api §4.3                                | T1/T2 merge   |
| TEST-027 | `tests/test_timers.py` + `tests/integration/test_timers.py` | FR-015, NFR-004                                    | T1/T2 merge   |
| TEST-028 | `tests/test_gdpr_erasure.py` + `tests/integration/test_gdpr_erasure.py` | SEC-008, ADR-006                              | T2/T6 release |
| TEST-029 | `tests/integration/test_grading_event_emission.py`           | NFR-009, SEC-014                                  | T1/T2 merge   |
| TEST-030 | `tests/integration/test_no_pii_in_telemetry.py`              | SEC-001 in telemetry                              | T0 PR + T1/T2 |

---

## 3. Pipeline Tiers

CI orchestration lives under `.github/workflows/`:

| Workflow            | Trigger                  | What it runs                                                                                   |
|---------------------|--------------------------|-------------------------------------------------------------------------------------------------|
| `security-grep.yml` | every PR + push to main  | Credential-pattern grep (007 TASK-120).                                                        |
| `ci-pr.yml`         | every PR                 | Unit + TEST-006/018/019/020/021/024 + grading + multilingual + smoke (in-process).             |
| `ci-merge.yml`      | push to main             | TEST-007 (real Cosmos) + TEST-010 + TEST-022 + TEST-026 + TEST-027 + dispatcher mutex.        |
| `ci-release.yml`    | tag `v*` + nightly cron  | Full smoke matrix + TEST-011 (per-language gate) + TEST-023 + TEST-025 + TEST-028 + pre-public.|

Each `TEST-*` ID lives in **exactly one** primary tier (FORBIDDEN ACTIONS). Some IDs also have a complementary integration-tier counterpart that runs on the merge pipeline.

---

## 4. Conventions

* **Multilingual matrix**: parametrise against `supported_languages` (fixture). Hard-coded `["en", "fr", "es"]` is acceptable as a sanity check but never as the load-bearing matrix (FORBIDDEN ACTIONS in TASK-009).
* **Async tests**: use `pytest.mark.asyncio` (configured globally via `asyncio_mode = "auto"`).
* **Real-Cosmos tests**: gate on `cosmos_emulator_available()` so they skip cleanly locally and run end-to-end in CI (which provisions the emulator).
* **GDPR cascade**: NEVER mock the cascade itself; use the in-memory `FakeErasureRepo` from `tests/integration/test_gdpr_erasure.py`, or the real Cosmos emulator when available.
* **Fixtures with answer keys**: synthesised opaque values (e.g., the fake search records). Real seed answer values live in `src/seed/questions/` and are read via the canonical `AnswerKey` channel.

---

## 5. Adding a Test

1. Pick the `TEST-NNN` ID from `specs/006-testing-strategy.md §1`.
2. Place the file at the top-level `tests/` if it is the CI-pipeline entry; under `tests/integration/` if it requires the in-process fakes; under `tests/unit/` if it is pure-function.
3. Map the new ID into the table in §2.
4. Add it to the pipeline tier in §3 and the matching `.github/workflows/*.yml`.
5. **Each ID lives in exactly one primary tier.** Reinforcement in another tier is fine; double-registration as the primary path is not.

---

## 6. Local Run

```bash
# Full suite (skips real-Cosmos tests without emulator).
pytest tests/ -q

# PR tier only.
pytest tests/unit tests/test_no_answer_leakage.py tests/test_prompt_redaction.py \
       tests/test_tool_allowlist.py tests/test_explanation_provenance.py \
       tests/test_refusal_localization.py tests/test_tts_invariants.py \
       tests/test_grading.py tests/test_language_resolution.py \
       tests/test_multilingual_matrix.py tests/test_voice_normalization.py \
       tests/test_negative_scenarios.py tests/smoke -q

# Single TEST-NNN.
pytest tests/test_no_answer_leakage.py -q   # TEST-006
pytest tests/test_idempotency.py -q          # TEST-007
pytest tests/test_gdpr_erasure.py -q         # TEST-028
```

---

## 7. Re-baselining (TEST-011)

The per-language correctness baseline lives at
`tests/eval/baselines/correctness-baseline.json`. Re-baseline **only**:

* Yearly, OR
* On a model upgrade (Foundry / OpenAI catalog change),

with **explicit review** from Security + Content (FORBIDDEN ACTIONS in TASK-009). Silent re-baselining defeats the gate.
