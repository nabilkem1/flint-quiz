"""Voice channel wiring for the Flint Quiz agent (006-voice-realtime).

The voice surface is a *second entry point* to the same `QuizAgent`
instance. Durable state lives in Cosmos (ADR-003); channel is metadata,
never state. Nothing in this package re-registers tools — the dispatcher
built by ``src/agent/quiz_agent.py`` is reused for both channels.
"""
