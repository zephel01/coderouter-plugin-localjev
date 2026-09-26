"""Default System One questions.

Decomposed noul + one choice, matching the Jev-sample finding that
single 4-way choice is poorly calibrated vs. independent nouls.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = "jev-latest"

DEFAULT_QUESTIONS: dict[str, Any] = {
    "needs_tools": {
        "type": "noul",
        "instructions": (
            "Does this turn require calling tools, editing files, "
            "or running commands rather than answering in prose?"
        ),
        "criteria": {
            "true": "The user asks to change code, inspect the repo, run tests, or use tools.",
            "false": "The user only wants an explanation or a short answer.",
        },
    },
    "high_risk": {
        "type": "noul",
        "instructions": (
            "Would a wrong action here be hard to undo "
            "(delete, deploy, secrets, production, force-push)?"
        ),
        "criteria": {
            "true": "Destructive, irreversible, or secret-bearing work.",
            "false": "Read-only or easily reversible work.",
        },
    },
    "needs_strong_model": {
        "type": "noul",
        "instructions": (
            "Does this need a stronger / larger model than a small local coder?"
        ),
        "criteria": {
            "true": "Architecture, subtle bug, multi-file design, or ambiguous spec.",
            "false": "Mechanical rename, boilerplate, or a short factual answer.",
        },
    },
    "route": {
        "type": "choice",
        "instructions": "Which CodeRouter profile should handle this turn?",
        "criteria": {
            "coding": "Implementation, refactor, tests, tool use on a local coder model.",
            "reasoning": "Design, root-cause, or ambiguous requirements; use a stronger model.",
            "general": "Q&A, docs, or chat that is not a coding task.",
        },
    },
}

# Map LocalJev choice labels → CodeRouter profile names.
DEFAULT_PROFILE_MAP: dict[str, str] = {
    "coding": "coding",
    "reasoning": "reasoning",
    "general": "general",
}
