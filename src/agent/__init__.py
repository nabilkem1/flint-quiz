"""Single Microsoft Agent Framework (MAF) agent for Flint Quiz.

The package implements the runtime side of ADR-001 / ADR-002 / ADR-005:

* `quiz_agent.create_agent()` is the factory the Hosted Agent runtime
  instantiates. It wires the four pinned prompt layers, registers
  exactly the five allowed tools (see `dispatcher.ALLOWED_TOOLS`),
  installs the dispatcher between MAF's tool-call loop and the tool
  bodies, and applies the 600-token output cap (GOV-091).
* `dispatcher.dispatch()` is the **only** call path from MAF to the
  tool bodies. The `import-linter` contract in `pyproject.toml`
  enforces this statically; the integration test in
  `tests/integration/test_dispatcher_allowlist.py` enforces it
  dynamically. Bypass is a P1 (GOV-010).
* `prompts/` carries the four content-addressed layers
  (identity / contract / per-language phrasing block / session frame
  template) and the `compose()` function that produces the hashed,
  per-session system prompt (GOV-001..003).

Tool **bodies** live in 005-tools — this package owns only the agent
shell, the dispatcher, prompt composition, language detection,
thread/resumption helpers, and the latency-discipline guarantees.
"""
