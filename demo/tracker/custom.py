"""
The custom-equation path: a student types their own quadratic and their own
answer, and a MODEL - not code - judges it.

This is a deliberate exception to how the rest of the tracker works. Every
graded question has known a/b/c and known roots, so `evaluate` in flow.py is
plain code and "correct" is never a model's opinion. A student-typed equation
has no roots on file, and pretending a half-working parser could supply them
would be worse than saying so. So this path is handed to the model openly,
logged as its own record kind ("custom_check", never "attempt"), and kept out
of every count that feeds a student's profile. It does not run through
runner.advance() or the Flow at all - there is no multi-step state here, just
one judged exchange.

Because a bare verdict from a model is not trustworthy enough to grade on,
the prompt makes it derive the roots first (`computed_roots`) and the page
shows them. `check_consistency` then does the one thing code can do without
re-solving anything: compare the model's own stated roots against the
student's typed answer and flag it if the model's verdict disagrees with its
own working. That flag is a reliability signal for a human, never a silent
correction.
"""
from __future__ import annotations

import re
from pathlib import Path

from .flow import parse_roots, roots_match
from .schema import CustomCheck

_PROMPT = Path(__file__).parent / "prompts" / "custom_check.md"

# "2", "-1/3", "0.5" - the only root strings code can compare numerically.
_PLAIN_NUMBER = re.compile(r"\s*-?\d+(?:\.\d+)?(?:\s*/\s*-?\d+(?:\.\d+)?)?\s*")


def custom_check_messages(equation: str, student_answer: str) -> list[dict]:
    return [
        {"role": "system", "content": _PROMPT.read_text(encoding="utf-8")},
        {"role": "user", "content":
            f"Equation: {equation}\n"
            f"Student's answer: {student_answer}"},
    ]


def check_consistency(result: CustomCheck, student_answer: str) -> bool | None:
    """Does the model's verdict agree with its own stated roots?

    True/False when that can be decided by plain numeric comparison; None when
    it cannot (a root with a radical, an empty root list, an answer written
    with radicals) - "can't tell" is reported as such, never as agreement."""
    if not result.computed_roots:
        return None
    if not all(_PLAIN_NUMBER.fullmatch(r) for r in result.computed_roots):
        return None
    if any(tok in student_answer.lower() for tok in ("√", "sqrt", "^")):
        return None
    model_roots = parse_roots(" ".join(result.computed_roots))
    student_roots = parse_roots(student_answer)
    if not student_roots:
        return None
    return roots_match(student_roots, model_roots) == result.student_correct
