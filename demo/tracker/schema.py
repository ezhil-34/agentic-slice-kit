"""
What this slice's two model calls produce, the fixed question bank, and the
operator layer that tells `classify` what it's looking at instead of asking
it to guess from scratch.

An "operator" is a tiny deterministic function: if the student has bug X,
their wrong answer will be exactly Y, computed from the question's own a/b/c
- never assumed. Matching the student's real answer against every operator's
prediction is pure code (`match_operators`), on purpose: which bug fits is
something the numbers can settle, not something we want to be the model's
opinion. `classify` is still a real model call - it explains *why* in words,
and is the only decider when no operator's prediction matches at all - but it
is handed the code's own finding rather than starting blind.

Two of the four operators - formula_sign_flip and factor_sign_flip - always
predict the identical wrong answer (both are "negate every correct root").
That collision is deliberate: it is the concrete case where re-asking a
question of the same shape teaches nothing, and the only honest move is to
ask the student which method they used. See MisconceptionTracker MVP Build
Context, section 2.1.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ----------------------------------------------------------------- constants
# Four fixed bug types - a deliberate, human-made judgement call, not
# something classify invents. Three are real operators; the fourth
# ("unclassified") is the honest fallback when nothing else fits.
ERROR_TYPES = ("formula_sign_flip", "formula_forgot_2a", "factor_sign_flip",
               "factor_wrong_pair", "unclassified")

# The three re-explanation strategies are likewise fixed by us in advance.
# `reexplain` chooses among them; it does not invent new pedagogy on the fly.
STRATEGIES = ("worked_example", "alternate_method", "simpler_problem", "real_world_example")

# alternate_method is the one strategy that costs the student something: a
# whole new solving method to learn mid-question. demo/tracker/plan.py only
# offers it when the other method is genuinely quicker for THIS equation.
STRATEGY_LABELS: dict[str, str] = {
    "worked_example": "worked example",
    "alternate_method": "different solving method",
    "simpler_problem": "simpler practice problem",
    "real_world_example": "real-world example",
}

REPEATS_BEFORE_PAUSE = 3
"""Three same-bug failures on one question before we stop re-explaining and
ask the student directly. Counted from stored `classification` records,
never from the kit's budget.attempt() counter - see README.md section 6."""

# --------------------------------------------------------------- operators
# method-tagged so the method-check question (schema.OPERATOR_METHOD) can
# resolve a collision once the student says which one they used.
OPERATORS: list[dict] = [
    {"id": "formula_sign_flip", "method": "formula",
     "description": "Uses b instead of -b in the quadratic formula, so both "
                     "roots come out with the wrong sign."},
    {"id": "formula_forgot_2a", "method": "formula",
     "description": "Divides by a instead of 2a at the final step."},
    {"id": "factor_sign_flip", "method": "factorization",
     "description": "From a factor (x - p) = 0, writes x = -p and drops the "
                     "sign flip - same wrong numbers as formula_sign_flip."},
    {"id": "factor_wrong_pair", "method": "factorization",
     "description": "Picks a factor pair that does not actually multiply to "
                     "c, so the result does not satisfy the equation at all."},
]
OPERATOR_METHOD: dict[str, str] = {op["id"]: op["method"] for op in OPERATORS}

# Human-friendly display text for a bug type - shared by flow.py's "second
# encounter" greeting and by web/student.py's UI, so both say the same thing.
BUG_LABELS: dict[str, str] = {
    "formula_sign_flip": "sign errors in the quadratic formula",
    "formula_forgot_2a": "dividing by the wrong number at the end of the formula",
    "factor_sign_flip": "sign errors when factoring",
    "factor_wrong_pair": "picking a factor pair that doesn't actually work",
    "unclassified": "a mistake we haven't pinned down yet",
}


def _discriminant(a: float, b: float, c: float) -> float:
    return b * b - 4 * a * c


def predict_operator(op_id: str, a: float, b: float, c: float) -> set[float] | None:
    """What a student with exactly this bug would type, for THIS equation.
    None means the operator makes no fixed prediction here (either the maths
    doesn't apply, e.g. a negative discriminant, or - for factor_wrong_pair -
    because "some pair that doesn't work" is a class of answers, not one
    number; that operator is matched as a catch-all in `match_operators`
    instead, never predicted here)."""
    D = _discriminant(a, b, c)
    if op_id in ("formula_sign_flip", "factor_sign_flip"):
        if D < 0:
            return None
        root1, root2 = (-b + D ** 0.5) / (2 * a), (-b - D ** 0.5) / (2 * a)
        return {round(-root1, 6), round(-root2, 6)}
    if op_id == "formula_forgot_2a":
        if D < 0:
            return None
        return {round((-b + D ** 0.5) / a, 6), round((-b - D ** 0.5) / a, 6)}
    if op_id == "factor_wrong_pair":
        return None
    raise ValueError(f"unknown operator {op_id!r}")


def _set_match(a: set[float], b: set[float], tol: float = 0.05) -> bool:
    """Set equality with float tolerance - duplicated in miniature from
    flow.py's roots_match on purpose: schema.py stays import-free of flow.py
    so this pure-maths module never depends on the handler layer."""
    if len(a) != len(b):
        return False
    remaining = set(b)
    for x in a:
        hit = next((y for y in remaining if abs(x - y) <= tol), None)
        if hit is None:
            return False
        remaining.discard(hit)
    return True


def match_operators(a: float, b: float, c: float, correct_roots: list[float],
                     student_roots: set[float], tol: float = 0.05) -> list[str]:
    """Which operators' predicted wrong answer matches what the student
    actually typed, computed fresh from a/b/c every time - never assumed.
    Zero, one, or two-or-more (a collision) operators can match."""
    matched = [
        op_id for op_id in ("formula_sign_flip", "factor_sign_flip", "formula_forgot_2a")
        if (pred := predict_operator(op_id, a, b, c)) is not None
        and _set_match(student_roots, pred, tol)
    ]
    if not matched and student_roots:
        satisfies = all(abs(a * x * x + b * x + c) <= max(1.0, abs(a)) * tol
                         for x in student_roots)
        if not satisfies:
            matched.append("factor_wrong_pair")
    return matched


def collisions(questions: list[dict] | None = None) -> list[tuple[str, str, str]]:
    """(op_a, op_b, question_id) triples where both operators predict the
    identical wrong answer on that question - computed against the actual
    question bank, never hardcoded. Diagnostic + used by tests to prove the
    collision this project is built around is real, not assumed."""
    qs = QUESTIONS if questions is None else questions
    fixed_ops = ["formula_sign_flip", "formula_forgot_2a", "factor_sign_flip"]
    hits: list[tuple[str, str, str]] = []
    for q in qs:
        preds = {op_id: predict_operator(op_id, q["a"], q["b"], q["c"]) for op_id in fixed_ops}
        for i, op_a in enumerate(fixed_ops):
            for op_b in fixed_ops[i + 1:]:
                pa, pb = preds[op_a], preds[op_b]
                if pa is not None and pb is not None and pa == pb:
                    hits.append((op_a, op_b, q["id"]))
    return hits


# ------------------------------------------------------------- model outputs

class ErrorClassification(BaseModel):
    """What `classify` produces. One of ERROR_TYPES, never a new label."""

    error_type: Literal["formula_sign_flip", "formula_forgot_2a", "factor_sign_flip",
                         "factor_wrong_pair", "unclassified"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(
        description="One sentence: what in the student's answer supports this label. "
                    "Surfaced back to the student per README.md refusal #3 - never guess silently.")


class ReExplanation(BaseModel):
    """What `reexplain` produces. `strategy` must be one not already tried
    for this error type this session - `flow.py` enforces that, not the prompt."""

    strategy: Literal["worked_example", "alternate_method", "simpler_problem",
                      "real_world_example"]
    explanation: str = Field(description="The actual re-teaching text shown to the student.")


# -------------------------------------------------------------- question bank
# 10 of the 16 questions from docs' methods reference - the ones with clean
# rational roots, so the plain-code root checker in flow.py never has to
# parse a radical. Q11-16 (radicals) are left for a later pass; see
# README.md section 12, phase 2.
#
# a/b/c are the equation's own coefficients (ax²+bx+c=0) - added so every
# operator's prediction can be computed directly from the question, not
# hand-guessed per question. known_error_patterns is likewise no longer
# hand-curated per question: with a/b/c present, all four operators are
# always mechanically defined, so it's just ERROR_TYPES minus "unclassified".

_ALL_OPERATOR_IDS = [op["id"] for op in OPERATORS]

QUESTIONS: list[dict] = [
    {"id": "q1", "text": "x² − 5x + 6 = 0", "a": 1, "b": -5, "c": 6, "roots": [2, 3],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q2", "text": "x² + 7x + 12 = 0", "a": 1, "b": 7, "c": 12, "roots": [-3, -4],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q3", "text": "x² − 3x − 10 = 0", "a": 1, "b": -3, "c": -10, "roots": [5, -2],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q4", "text": "2x² − 5x + 3 = 0", "a": 2, "b": -5, "c": 3, "roots": [1.5, 1],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q5", "text": "x² − 4x + 4 = 0", "a": 1, "b": -4, "c": 4, "roots": [2, 2],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q6", "text": "x² + 2x − 8 = 0", "a": 1, "b": 2, "c": -8, "roots": [-4, 2],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q7", "text": "3x² − 2x − 1 = 0", "a": 3, "b": -2, "c": -1, "roots": [1, -1 / 3],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q8", "text": "x² − 2x − 3 = 0", "a": 1, "b": -2, "c": -3, "roots": [3, -1],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q9", "text": "2x² + 3x − 2 = 0", "a": 2, "b": 3, "c": -2, "roots": [0.5, -2],
     "known_error_patterns": _ALL_OPERATOR_IDS},
    {"id": "q10", "text": "x² − 7x + 10 = 0", "a": 1, "b": -7, "c": 10, "roots": [5, 2],
     "known_error_patterns": _ALL_OPERATOR_IDS},
]


class CustomCheck(BaseModel):
    """What the custom-equation check produces. This is the ONE place in the
    project where the model, not code, judges correctness - a student-typed
    equation has no known roots on file. `computed_roots` exists so that
    judgement shows its working: the model must state its own answer before
    comparing, and the page shows it next to the verdict."""

    computed_roots: list[str] = Field(
        description="The model's own solution to the equation, one string per root "
                    "(e.g. '2', '-1/3', '1 + sqrt(2)'), derived BEFORE looking at the "
                    "student's answer.")
    student_correct: bool = Field(
        description="True only if the student's answer matches computed_roots.")
    feedback: str = Field(description="One paragraph for the student.")


# What each mistake means, in plain words, and one thing to do about it - shown
# on the student's progress page. Same keys as BUG_LABELS / ERROR_TYPES.
BUG_INFO: dict[str, dict[str, str]] = {
    "formula_sign_flip": {
        "what": "The quadratic formula starts from −b. Using +b flips the sign of both answers.",
        "tip": "Put −b in brackets first: with b = −5, −(−5) = +5.",
    },
    "formula_forgot_2a": {
        "what": "The formula divides by 2a, the whole bottom line. Dividing by just a doubles your answers when a = 1.",
        "tip": "Work out 2a on its own line before you divide anything.",
    },
    "factor_sign_flip": {
        "what": "A bracket like (x − 2) means the root is +2. The sign inside the bracket is the opposite of the root.",
        "tip": "Set each bracket to zero and solve it: x − 2 = 0, so x = 2.",
    },
    "factor_wrong_pair": {
        "what": "The two numbers picked don't multiply to c (and add to b), so the factors don't rebuild the equation.",
        "tip": "Check by expanding your brackets back out - you should land on the original equation.",
    },
    "unclassified": {
        "what": "A mistake that didn't match a known pattern.",
        "tip": "Substitute your answer back into the equation; if it doesn't give 0, it isn't a root.",
    },
}
