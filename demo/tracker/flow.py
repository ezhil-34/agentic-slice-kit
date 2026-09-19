"""
The Misconception Tracker's handlers, mapped onto the kit's existing states.

No spine edit was needed - all four active/suspended states this domain uses
(DRAFTING, GATING, PROBING, AWAITING_EXPERT) already exist in slice/records.py.
Mapping, matching README.md's own names for each step:

    DRAFTING  = evaluate (code) + classify (model)   - README section 8 "evaluate", "classify"
    GATING    = log_and_decide (code, the back-edge)  - README section 8 "log_and_decide"
    PROBING   = reexplain (model)                     - README section 8 "reexplain"
    AWAITING_EXPERT = Presenting / Waiting for the student - both are "ask a
                       person and suspend"; only the resume_state differs.

Every point where the student is asked anything - a fresh question, a retry
of the same question, or the pause after three repeats - goes through
slice/callback.py's ask()/answer(), so "Presenting" is never its own active
state; it is what handle_gating and handle_probing do on their way to
suspending.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from slice import callback
from slice.llm import complete
from slice.records import RunState

from .schema import ERROR_TYPES, REPEATS_BEFORE_PAUSE, STRATEGIES, ErrorClassification, QUESTIONS, ReExplanation

_PROMPTS = Path(__file__).parent / "prompts"
_QUESTIONS_BY_ID = {q["id"]: q for q in QUESTIONS}


def _prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.md").read_text(encoding="utf-8")


# ------------------------------------------------------------ root checking
# Plain code, deliberately. README.md section 8: "kept out of the model means
# correctness is never a matter of the model's opinion."

_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:\s*/\s*-?\d+(?:\.\d+)?)?")


def parse_roots(text: str) -> set[float]:
    """Pull every number (including simple fractions like '3/2') out of a
    student's free-text answer. Deliberately permissive about format -
    'x=5,-2', 'x = 5 or x = -2', '5 and -2' all parse the same way."""
    out: set[float] = set()
    for m in _NUM.finditer(text or ""):
        token = m.group(0).replace(" ", "")
        if "/" in token:
            num, den = token.split("/")
            try:
                out.add(round(float(num) / float(den), 4))
            except ZeroDivisionError:
                continue
        else:
            out.add(round(float(token), 4))
    return out


def roots_match(a: set[float], b: set[float], tol: float = 0.05) -> bool:
    """Set equality with float tolerance, order-independent - a student can
    type either root first."""
    if len(a) != len(b):
        return False
    remaining = set(b)
    for x in a:
        hit = next((y for y in remaining if abs(x - y) <= tol), None)
        if hit is None:
            return False
        remaining.discard(hit)
    return True


# --------------------------------------------------------------- presenting
# Not a state of its own - a helper both handle_gating and handle_probing
# call on their way to AWAITING_EXPERT. See module docstring.

def _present_question(store, run_id, settings, question_id: str, attempt_number: int) -> None:
    q = _QUESTIONS_BY_ID[question_id]
    store.append(run_id, "problem", {
        "id": q["id"], "text": q["text"], "roots": q["roots"],
        "known_error_patterns": q["known_error_patterns"],
        "phase": "practice", "attempt_number": attempt_number,
    }, produced_by="system")
    callback.ask(store, run_id, f"Solve for x: {q['text']}",
                 {"resume_state": "drafting"}, settings)


def _present_choice(store, run_id, settings, question_id: str) -> None:
    store.append(run_id, "problem", {"id": question_id, "phase": "choice"}, produced_by="system")
    text = ("You've made the same kind of mistake three times on this one. "
            "Do you want a fully worked example, or a simpler problem first?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


# --------------------------------------------------------------- messages

def build_classify_messages(question: dict, wrong_answer: str) -> list[dict]:
    return [
        {"role": "system", "content": _prompt("classify")},
        {"role": "user", "content":
            f"Question: Solve for x: {question['text']}\n"
            f"Correct roots: {question['roots']}\n"
            f"Known error patterns for this question: {question['known_error_patterns']}\n"
            f"Student's answer: {wrong_answer}"},
    ]


def build_reexplain_messages(question: dict, error_type: str, already_tried: set[str],
                              forced_strategy: str | None) -> list[dict]:
    remaining = [s for s in STRATEGIES if s not in already_tried] or list(STRATEGIES)
    user = [
        f"Question: Solve for x: {question['text']}",
        f"Correct roots: {question['roots']}",
        f"The student's error type: {error_type}",
        f"Strategies already tried and rejected: {sorted(already_tried) or 'none'}",
    ]
    if forced_strategy:
        user.append(f"The student explicitly asked for: {forced_strategy}. Use that strategy.")
    else:
        user.append(f"Pick one strategy from: {remaining}")
    return [
        {"role": "system", "content": _prompt("reexplain")},
        {"role": "user", "content": "\n".join(user)},
    ]


# ------------------------------------------------------------------ handlers

def build_flow(call=complete):
    """Return the Flow. `call` is injected so the whole loop can be proven
    with demo/tracker/stub.py first - no key, no network. Matches the
    injection pattern in demo/smoke/flow.py exactly."""

    def handle_drafting(ctx) -> RunState:
        """evaluate + classify. README.md section 8."""
        q = ctx.latest("problem")
        answers = ctx.history("expert_answer")
        raw = answers[-1].payload.get("answer") or "" if answers else ""

        student_roots = parse_roots(raw)
        correct = roots_match(student_roots, set(q["roots"]))
        ctx.append("attempt", {
            "question_id": q["id"], "attempt_number": q["attempt_number"],
            "student_answer": raw, "correct": correct,
        }, produced_by="code:evaluate")

        if not correct:
            result: ErrorClassification = call(
                settings=ctx.settings, budget=ctx.budget,
                messages=build_classify_messages(q, raw),
                schema=ErrorClassification, step="classify",
            )
            payload = result.model_dump()
            payload.update(question_id=q["id"], attempt_number=q["attempt_number"])
            ctx.append("classification", payload, produced_by="agent:classify")

        return RunState.GATING

    def handle_gating(ctx) -> RunState:
        """log_and_decide. The back-edge lives here. README.md section 8."""
        q = ctx.latest("problem")

        # --- resumed from the pause question: the student just chose a strategy
        if q.get("phase") == "choice":
            answers = ctx.history("expert_answer")
            raw = (answers[-1].payload.get("answer") or "").strip().lower() if answers else ""
            defaulted = raw == ""
            strategy = ("simpler_problem" if "simple" in raw
                        else "alternate_method" if "alternate" in raw or "different" in raw
                        else "worked_example")
            ctx.append("human_choice", {
                "question_id": q["id"], "choice": strategy, "defaulted": defaulted,
            }, produced_by="system:timeout" if defaulted else "student")
            return RunState.PROBING

        # --- normal path: was the last attempt correct?
        attempt = ctx.history("attempt")[-1].payload
        if attempt["correct"]:
            idx = list(_QUESTIONS_BY_ID).index(q["id"])
            if idx + 1 >= len(QUESTIONS):
                return RunState.COMPLETE
            next_id = list(_QUESTIONS_BY_ID)[idx + 1]
            _present_question(ctx.store, ctx.run_id, ctx.settings, next_id, attempt_number=1)
            return RunState.AWAITING_EXPERT

        # --- wrong: count this error type's occurrences on THIS question
        cls = ctx.history("classification")[-1].payload
        error_type = cls["error_type"]
        occurrence = sum(
            1 for c in ctx.history("classification")
            if c.payload["question_id"] == q["id"] and c.payload["error_type"] == error_type
        )
        ctx.append("misconception", {
            "error_type": error_type, "question_id": q["id"], "occurrences": occurrence,
        }, produced_by="code:log_and_decide")

        if occurrence >= REPEATS_BEFORE_PAUSE:
            _present_choice(ctx.store, ctx.run_id, ctx.settings, q["id"])
            return RunState.AWAITING_EXPERT

        return RunState.PROBING

    def handle_probing(ctx) -> RunState:
        """reexplain. README.md section 8."""
        # question_id survives whether we arrived from a normal repeat or
        # from the choice pause - both write it under "id" on the "problem"
        # record we last appended. ("problem", not "question" - that kind
        # name is already used by slice/callback.py's own bookkeeping.)
        question_id = ctx.latest("problem")["id"]
        q = _QUESTIONS_BY_ID[question_id]

        cls = [c.payload for c in ctx.history("classification")
               if c.payload["question_id"] == question_id]
        error_type = cls[-1]["error_type"]
        already_tried = {
            r.payload["strategy"] for r in ctx.history("reexplanation")
            if r.payload["question_id"] == question_id
        }

        choices = [c.payload for c in ctx.history("human_choice")
                   if c.payload["question_id"] == question_id]
        forced = choices[-1]["choice"] if choices else None

        result: ReExplanation = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_reexplain_messages(q, error_type, already_tried, forced),
            schema=ReExplanation, step="reexplain",
        )
        payload = result.model_dump()
        payload["question_id"] = question_id
        ctx.append("reexplanation", payload, produced_by="agent:reexplain")

        last_attempt = [a.payload for a in ctx.history("attempt")
                        if a.payload["question_id"] == question_id][-1]
        _present_question(ctx.store, ctx.run_id, ctx.settings, question_id,
                          attempt_number=last_attempt["attempt_number"] + 1)
        return RunState.AWAITING_EXPERT

    return SimpleNamespace(
        name="tracker",
        handlers={
            RunState.DRAFTING: handle_drafting,
            RunState.GATING: handle_gating,
            RunState.PROBING: handle_probing,
        },
    )


def start_run(store, settings) -> str:
    """Create a run and present question 1. Call once per new student.

    callback.ask() (inside _present_question) already sets the state to
    AWAITING_EXPERT, so nothing further is needed here.
    """
    run_id = store.create_run("tracker")
    _present_question(store, run_id, settings, QUESTIONS[0]["id"], attempt_number=1)
    return run_id
