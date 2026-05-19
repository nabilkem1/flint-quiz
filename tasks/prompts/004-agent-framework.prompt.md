# DEV-STORY PROMPT — TASK-004 MICROSOFT AGENT FRAMEWORK (MAF) AGENT

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 3 — Agent Layer
**Current Task Pack**: 004-agent-framework (the single MAF agent: definition, system prompt, tool registration, dispatcher, prompt-hash, language detection, AgentThread, resumption, channel switch, latency, output cap)
**Scope**: Build the single MAF Python agent for the Hosted Agent runtime. Includes the tool dispatcher (allowlist + concurrency mutex), prompt composition with SHA-256 hash, per-language phrasing blocks, language detection, AgentThread integration, resumption/channel-switch behaviour, latency discipline, and output-token cap. Tool implementation lives in 005; voice specifics in 006.

## TASK REFERENCES

- `tasks/004-agent-framework.md`
  - TASK-060 — `pyproject.toml` dependencies
  - TASK-061 — `quiz_agent.py` skeleton
  - TASK-062 — System prompt + per-language phrasing blocks (en/fr/es)
  - TASK-063 — Tool registration (exactly five tools)
  - TASK-064 — Language detection on first message
  - TASK-065 — Hosted Agent deployment integration
  - TASK-066 — AgentThread integration (ephemeral conversational state)
  - TASK-067 — Resumption behaviour
  - TASK-068 — Channel-switch tolerance
  - TASK-069 — Latency discipline (voice hot path)
  - TASK-070 — Tool dispatcher: allowlist + parallel-call mutex (GOV-010, GOV-012)
  - TASK-071 — Prompt composition + SHA-256 hash + per-turn verification (GOV-001..003)
  - TASK-072 — Output token cap + truncation event (GOV-091)
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-008 (AppConfig), TASK-012 (Hosted Agent)
  - `tasks/003-cosmos-db.md` TASK-048 (state machine), TASK-049 (shuffle)
  - `tasks/005-tools.md` (tool signatures only; bodies live there)

## SPEC REFERENCES

- `specs/002-system-architecture.md` — §6.3 (agent runtime)
- `specs/004-agent-behavior.md` — §3 (tool surface), §6 (normalisation), §7.3 (phrasing), §7.4 (voice), §8 (channel agnostic), §10 (resumption), §11 (latency)
- `specs/008-api-contracts.md` — §0.4 (casing), §1.5.3 (start_quiz wire shape), §1.5.6 (E_SESSION_ACTIVE)
- `specs/009-agent-governance.md` — GOV-001..003, GOV-010, GOV-012, GOV-014, GOV-025, GOV-052, GOV-060, GOV-061, GOV-070, GOV-072, GOV-091, §15 (escalation table)
- `specs/006-testing-strategy.md` — TEST-003, TEST-004, TEST-005, TEST-008, TEST-009, TEST-018, TEST-019, TEST-021, TEST-022, TEST-025

## ADR REFERENCES

- `adr/001-use-microsoft-agent-framework.md` — MAF + Hosted Agent
- `adr/002-single-agent-architecture.md` — one agent for both channels
- `adr/003-use-cosmos-db-for-session-state.md` — Cosmos is durable; thread is ephemeral
- `adr/005-tool-boundary-prevents-answer-leakage.md` — the model never grades

## GOVERNANCE REFERENCES

- `docs/ai-agent-development-guidelines.md` — prompt structure, tool boundaries, grading discipline
- `docs/coding-standards.md` — Python conventions, async patterns, dependency pinning
- `docs/llm-boundary.md` — what the LLM sees vs does not see
- `docs/content-governance.md` — phrasing-block authoring, translation discipline

## OBJECTIVE

Implement the single MAF agent that:

1. Boots from a configurable model deployment (read from AppConfig at construction).
2. Composes its system prompt from four pinned layers (identity / contract / per-language phrasing block / session frame), hashes the composed text with SHA-256, persists the hash on the `SessionDoc`, and verifies the hash on every subsequent tool invocation.
3. Registers **exactly five** tools and routes every model tool-call request through a dispatcher that (a) fails closed on unknown names and (b) serializes concurrent `submit_answer` for the same `(session_id, question_id)`.
4. Per-language phrasing blocks for `en`/`fr`/`es` with every required slot (greeting, ask_topic, frame_question, feedback_correct/incorrect, topic_unavailable_fallback, coverage_gap_consent, score_preview_decline, refusal_off_topic, refusal_answer_key, stay_on_task, results_summary, pass_message, fail_message, idle_reprompt).
5. Detects language on the first message (FR-011), respects the user's persisted preference thereafter, and refuses implicit switches.
6. Uses Foundry `AgentThread` for ephemeral conversational state; persists `thread_id ↔ session_id` mapping on the `SessionDoc`. Cosmos remains the authority for durable state.
7. Supports resumption by `session_id` and seamless channel switching (text ↔ voice) on the same session.
8. Caps per-turn output at 600 tokens; emits `agent.output_truncated` on overflow.
9. Stays within latency discipline on the voice hot path: no Foundry Evaluations, ≤ 1 AI Search call per turn (`start_quiz` may do two), topic catalog cached in-process with TTL.

## IMPLEMENTATION RULES

- **System prompt forbids grading**: the prompt explicitly states "you are a conversational shell; grading is performed by the `submit_answer` tool — never assert correctness yourself" (ADR-005 reinforcement).
- **Exactly five tools** registered: `list_topics`, `set_language`, `start_quiz`, `submit_answer`, `get_results`. A unit test fails the build if a sixth tool is registered.
- **Dispatcher is the only path from MAF tool-call loop to tool bodies.** A CI grep ensures only `dispatch()` may import the tool functions.
- **`ALLOWED_TOOLS` is a frozen constant**; any tool name not in the set is rejected with an `E_INTERNAL`-shaped error envelope and emits `agent.unknown_tool` (no payload beyond the rejected name).
- **Concurrency mutex**: for `submit_answer` only, acquire an in-process asyncio lock keyed `(session_id, question_id)` from a TTL cache (60 s). The first caller proceeds; concurrent callers `await` the same future and receive the same `ToolResult`. (This is the intra-process optimization; cross-process is handled by Cosmos `ifMatch` in 003-cosmos-db TASK-047.)
- **Tool-arg impersonation defense**: dispatcher validates `args.user_id == principal.entra_oid`; mismatch → reject (audit §5.8).
- **Span discipline**: every dispatch records `agent.dispatch.{tool_name}` with `outcome`, `latency_ms`, `cache_hit`. No span attribute named `correct_answer` (CI lint forbids).
- **Prompt composition determinism**: `compose(language, session_frame) -> (rendered_prompt, sha256_hex)` is pure. Layer files are content-addressed at build time via `prompts/MANIFEST.json`. No timestamps, no random elements, no model-generated content in the composed text. Property-tested.
- **Prompt-hash verification on every turn**: dispatcher recomputes `compose` for the current language and asserts equality against `session.promptHash`. Mismatch → emit `agent.prompt_hash_mismatch` (P0), set session `status="Paused"`, page on-call, return a localized "session paused" error.
- **Phrasing block discipline**: phrasing-block files include NO string interpolation. The session frame layer is the only place runtime values appear; everything else is static text.
- **Language detection**: on first turn, if user language unknown, the system prompt instructs the model to infer from utterance and call `set_language(user_id, lang)` with an allowlisted ISO 639-1 code. Low confidence → ask user to confirm. New languages add a phrasing block; no code change.
- **AgentThread is ephemeral**: persist `thread_id` on `SessionDoc`; rehydrate on resume; never rely on the thread for durable state — read from Cosmos every turn.
- **Channel = metadata**, not state. The agent reads durable state from Cosmos on every turn; on channel switch, re-acknowledges the active question without re-issuing it.
- **Output token cap**: configure MAF runtime per-turn output budget to 600 tokens; on truncation, the runtime emits `agent.output_truncated` with `{session_id, channel, language, requested_max, returned}` (no message content). Phrasing blocks budgeted to stay well under 200 tokens for most turns.
- **Latency hot-path discipline**: no Foundry Evaluations, no needless `list_topics` mid-quiz, topic catalog cached in-process with short TTL polling AppConfig, Cosmos point-reads only, AI Search reads filtered + small.
- **`requires-python = ">=3.11"`**; pin `agent-framework>=1.0`, `azure-ai-projects`, `azure-cosmos`, `azure-search-documents`, `azure-identity`, `azure-keyvault-secrets`, `azure-appconfiguration`, `azure-monitor-opentelemetry`, `pydantic>=2.5`.

## OUTPUT FILES

Generate:

- `pyproject.toml` (dependencies + python version pin + tooling config)
- `src/agent/__init__.py`
- `src/agent/quiz_agent.py` — agent factory `create_agent() -> Agent`, dispatcher, tool routing
- `src/agent/prompts/__init__.py`
- `src/agent/prompts/identity.txt` — pinned identity layer
- `src/agent/prompts/contract.txt` — pinned contract layer (grading discipline, tool surface, refusal rules)
- `src/agent/prompts/lang/en.yaml`, `fr.yaml`, `es.yaml` — phrasing blocks per language with every required slot
- `src/agent/prompts/session-frame-template.txt` — runtime-substituted session frame
- `src/agent/prompts/MANIFEST.json` — content hashes of every layer (build-time)
- `src/agent/prompts/compose.py` — `compose(language, session_frame) -> tuple[str, str]`
- `src/agent/language_detection.py` — first-message detection helper
- `src/agent/resumption.py` — `resume_from_session(session_id) -> ResumeContext`
- `src/agent/dispatcher.py` — `dispatch(tool_name, args, principal) -> ToolResult` (allowlist + mutex)
- `src/agent/agent_thread.py` — thread lookup/create + `thread_id ↔ session_id` mapping helpers
- `azure.yaml` update — `quiz-agent` service pointing at `src/agent/`
- `tests/unit/test_tool_registration.py` — asserts exactly five tools
- `tests/unit/test_compose_determinism.py` — same inputs ⇒ same hash; no timestamps in output
- `tests/integration/test_dispatcher_allowlist.py` — unknown tool rejected, event emitted
- `tests/integration/test_dispatcher_mutex.py` — two concurrent `submit_answer` → one tool-body invocation
- `tests/integration/test_prompt_hash_verification.py` — mid-session mutation halts the session P0
- `tests/integration/test_language_resolution.py` — first-message French → `set_language("fr")`
- `tests/integration/test_resumption.py` — disconnect mid-quiz → resume at next unanswered question
- `tests/integration/test_channel_switch.py` — voice → text on same `session_id` preserves state

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **GOV-001..003**: prompt hash deterministic, persisted on `start_quiz`, verified per turn, P0 halt on mismatch.
- **GOV-010**: unknown tool name → rejected + `agent.unknown_tool` event.
- **GOV-012**: concurrent `submit_answer` for same `(session_id, question_id)` → one body invocation.
- **GOV-091**: per-turn output ≤ 600 tokens; truncation event on overflow.
- **NFR-001**: voice tool-call p95 ≤ 300 ms under smoke load; no more than 4 tool calls per turn under normal flow.
- **FR-008 / FR-009**: resumption returns next unanswered question; voice → text switch preserves language and state.
- **FR-010 / FR-011 / FR-014**: language detection + persistence + propagation across turns and channels.
- **TEST-003 / TEST-004 / TEST-005**: smoke quizzes complete end-to-end in `en`/`fr`/`es`.
- **TEST-008 / TEST-009**: resumption and channel-switch scenarios pass.
- **TEST-018**: rendered prompt (all language × channel pairs) passes redaction lint (no answer-key strings, no PII patterns, no secret-shaped strings).
- **TEST-019**: dispatcher allowlist + mutex.
- **TEST-025**: prompt-hash stability + mismatch halt.

## FORBIDDEN ACTIONS

- Do NOT register a sixth tool. The set is exactly: `list_topics`, `set_language`, `start_quiz`, `submit_answer`, `get_results`.
- Do NOT let any code path other than `dispatch()` invoke a tool body. The CI grep enforces.
- Do NOT include grading logic, answer-key fetching, or any reference to `get_answer_key` in `src/agent/`. The model never grades (ADR-005).
- Do NOT include user names, timestamps, random values, or any non-deterministic content in any prompt layer other than the session-frame template. The hash must be deterministic for `compose` to be verifiable.
- Do NOT rely on `AgentThread` for durable state. Read from Cosmos every turn.
- Do NOT silently switch the session language on a code-switched utterance. Implicit switches are rejected; `set_language` must be called explicitly (GOV-024, GOV-027).
- Do NOT emit `agent.unknown_tool` with the tool-call args. Only the rejected name.
- Do NOT include `correct_answer` as a span attribute, log line, event property, or any other observability surface.
- Do NOT bypass the latency hot-path forbidden list: no Foundry Evaluations, no extraneous `list_topics`, no long-running blob reads in tool-call turns.
- Do NOT hard-code the model deployment name, supported languages list, or any AppConfig-sourced value in code. All such values flow through AppConfig with short-TTL cache.
- Do NOT implement tool bodies (`list_topics`, `set_language`, `start_quiz`, `submit_answer`, `get_results`) in this pack — those live in 005-tools.
- Do NOT implement the answer normalizer or TTS-friendly shaper — those live in 005-tools (TASK-086/087).
- Do NOT implement the background sweeper — it lives in 003-cosmos-db TASK-191.
- Do NOT bypass the prompt-hash verification on any tool invocation. Every turn re-computes and compares.
