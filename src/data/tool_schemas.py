"""Helpers for publishing model-facing tool schemas.

The Pydantic request models in `src.data.models` carry `user_id` because
the dispatcher's defence-in-depth check requires `request.user_id` to
match the authenticated principal. That's a WIRE concern, not a MODEL
concern — the user identity flows from the authenticated caller (Entra
JWT for MCP, `DefaultAzureCredential` for the chat CLI), never from a
free-text field the LLM picks.

Exposing `user_id` to the model has two bad effects:

  * The model asks the user for an Entra OID it has no business knowing.
  * Even when the wire already has the right OID (MCP path), the model
    still wastes a turn filling the required field — see the
    `start_quiz: user_id does not match principal` failure mode hit
    during the MCP migration.

This module exposes :func:`public_input_schema` — strips `user_id` from
the JSON Schema (`properties` + `required`) so the model never sees it.
Callers at the wire boundary (MCP server, FunctionTool registration in
the agent) use this helper; the dispatcher injects `user_id` from the
authenticated principal before the Pydantic validation runs.
"""

from __future__ import annotations

from typing import Any


def public_input_schema(model_cls: Any) -> dict[str, Any]:
    """Return ``model_cls.model_json_schema()`` with ``user_id`` removed.

    Safe to call on models that don't have ``user_id`` — they pass through
    unchanged. Recursively scrubs nested ``$defs`` only at the top level;
    no Flint Quiz tool currently nests ``user_id`` inside a sub-model.
    """

    schema = model_cls.model_json_schema()
    props = schema.get("properties")
    if isinstance(props, dict) and "user_id" in props:
        # Mutate a copy to avoid surprising the caller if they retained
        # a reference to `model_cls`'s schema cache.
        props = {k: v for k, v in props.items() if k != "user_id"}
        schema = dict(schema)
        schema["properties"] = props
    required = schema.get("required")
    if isinstance(required, list) and "user_id" in required:
        schema["required"] = [f for f in required if f != "user_id"]
    return schema


__all__ = ["public_input_schema"]
