"""MCP server — exposes the 5 quiz tools to the Foundry Playground.

The tool bodies are SHARED with `src/agent/chat.py` (which dispatches
them through MAF) and with the production agent runtime (via the
deployed Container App). This module's job is purely transport:
speak MCP / JSON-RPC over HTTP, validate the Foundry caller's Entra
token, and delegate to ``build_tools(deps)``.
"""
