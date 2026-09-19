"""
What this slice's two model calls produce, and the fixed question bank.

Only two records here are ever validated against a model's output:
ErrorClassification (from `classify`) and ReExplanation (from `reexplain`).
Everything else - Question, the root-checking - is plain code, per README.md
section 5: "Deciding which fixed re-explanation strategy to try next" and
"Classifying a wrong answer" are the agent's job; writing the question bank
and the error-pattern definitions is ours, not the model's.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ----------------------------------------------------------------- constants
# The three error types are a deliberate, human-made judgement call - see
# README.md section 10 ("the three error types ... are ours"). `classify`
# picks one of these four; it never invents a new category.
ERROR_TYPES = ("sign_error", "factoring_error", "arithmetic_slip", "unclassified")

# The three re-explanation strategies are likewise fixed by us in advance.
# `reexplain` chooses among them; it does not invent new pedagogy on the fly.
STRATEGIES = ("worked_example", "alternate_method", "simpler_problem")

REPEATS_BEFORE_PAUSE = 3
"""Three same-pattern failures on one question before we stop re-explaining
and ask the student directly. Counted from stored `classification` records,
never from the kit's budget.attempt() counter - see README.md section 6."""


# ------------------------------------------------------------- model outputs

class ErrorClassification(BaseModel):
    """What `classify` produces. One of ERROR_TYPES, never a new label."""

    error_type: Literal["sign_error", "factoring_error", "arithmetic_slip", "unclassified"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(
        description="One sentence: what in the student's answer supports this label. "
                    "Surfaced back to the student per README.md refusal #3 - never guess silently.")


class ReExplanation(BaseModel):
    """What `reexplain` produces. `strategy` must be one not already tried
    for this error type this session - `flow.py` enforces that, not the prompt."""

    strategy: Literal["worked_example", "alternate_method", "simpler_problem"]
    explanation: str = Field(description="The actual re-teaching text shown to the student.")


# -------------------------------------------------------------- question bank
# 10 of the 16 questions from docs' methods reference - the ones with clean
# rational roots, so the plain-code root checker in flow.py never has to
# parse a radical. Q11-16 (radicals) are left for a later pass; see
# README.md section 12, phase 2.

QUESTIONS: list[dict] = [
    {"id": "q1", "text": "x² − 5x + 6 = 0", "roots": [2, 3],
     "known_error_patterns": ["factoring_error", "arithmetic_slip"]},
    {"id": "q2", "text": "x² + 7x + 12 = 0", "roots": [-3, -4],
     "known_error_patterns": ["sign_error", "factoring_error"]},
    {"id": "q3", "text": "x² − 3x − 10 = 0", "roots": [5, -2],
     "known_error_patterns": ["sign_error", "arithmetic_slip"]},
    {"id": "q4", "text": "2x² − 5x + 3 = 0", "roots": [1.5, 1],
     "known_error_patterns": ["factoring_error", "arithmetic_slip"]},
    {"id": "q5", "text": "x² − 4x + 4 = 0", "roots": [2, 2],
     "known_error_patterns": ["arithmetic_slip"]},
    {"id": "q6", "text": "x² + 2x − 8 = 0", "roots": [-4, 2],
     "known_error_patterns": ["sign_error", "factoring_error"]},
    {"id": "q7", "text": "3x² − 2x − 1 = 0", "roots": [1, -1 / 3],
     "known_error_patterns": ["sign_error", "arithmetic_slip"]},
    {"id": "q8", "text": "x² − 2x − 3 = 0", "roots": [3, -1],
     "known_error_patterns": ["sign_error", "factoring_error"]},
    {"id": "q9", "text": "2x² + 3x − 2 = 0", "roots": [0.5, -2],
     "known_error_patterns": ["arithmetic_slip", "factoring_error"]},
    {"id": "q10", "text": "x² − 7x + 10 = 0", "roots": [5, 2],
     "known_error_patterns": ["factoring_error", "arithmetic_slip"]},
]
