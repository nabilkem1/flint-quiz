# Voice Channel — Flint Quiz

**Purpose**: How the voice channel reaches the deployed Flint Quiz agent and how it integrates with the same tools and durable state as the text Playground.

**Owner**: Platform. **Audience**: developers, reviewers, on-call.

**Cross-references**: [`specs/002-system-architecture.md §9`](../specs/002-system-architecture.md), [`specs/004-agent-behavior.md §8`](../specs/004-agent-behavior.md), [`specs/009-agent-governance.md §2.6`](../specs/009-agent-governance.md), [`tasks/006-voice-realtime.md`](../tasks/006-voice-realtime.md), [`docs/playground.md`](./playground.md).

---

## 1. One Agent, Two Channels

The voice channel is a **second entry point** to the same `QuizAgent`. There is **no separate voice codebase** (ADR-001 / 004-agent §8). Durable state lives in Cosmos (ADR-003); the channel is metadata, recorded per submission on the `answers[].channel` and `grading_event.channel` fields, never on a persisted "current channel" flag.

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  Foundry Playground      │         │  Foundry Realtime        │
│  (text channel, FR-006)  │         │  (voice channel, FR-007) │
└────────────┬─────────────┘         └────────────┬─────────────┘
             │                                    │
             ▼                                    ▼
   ┌───────────────────────────────────────────────────────┐
   │   The single QuizAgent                                │
   │   - dispatcher (src/agent/dispatcher.py)              │
   │   - five tools (src/agent/tools.py)                   │
   │   - prompt-hash verification per turn (GOV-003)       │
   └───────────────────────────────────────────────────────┘
                              │
                              ▼
                ┌───────────────────────────┐
                │  Cosmos `sessions` row    │
                │  (authoritative state)    │
                └───────────────────────────┘
```

Source files added in 006-voice-realtime:

| File                                 | Purpose                                                              |
|--------------------------------------|----------------------------------------------------------------------|
| `src/voice/realtime_runtime.py`      | WebRTC-facing façade; per-language voice; channel-tagged dispatch.   |
| `src/voice/stt_pipeline.py`          | STT router — only finals that pass the confidence floor reach tools. |
| `src/voice/tts_pipeline.py`          | Defensive markdown strip pre-TTS; `agent.tts_strip` warning emitter. |
| `src/voice/idle_handler.py`          | Two-stage dead-air handler (30 s reprompt / 60 s close).             |
| `src/voice/session_cap.py`           | Per-connection length cap (`voice:maxSessionMinutes`, default 30).   |

---

## 2. Connecting

The Realtime endpoint is provisioned in [`infra/modules/realtime.bicep`](../infra/modules/realtime.bicep). It is **not** a separate ARM resource — it is the protocol surface the Foundry account exposes once a realtime-capable model deployment exists (e.g., `gpt-realtime`).

Per env, post-`azd up`:

1. Resolve the endpoint: from `azd env get-values`, capture `REALTIME_ENDPOINT` (e.g., `wss://<custom-subdomain>.openai.azure.com/openai/realtime?deployment=<model>`).
2. Authenticate with your Entra identity (SECT-005 — anonymous WebRTC handshakes are rejected).
3. The Realtime SDK adapter (production) plugs into `src/voice/realtime_runtime.py:RealtimeRuntime` to dispatch tool calls through the shared `Dispatcher`.

A handshake from a Realtime client that is RBAC-allowed should reach the same `QuizAgent` instance that the Playground uses; no second tool registration.

---

## 3. Voice-per-Language

| Language | AppConfig key | Default voice | Notes                          |
|----------|---------------|---------------|--------------------------------|
| `en`     | `voices:en`   | `alloy`       | Foundry / OpenAI Realtime.     |
| `fr`     | `voices:fr`   | `shimmer`     | Foundry / OpenAI Realtime.     |
| `es`     | `voices:es`   | `verse`       | Foundry / OpenAI Realtime.     |

The voice is selected at session-bind time from `session.language` (the persisted column on the session row). A brief code-switched utterance (e.g., "uh, *the answer is la primera*" in an `en` session) **does not flip the voice**. Language changes only happen via an explicit `set_language` tool call (GOV-027).

The default map lives in [`infra/modules/realtime.bicep`](../infra/modules/realtime.bicep); AppConfig overrides at runtime so a voice swap does not require a redeploy.

---

## 4. Latency Budget (NFR-001)

Voice tool-call **p95 ≤ 300 ms** under nominal load. The runtime emits a `voice.tool_call` custom event on every dispatch carrying:

| Field        | Sensitivity | Notes                                              |
|--------------|-------------|----------------------------------------------------|
| `session_id` | 🟢          | Used for joins to `audit` and `sessions`.          |
| `tool`       | 🟢          | One of the five tools.                             |
| `language`   | 🟢          | Session language at dispatch time.                 |
| `channel`    | 🟢          | Always `voice` here.                               |
| `latency_ms` | 🟢          | Wall clock around the dispatcher call.             |
| `ok`         | 🟢          | True on successful envelope; False on error code.  |

These flow into:

- **Workbook**: `infra/modules/observability/workbooks/voice-hot-path.bicep` — "Quiz Voice — Hot Path".
- **Alert**: `infra/modules/observability/alerts/voice-latency.bicep` — fires when p95 latency exceeds the budget over a rolling 5-minute window. Disabled by default in dev.

**Hot-path forbidden activities** (codified in code review checklist):

- No Foundry Evaluations inline.
- No more than one AI Search call per turn (`start_quiz` may do two: ID list + Q1 view).
- No long-running blob reads.

---

## 5. Idle Handling (GOV-014)

Two-stage dead-air model implemented in `src/voice/idle_handler.py`:

| Threshold                     | Key                          | Default | Action                                                                  |
|-------------------------------|------------------------------|---------|-------------------------------------------------------------------------|
| First idle                    | `voice:idleReprompSeconds`   | 30 s    | Agent re-prompts once using the `idle_reprompt` phrasing-block slot.    |
| Second idle (cumulative)      | `voice:idleCloseSeconds`     | 60 s    | Runtime gracefully closes the WebRTC connection; state intact.          |

Close does **not** mutate the Cosmos row — the next `submit_answer` for the same `session_id` (text or voice) succeeds (NFR-013, FORBIDDEN ACTIONS).

---

## 6. Session Length Cap (NFR-013)

| Key                          | Default | Behaviour on exceed                                                                 |
|------------------------------|---------|--------------------------------------------------------------------------------------|
| `voice:maxSessionMinutes`    | 30 min  | Agent says farewell in session language; runtime closes the connection; state intact. |

Realtime billing is per-minute and runs to real money. The cap is server-clock, never the connection's heartbeat. Tests pin a synthetic clock; the live runtime ticks the cap on every transcript event.

---

## 7. Channel Switch (FR-009)

A voice → text (or text → voice) switch on the same `session_id` is seamless because:

- The agent loads state from Cosmos on every turn (ADR-003).
- `src/agent/resumption.py:resume_from_session` flags `is_channel_switch=True` so the agent re-acknowledges the active question instead of re-issuing it.
- Per-submission `channel` is recorded on the answer row — the `grading_event` carries the correct dimension automatically.

Smoke test path (TEST-009): voice answer Q1 → reconnect on text → text answer Q2 → Cosmos shows `answers[0].channel="voice"`, `answers[1].channel="text"`, language unchanged.

---

## 8. Smoke Tests

| ID         | What it asserts                                                                  |
|------------|-----------------------------------------------------------------------------------|
| TEST-005   | Spanish voice quiz completes end-to-end; voice dashboard reports p95 within budget. |
| TEST-009   | Voice → text channel switch on the same `session_id` resumes at the next unanswered question, in the persisted language. |

In-process flavours of both live under `tests/integration/test_voice_smoke_es.py` and `tests/integration/test_channel_switch.py`. The live-endpoint version runs against a deployed Foundry account in `009-testing`.

---

## 9. Operational Notes

- **Region**: the Realtime endpoint inherits the Foundry account's region (provisioned in `001-infrastructure` TASK-003 / TASK-013). Voice clients must connect to the regional endpoint — cross-region connections add ~200 ms RTT, eating the latency budget.
- **STT confidence floor**: `voice:sttConfidenceFloor` (default 0.5). Below-floor finals are dropped with a `voice.stt_drop` event; the agent re-prompts.
- **Defensive TTS strip**: when the `agent.tts_strip` warning fires, fix the upstream tool — the strip is the last line of defence, not the first.

---

## 10. Troubleshooting

| Symptom                                       | First place to look                                                              |
|-----------------------------------------------|-----------------------------------------------------------------------------------|
| WebRTC handshake fails                        | Entra auth; APIM rule; Foundry account regional pin.                              |
| Voice plays markdown ("asterisk asterisk")    | `agent.tts_strip` warning count in the workbook → fix the source tool.            |
| Quiz "stops listening" after 60 s             | Expected (GOV-014). Reconnect; state intact.                                      |
| Voice flips language after a code-switch      | Bug — file a P0. Session language only changes via explicit `set_language`.       |
| Latency alert firing on cold start            | Confirm warm pool sizing (004-agent TASK-069). Suppress with quiet hours in dev.  |
