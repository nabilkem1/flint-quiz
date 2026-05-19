# DEV-STORY PROMPT — TASK-006 VOICE (Foundry Realtime API)

## IMPLEMENTATION CONTEXT

**Current Phase**: Phase 4 — Voice Layer
**Current Task Pack**: 006-voice-realtime (wire the voice channel on the same agent instance: Realtime endpoint, per-language voices, STT/TTS, voice answer normalization, latency budget enforcement, voice session length cap, voice-specific observability dashboard)
**Scope**: Wire voice as a second entry point to the same `QuizAgent`. No second codebase. Channel = metadata. Durable state stays in Cosmos.

## TASK REFERENCES

- `tasks/006-voice-realtime.md`
  - TASK-100 — Foundry Realtime endpoint wiring
  - TASK-101 — Per-language voice config
  - TASK-102 — STT streaming integration
  - TASK-103 — TTS streaming integration
  - TASK-104 — Voice answer normalization
  - TASK-105 — Voice session length cap + two-stage dead-air handling
  - TASK-106 — Channel-switch state preservation
  - TASK-107 — Voice latency budget enforcement (NFR-001)
  - TASK-108 — TTS-friendly enforcement (voice channel)
  - TASK-109 — Voice-specific dashboard
- Cross-pack dependencies:
  - `tasks/001-infrastructure.md` TASK-013 (Realtime endpoint provisioned)
  - `tasks/004-agent-framework.md` TASK-065 (Hosted Agent deploy), TASK-068 (channel-switch tolerance), TASK-069 (latency discipline)
  - `tasks/005-tools.md` TASK-086 (answer normalizer), TASK-087 (TTS shaper)
  - `tasks/003-cosmos-db.md` TASK-048 (state machine)
  - `tasks/008-observability.md` TASK-140 (App Insights), TASK-107-side (latency alerts)

## SPEC REFERENCES

- `specs/002-system-architecture.md` — §9 (voice channel)
- `specs/004-agent-behavior.md` — §6 (normalisation), §7.4 (voice-specific phrasing), §8 (channel agnostic), §11 (latency)
- `specs/008-api-contracts.md` — channel propagation in events
- `specs/009-agent-governance.md` — GOV-014 (voice idle reprompts)
- `specs/006-testing-strategy.md` — TEST-005, TEST-009

## ADR REFERENCES

- `adr/001-use-microsoft-agent-framework.md` — MAF agent serves both channels
- `adr/003-use-cosmos-db-for-session-state.md` — channel = metadata; durable state in Cosmos

## GOVERNANCE REFERENCES

- `docs/ai-agent-development-guidelines.md` — single agent across channels, no separate voice codebase
- `docs/coding-standards.md` — Python async, streaming patterns
- `docs/llm-boundary.md` — voice surface does not bypass any boundary

## OBJECTIVE

Wire the voice channel so that:

1. The Foundry Realtime endpoint (provisioned in 001 TASK-013) reaches the same `QuizAgent` instance the Playground (text) uses. No second codebase. No duplicate tool registration.
2. Per-language voices configured via AppConfig (`voices:en=nova`, `voices:fr=alloy`, `voices:es=alloy`). Session voice matches `session.language`.
3. STT streaming delivers per-turn final transcripts (with confidence scores) into the agent's normal turn loop. Partial transcripts never reach tools.
4. TTS streaming pipes agent text into the configured voice; markdown stripped defensively before TTS.
5. Voice answer normalisation re-uses 005-tools TASK-086, extended with voice fillers per language ("um", "uh", "euh", "este").
6. Voice session length cap (default 30 min, AppConfig `voice:maxSessionMinutes`) prevents runaway Realtime billing. Two-stage dead-air handling (GOV-014): first idle (`voice:idleReprompSeconds=30s`) → re-prompt in active language; second idle (`voice:idleCloseSeconds=60s` cumulative) → close. State preserved in Cosmos.
7. Channel switch (text ↔ voice) on the same `session_id` preserves state and language. The agent re-acknowledges the active question without re-issuing it.
8. Voice latency hot-path discipline: ≤ 300 ms p95 tool-call round-trip. Forbidden in voice hot path: Foundry Evaluations, >1 AI Search call per turn (`start_quiz` may do 2), long-running blob reads.
9. TTS-friendly defensive stripper runs in the Realtime pipeline as the last line of defense: `*`, `` ` ``, `#`, raw URLs stripped pre-TTS; warning emitted if it had to act.
10. Voice-specific App Insights workbook visualises STT first-final latency, TTS first-byte latency, voice tool-call round-trip (p50/p95/p99), per-language counts. Alert on voice p95 > 300 ms for 5 min.

## IMPLEMENTATION RULES

- **One agent, two channels.** No duplicate codebase. The Realtime endpoint registers the same agent that serves the Playground.
- **STT finals only reach the agent.** Partial transcripts may be observed for UX (interim captions) but never feed `submit_answer`.
- **Session language drives the voice.** Voice is selected at session start (and on resume) by reading `session.language` from Cosmos. Brief code-switched utterances do NOT flip the voice or session language (GOV-027).
- **Voice answer normalisation** delegates to `src/agent/answer_normalizer.py` (005-tools TASK-086). Voice-specific pre-processing (filler-strip per language) is added there, not duplicated here.
- **Channel field on `grading_event`** is `text` | `voice` and is set per submission. 008-observability TASK-141 owns the event; this pack ensures the channel dimension is populated correctly.
- **Session length cap**:
  - Read `voice:maxSessionMinutes` from AppConfig (default 30).
  - On exceed → graceful close. Agent says farewell in session language; state preserved in Cosmos; user resumes in text or new voice session.
  - Two-stage idle (GOV-014):
    - 30 s silence (`voice:idleReprompSeconds`): agent re-prompts with `idle_reprompt` phrasing slot.
    - 60 s cumulative silence (`voice:idleCloseSeconds`): close connection. State intact.
- **Channel switch**:
  - Agent loads state from Cosmos every turn — channel is metadata, never state.
  - On a new connection for an existing `session_id`, greet in persisted language; restate the current question (do not re-issue, re-acknowledge).
  - `grading_event` channel field updated per submission.
- **Voice latency hot path** — forbidden activities codified in code review checklist + lint:
  - No Foundry Evaluations in voice tool calls.
  - No more than one AI Search call per turn (`start_quiz` may do two: one for IDs, one for Q1 fetch — accepted).
  - No long-running blob reads.
- **Defensive TTS strip** runs in the Realtime client pipeline before audio synthesis:
  - Removes `*`, `**`, `` ` ``, `#`, raw `http://` / `https://` URLs.
  - Emits a warning to App Insights when the stripper had to act — surface to remediate the tool that emitted markdown.
- **AppConfig keys** (added by 001 if not already): `voices:en`, `voices:fr`, `voices:es`, `voice:maxSessionMinutes`, `voice:idleReprompSeconds`, `voice:idleCloseSeconds`.
- **Realtime endpoint regional pinning** is handled in 001 TASK-003 / TASK-013; verify the agent connects to the correct region in deployment validation.

## OUTPUT FILES

Generate:

- `src/voice/realtime_runtime.py` — Realtime client wiring, channel registration, voice selection per session
- `src/voice/stt_pipeline.py` — STT final-transcript routing into the agent loop (partials excluded)
- `src/voice/tts_pipeline.py` — TTS streaming; defensive markdown strip before synthesis
- `src/voice/idle_handler.py` — two-stage dead-air handler (re-prompt at 30 s, close at 60 s cumulative)
- `src/voice/session_cap.py` — `voice:maxSessionMinutes` enforcement with graceful close
- `src/agent/answer_normalizer.py` — **extend** with voice-filler pre-processing per language (do not duplicate the module)
- `infra/modules/observability/workbooks/voice-hot-path.bicep` — App Insights workbook "Quiz Voice — Hot Path"
- `infra/modules/observability/alerts/voice-latency.bicep` — alert: voice tool-call p95 > 300 ms over 5-min window
- `tests/integration/test_voice_smoke_es.py` — TEST-005 happy path (Spanish voice end-to-end)
- `tests/integration/test_voice_idle.py` — 30 s re-prompt + 60 s close + state preservation
- `tests/integration/test_voice_session_cap.py` — 31-minute session terminates cleanly with state intact
- `tests/integration/test_channel_switch.py` (extend from 004 if exists) — start in voice, finish in text, same `session_id`
- `tests/integration/test_tts_strip.py` — tainted output → markdown stripped + warning fired
- `docs/voice.md` (or extend `docs/playground.md`) — voice connection instructions, region notes, voice-per-language table

## VALIDATION REQUIREMENTS

Implementation must satisfy:

- **FR-007 / FR-009**: voice channel reaches the same `QuizAgent`; channel switch on the same `session_id` is seamless.
- **NFR-001**: voice tool-call p95 ≤ 300 ms under smoke load (TEST-005).
- **NFR-013**: voice session length cap enforced; two-stage dead-air handling closes idle sessions without state loss.
- **NFR-014**: voice output contains no markdown, no raw URLs; option keys framed per language.
- **GOV-014**: 30 s idle reprompt in active language; 60 s cumulative idle → close.
- **TEST-005**: Spanish voice quiz completes end-to-end; voice dashboard reports tool-call p95 within budget.
- **TEST-009**: voice → text channel switch on same `session_id` returns the next unanswered question in the persisted language.
- **Workbook**: voice hot-path workbook populates within 10 minutes of first voice turn.
- **Alert**: synthetic latency spike fires the voice alert.

## FORBIDDEN ACTIONS

- Do NOT create a second codebase or a separate agent for voice. The same `QuizAgent` instance serves both channels.
- Do NOT register tools on the voice channel separately. Tool registration happens once in 004-agent-framework TASK-063.
- Do NOT trust STT partial transcripts. Only finals reach the agent's turn loop / `submit_answer`.
- Do NOT flip the session language on a brief code-switched utterance. `set_language` is the only path to a language change (GOV-027).
- Do NOT include markdown (`*`, `**`, `` ` ``, `#`) in any TTS-bound string. The TTS defensive strip is the LAST line of defense — fix at source when the warning fires.
- Do NOT include raw URLs in TTS-bound strings. Use the phonetic-safe form from 005-tools TASK-087.
- Do NOT bypass the voice session length cap. Realtime per-minute billing is real money; runaway sessions are an incident.
- Do NOT close idle sessions without preserving state. The next `submit_answer` for the same `session_id` must succeed.
- Do NOT add Foundry Evaluations to the voice hot path. They are gates at publish time (009-testing TASK-167), not runtime.
- Do NOT exceed one AI Search call per voice turn (`start_quiz` may do two; everything else is budgeted to one).
- Do NOT duplicate the answer normalizer. Extend `src/agent/answer_normalizer.py` with voice-filler pre-processing in the same module.
- Do NOT include user PII or transcripts in the voice dashboard. The dashboard surfaces latencies and counts; transcripts live in App Insights only with the retention configured in 007-security TASK-132.
- Do NOT implement the prompt-injection corpus or the leak test in this pack — those live in 007 and 009.
- Do NOT provision the Realtime endpoint resource here. It is provisioned in 001 TASK-013; this pack wires the agent to it.
