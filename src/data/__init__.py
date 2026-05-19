"""Repository / data-access layer.

Domain models live in `models`; the AI Search client lives in
`question_search`. No tool, agent, or prompt code may live here — dependencies
flow downward only (docs/coding-standards.md §2.1).
"""

__all__ = [
    "models",
    "question_search",
]
