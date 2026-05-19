"""Prompt layers + composition for the single MAF agent.

Four pinned layers, fixed concatenation order (009-gov §1.1):

  1. `identity.txt`        — code-pinned identity and role.
  2. `contract.txt`        — code-pinned behavioral contract (refusal
                             rules, tool boundary, grading discipline).
  3. `lang/{en,fr,es}.yaml` — session-pinned per-language phrasing block.
  4. `session-frame-template.txt` — server-rendered per-session frame.

`compose.py` reads these four sources, renders the session frame from
the `SessionFrame` model, and emits `(rendered_prompt, sha256_hex)`.
The hash is persisted on `SessionDoc.prompt_hash` at `start_quiz` and
re-verified on every subsequent tool invocation (GOV-001..003 /
TASK-071). Mismatch is a P0 halt path.

`MANIFEST.json` records the SHA-256 of each individual layer file (the
build-time content-addressing contract). The composed hash is derived
from these — never the other way around — so a layer-file tamper
attempt fails fast.
"""

# Intentionally no eager re-exports — `compose.py` is the load-bearing module
# and importers depend on it directly. The runpy entrypoint
# (`python -m src.agent.prompts.compose --write-manifest`) regenerates
# MANIFEST.json; an eager re-export here would trigger a Python `runpy`
# warning about double-loading the same module under both names.
