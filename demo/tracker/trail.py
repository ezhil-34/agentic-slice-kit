"""
The student-facing view of "which agent is doing what" - an abstract trail
built from the records a turn wrote, not a fake progress bar.

Four names, one per job (they map onto flow.py's handlers):

    Evaluator  checks the answer against the known roots           (code)
    Diagnoser  matches it to known mistakes, names the mistake     (code + model)
    Planner    counts repeats, decides how to re-teach, or moves on (code)
    Tutor      writes the new explanation                          (model)

A "turn" is everything written after the student's latest answer. Each record
of interest becomes one step, so the trail is exactly as long as the work
that really happened - a correct answer is two steps, a repeated mistake is
five or six. The page paces them out (a step stays up for a second or two even
if it took a millisecond) so a student can actually read what the system did.
"""
from __future__ import annotations

from typing import NamedTuple

from slice.records import Version

from .schema import BUG_LABELS, QUESTIONS, STRATEGY_LABELS

_Q_ORDER = [q["id"] for q in QUESTIONS]


class Step(NamedTuple):
    agent: str
    doing: str


def turn_records(records: list[Version]) -> list[Version]:
    """Records after the student's most recent answer."""
    last = max((i for i, v in enumerate(records) if v.kind == "expert_answer"), default=-1)
    return records[last + 1:] if last >= 0 else []       # no answer yet -> no turn yet


def _step_for(v: Version) -> Step | None:
    p = v.payload
    if v.kind == "attempt":
        return Step("Evaluator", "Your answer is correct." if p["correct"]
                    else "Checked your answer against the correct roots - it doesn't match.")
    if v.kind == "operator_match":
        n = len(p.get("matched_operators", []))
        if n >= 2:
            return Step("Diagnoser", "Your answer fits more than one kind of mistake.")
        if n == 1:
            return Step("Diagnoser", "Your answer matches a known mistake pattern.")
        return Step("Diagnoser", "No known pattern matches - reading your answer closely.")
    if v.kind == "classification":
        label = BUG_LABELS.get(p["error_type"], p["error_type"])
        return Step("Diagnoser", f"Working out what went wrong: {label}.")
    if v.kind == "method_choice":
        return Step("Planner", "Noted which method you used.")
    if v.kind == "misconception":
        n = p["occurrences"]
        return Step("Planner", f"Logged this mistake - {n} time{'' if n == 1 else 's'} on this question.")
    if v.kind == "human_choice":
        return Step("Planner", "Following the way you chose to continue.")
    if v.kind == "strategy_plan":
        return Step("Planner", p["reason"])
    if v.kind == "reexplanation":
        label = STRATEGY_LABELS.get(p["strategy"], p["strategy"])
        return Step("Tutor", f"Wrote a new explanation as a {label}.")
    if v.kind == "problem":
        phase = p.get("phase")
        if phase == "method_check":
            return Step("Planner", "Asking which method you used.")
        if phase == "choice":
            return Step("Planner", "Asking how you'd like to continue.")
        if p.get("attempt_number", 1) > 1:
            return Step("Planner", "Setting up another try at this question.")
        n = _Q_ORDER.index(p["id"]) + 1 if p.get("id") in _Q_ORDER else None
        return Step("Planner", f"Moving on to question {n}." if n else "Moving on to the next question.")
    if v.kind == "failure":
        return Step("Runner", f"Stopped: {p.get('detail', 'something went wrong')}")
    return None


def steps_from_records(records: list[Version]) -> list[Step]:
    return [s for s in (_step_for(v) for v in turn_records(records)) if s]


def active_step(records: list[Version]) -> Step:
    """What is running RIGHT NOW, judged from the newest record: the step after
    it. An operator_match with 0-1 matches is followed by the classify model
    call; `misconception` and `strategy_plan` are followed by reexplain."""
    last = records[-1] if records else None
    if last is None:
        return Step("Evaluator", "Checking your answer…")
    if last.kind == "operator_match":
        if len(last.payload.get("matched_operators", [])) >= 2:
            return Step("Diagnoser", "Found more than one possible mistake…")
        return Step("Diagnoser", "Explaining what went wrong…")
    if last.kind == "classification":
        return Step("Diagnoser", "Explaining what went wrong…")
    if last.kind in ("misconception", "strategy_plan", "reexplanation", "human_choice"):
        return Step("Tutor", "Writing a different explanation…")
    return Step("Evaluator", "Checking your answer…")     # expert_answer / attempt / unmapped
