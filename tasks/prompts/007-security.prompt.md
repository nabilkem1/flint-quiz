# DEV-STORY PROMPT — TASK-007 SECURITY & HARDENING

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 5 — Security Hardening
**Current Task Pack**: 007-security (encode every `SEC-*` ID from the security model into enforced controls — Managed Identity end-to-end, RBAC verification, Key Vault, ISO 639-1 allowlist, answer-leakage defence in depth, prompt-injection testing, Entra ID across channels, retention, idempotency reinforcement, APIM pre-public gate, GDPR right-to-erasure cascade)
**Scope**: All security controls that wrap the data and agent layers. Each control maps to a runtime check, a test, or a CI lint — never just docs.

## TASK REFERENCES

- `tasks/007-security.md`
  - TASK-120 — Managed Identity end-to-end audit (SEC-004, NFR-007)
  - TASK-121 — RBAC scope verification
  - TASK-122 — Key Vault via Managed Identity (SEC-013)
  - TASK-123 — ISO 639-1 language allowlist validator (SEC-010)
  - TASK-124 — Tool-layer answer-leakage enforcement (SEC-001, SEC-002)
  - TASK-125 — Two-method search client enforcement (ADR-005)
  - TASK-126 — Prompt injection test suite (SEC-007)
  - TASK-127 — Entra ID end-to-end (SEC-003)
  - TASK-128 — "What does the LLM see" boundary doc (SEC-009)
  - TASK-129 — APIM rate limiting (optional v1, mandatory pre-public)
  - TASK-130 — Pre-public exposure gate checklist
  - TASK-131 — Idempotency reinforcement (SEC-006, NFR-002)
  - TASK-132 — PII / transcript retention policy (SEC-008)
  - TASK-133 — Audit retention divergence (SEC-014)
  - TASK-134 — GDPR right-to-erasure cascade
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-007 (Key Vault), TASK-010, TASK-011, TASK-012, TASK-013
  - `tasks/002-ai-search.md` TASK-027
  - `tasks/003-cosmos-db.md` TASK-041, TASK-042, TASK-044, TASK-047, TASK-050, TASK-051
  - `tasks/005-tools.md` TASK-082, TASK-084, TASK-088
  - `tasks/008-observability.md` TASK-141, TASK-149 (`agent.user_erased` event)

## SPEC REFERENCES

- `specs/005-security-model.md` — every `SEC-*` ID
- `specs/009-agent-governance.md` — GOV-010, GOV-024, GOV-025, GOV-060, GOV-061, GOV-070, §15 escalation
- `specs/008-api-contracts.md` — §0.1 (🟡/🔴 fields), §2.4 (AuditEvent), §4.5.1 (grading_event dimensions)
- `specs/006-testing-strategy.md` — TEST-006, TEST-007, TEST-018, TEST-023, TEST-028
- `specs/007-operational-runbook.md` — §8.4 (pre-public gate)

## ADR REFERENCES

- `adr/005-tool-boundary-prevents-answer-leakage.md` — the boundary every test in this pack reinforces
- `adr/006-retention-policy.md` — retention windows + GDPR boundary

## GOVERNANCE REFERENCES

- `docs/coding-standards.md` — lint rules, CI grep patterns
- `docs/ai-agent-development-guidelines.md` — boundary discipline
- `docs/secrets.md` — secret list (start empty if v1 needs none)
- `docs/retention.md` — retention windows + GDPR cascade
- `docs/llm-boundary.md` — what the LLM sees vs does not see
- `docs/pre-public-gate.md` — pre-public checklist

## OBJECTIVE

Implement the security layer that:

1. Asserts via CI grep that no connection string, account key, SAS, or `ApiKey=` ever appears in code, env, or Bicep outputs (App Insights connection string is the documented exception).
2. Verifies post-deploy that runtime UAMIs hold no over-broad roles (`Owner`/`Contributor`/`User Access Administrator`); `Search Index Data Contributor` only on `uami-indexer-*`; `Search Service Contributor` only on `uami-deploy-*`.
3. Wraps Key Vault access in `src/data/keyvault_client.py` with `DefaultAzureCredential` + 10-min in-process TTL cache; never writes secrets to disk.
4. Implements the ISO 639-1 allowlist validator (`validate_language`) sourced from AppConfig `languages:supported` with short-TTL cache; used by every tool that accepts a language code.
5. Reinforces the answer-leakage boundary with a layer-spanning test that injects tainted records and asserts clean output, plus an AST check forbidding `get_answer_key` outside `submit_answer`.
6. Runs a multilingual prompt-injection battery (en/fr/es, plain + encoded payloads) that asserts no leaks.
7. Wires Entra ID end-to-end (text + voice), keying Cosmos rows by the Entra-derived user principal; anonymous traffic rejected.
8. Authors `docs/llm-boundary.md` enumerating what the LLM sees vs does not see, with each item pointing to enforcing code + test.
9. Provisions optional APIM rate limiting (off by default in v1, gated on `features:apim`), with per-user quotas (`questions/minute`, `quizzes/day`, `voice-minutes/day`).
10. Encodes the pre-public exposure gate in CI (refuses to tag `public-ready` unless APIM + retention + boundary review + TEST-006/007/011 all pass).
11. Concurrency-tests `submit_answer` idempotency cross-layer (N=20 duplicate calls): exactly one persisted answer, one audit row, one observability event.
12. Configures Cosmos `sessions` TTL (per terminal state) + LAW retention windows; documents in `docs/retention.md`.
13. Documents audit retention divergence — audit > session — with a periodic post-deploy assertion.
14. Implements the GDPR right-to-erasure cascade (`src/data/erasure.py`) — Entra-group-gated; deletes `users` + `sessions`; pseudonymizes `audit` Cosmos rows; acknowledges `audit-archive` immutability; idempotent; emits `audit.user_erased` event (and `agent.user_erased.repeat` on no-op).

## IMPLEMENTATION RULES

- **CI grep (TASK-120)** fails the build on patterns: `AccountKey=`, `AccountEndpoint=...;AccountKey=`, `SharedAccessSignature`, `ApiKey=`, `os.environ["...KEY..."]`, `os.environ["...CONNECTION_STRING..."]`. Exception list (App Insights connection string) documented inline in CI config; exclude `*.md`, `/docs/`.
- **Post-provision RBAC hook (TASK-121)** lists role assignments per UAMI and exits non-zero if any assignment is at subscription scope, or if `Owner`/`Contributor`/`User Access Administrator` is held by a runtime UAMI.
- **Key Vault wrapper (TASK-122)**: thin wrapper around `SecretClient` + `DefaultAzureCredential`; in-process TTL cache 10 min; never logs secret values; never writes to disk. Empty v1 secret list documented in `docs/secrets.md`.
- **Language allowlist (TASK-123)**: `validate_language(code) -> str` raises `InvalidLanguageError` on disallowed; returns lowercased, normalised code on success. Sourced from AppConfig with short-TTL cache. Used by `set_language`, `start_quiz`, `list_topics`, seed loader.
- **Leak-test scope (TASK-124)**: `tests/test_no_answer_leakage.py` runs on every change under `src/agent/tools.py`, `src/data/question_search.py`, `src/agent/quiz_agent.py`. Injects tainted record → asserts clean output. AST check: `get_answer_key` imported only inside the `submit_answer` function body.
- **Search client docstring (TASK-125)**: `get_answer_key` module-level docstring reads verbatim: `"Server-only. Never exposed via src/agent/tools.py."` `import-linter` (or AST grep) rule fails the build on violation.
- **Injection corpus (TASK-126)** covers en/fr/es with plain + encoded payloads (base64, rot13, leet). Each scenario asserts: agent response contains no answer-key strings across all languages; tool returns contain no `correct_answer` field.
- **Entra ID end-to-end (TASK-127)**: Hosted Agent auth uses Entra; user principal flows into agent context and onto `userId` on Cosmos rows. Voice (Realtime) authenticates with Entra-issued tokens. Anonymous traffic rejected.
- **APIM (TASK-129)** is Bicep-provisioned but disabled by default; toggled via AppConfig `features:apim`. Per-user quotas (values in AppConfig). Mandatory before public exposure.
- **Pre-public gate (TASK-130)** is a CI job that refuses to tag `public-ready` unless: APIM quotas active, retention applied to `sessions` + transcripts, LLM-boundary reviewed, TEST-006 + TEST-007 + TEST-011 green.
- **Idempotency reinforcement (TASK-131)**: concurrency test N=20 against real Cosmos (or emulator with `ifMatch` enabled). Asserts exactly one persisted answer, one audit row, one `grading_event`.
- **Retention (TASK-132 / TASK-133)**: Cosmos `sessions` TTL per terminal state (delegated to 003-cosmos-db TASK-050). LAW retention: 30 days transcripts, 90 days grading events. Audit retention > session retention; periodic assertion script.
- **GDPR cascade (TASK-134)** — `src/data/erasure.py` exposes `async def erase_user(user_id: UUID, requested_by: str, ticket_ref: str) -> ErasureReceipt`:
  - **Authorization** via Entra group membership `group:flint-support-erasure`; verified from Entra OID claim, NOT tool args.
  - **Cascade order (each step idempotent, bounded per resource)**:
    1. `users.{userId}` hard-delete (single point delete, partition key `/userId`).
    2. `sessions` rows where partition key = `/userId` hard-delete (cross-row but single-partition).
    3. `audit` Cosmos rows where `userId = <target>`: replace `userId` with `pseudo:v{N}:{sha256(userId, kv_salt)[:16]}`. Salt fetched from Key Vault (`erasure-pseudonym-salt`). `ifMatch(_etag)` on every replace.
    4. `audit-archive` Blob snapshots past their immutability lock retain original `userId` by compliance design — emit `audit.erasure_archive_locked` with locked snapshot IDs; acknowledge GDPR Art. 17(3)(b).
  - **Audit-of-audit event** `audit.user_erased` to App Insights + dedicated Cosmos `audit` partition (with pseudonymized userId), capturing `requested_by`, `ticket_ref`, `timestamp`, counts.
  - **Idempotency**: re-running for an already-erased `user_id` is a no-op; emits one dedup'd `audit.user_erased.repeat` event.
  - **No tool wraps this.** Invoked by support tooling (CLI or API) — not on the agent surface.
  - **Salt versioning** via `pseudo:v1:<hash>`, `pseudo:v2:<hash>` after rotation.

## OUTPUT FILES

Generate:

- `.github/workflows/security-grep.yml` (or extend CI) — runs the credential-pattern grep on every PR
- `infra/hooks/post-provision-rbac.sh` — enumerates RBAC assignments and asserts no over-broad scope
- `src/data/keyvault_client.py` — `SecretClient` wrapper with TTL cache
- `src/data/language_allowlist.py` — `validate_language(code) -> str` + ISO 639-1 normalisation
- `tests/test_no_answer_leakage.py` — tainted-record injection + AST check (TEST-006)
- `tests/test_prompt_injection.py` — multilingual injection battery (gates per-release)
- `tests/integration/test_entra_id_e2e.py` — anonymous traffic rejected; userId flows from Entra
- `tests/integration/test_idempotency_concurrent.py` — N=20 concurrent `submit_answer` (TEST-007 + reinforcement)
- `tests/integration/test_retention_assertions.py` — audit retention > session retention; LAW retention configured
- `tests/integration/test_gdpr_erasure.py` — TEST-028 (cascade, repeat, auth-negative, salt rotation)
- `infra/modules/apim.bicep` — APIM (Consumption tier) + per-user policies, toggled by `features:apim`
- `infra/scripts/import-linter.ini` — rule forbidding `get_answer_key` references outside `submit_answer`
- `infra/scripts/post-deploy-retention-check.sh` — periodic assertion script
- `src/data/erasure.py` — GDPR cascade entry point (`erase_user`)
- `src/data/erasure_cli.py` — support-tooling CLI (Entra-group-gated)
- `docs/llm-boundary.md` — what the LLM sees vs does not see; per-item pointers to enforcing code + test
- `docs/pre-public-gate.md` — checklist + CI gate description
- `docs/secrets.md` — secret list (start empty if v1 needs none)
- `docs/retention.md` — retention windows + GDPR cascade documentation

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **SEC-001 / SEC-002 / ADR-005**: leak test green across en/fr/es with tainted-record injection. AST check rejects `get_answer_key` outside `submit_answer`.
- **SEC-003**: anonymous traffic rejected on both text and voice; userId on Cosmos rows equals Entra OID.
- **SEC-004 / NFR-007**: CI grep finds zero credential patterns; every Azure SDK constructed with `DefaultAzureCredential`.
- **SEC-005**: post-provision RBAC hook prints `OK`; over-broad assignments fail the deploy.
- **SEC-006 / NFR-002**: 20 concurrent `submit_answer` → exactly one persisted answer + one audit row + one `grading_event`.
- **SEC-007**: prompt injection battery (en/fr/es, plain + encoded) produces zero leaks.
- **SEC-008 / SEC-014**: audit retention > session retention; LAW retention configured; periodic assertion green.
- **SEC-009**: `docs/llm-boundary.md` reviewed in security review; every item points to enforcing code + test.
- **SEC-010**: disallowed language codes rejected with clear error; allowlist sourced from AppConfig.
- **SEC-011**: APIM Bicep deployable; pre-public gate refuses to tag `public-ready` without APIM active.
- **SEC-013**: secrets fetched via Key Vault wrapper; no secret material in env, code, or AppConfig.
- **GDPR (TASK-134) / TEST-028**:
  - End-to-end erasure: `users` row 404; all `sessions` rows for user 404; `audit` rows have `userId = pseudo:v1:<hash>`; one `audit.user_erased` event with correct counts.
  - Repeat run: no errors, one `audit.user_erased.repeat` event, no pseudonyms re-applied.
  - Auth-negative: caller without `group:flint-support-erasure` → 403; no state mutated.
  - Salt-rotation: new pseudonyms tagged `pseudo:v2:<hash>` and distinct from v1.

## FORBIDDEN ACTIONS

- Do NOT add a `correct_answer` field to any App Insights event, span attribute, log line, or runtime payload. The boundary spans both code AND telemetry.
- Do NOT bypass the Key Vault wrapper to read secrets directly via `SecretClient`. The wrapper exists to enforce caching + no-disk-write discipline.
- Do NOT cache language allowlist in a parallel constant. Every consumer routes through `validate_language`.
- Do NOT grant `Owner`/`Contributor`/`User Access Administrator` to runtime UAMIs. The RBAC hook rejects.
- Do NOT scope role assignments to the resource group or subscription when a smaller scope is available.
- Do NOT include user PII or transcripts in any App Insights event or span. PII lives only in Cosmos `audit` with RBAC-gated access (analyst/auditor only).
- Do NOT skip the AST check on `get_answer_key`. False positives are cheaper than a real leak.
- Do NOT log raw user utterances to App Insights. Use `received` (normalized key); `receivedRaw` lives in Cosmos `audit` only.
- Do NOT modify `audit-archive` Blob snapshots that have passed their immutability lock. The lock is compliance-required; the cascade emits `audit.erasure_archive_locked` and acknowledges Art. 17(3)(b).
- Do NOT include the GDPR `erase_user` cascade in the agent tool surface. It is support tooling, Entra-group-gated.
- Do NOT execute the GDPR cascade without `group:flint-support-erasure` membership — verified from Entra OID, never trusted from tool args.
- Do NOT pseudonymize the same audit row twice (would corrupt the hash). The cascade is idempotent: rows whose `userId` no longer matches the original `user_id` are skipped.
- Do NOT store the erasure pseudonym salt in code, env, or AppConfig. Key Vault only (`erasure-pseudonym-salt`).
- Do NOT skip salt versioning. Pseudonyms must record their salt version (`pseudo:vN:<hash>`).
- Do NOT bypass the pre-public CI gate. The gate is the load-bearing check for first public traffic.
- Do NOT enable APIM by default in dev. Toggle via `features:apim`; mandatory only before public exposure.
- Do NOT implement Cosmos repository methods, AI Search client methods, agent code, or tool surfaces in this pack. Those live in 002, 003, 004, 005.
