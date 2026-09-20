"""
The Misconception Tracker's handlers, mapped onto the kit's existing states.

No spine edit was needed - all four active/suspended states this domain uses
(DRAFTING, GATING, PROBING, AWAITING_EXPERT) already exist in slice/records.py.
Mapping, matching README.md's own names for each step:

    DRAFTING  = evaluate (code) + operator match (code) + classify (model)
    GATING    = log_and_decide (code, the back-edge) - including the
                collision check and the method-check branch it can trigger
    PROBING   = reexplain (model)
    AWAITING_EXPERT = Presenting / Waiting for the student / the method-check
                       question - all three are "ask a person and suspend";
                       only the resume_state (and, for method-check, a tag on
                       the "problem" record) differs.

Every point where the student is asked anything - a fresh question, a retry
of the same question, the "which method did you use?" disambiguation, or the
pause after three repeats - goes through slice/callback.py's ask()/answer(),
so "Presenting" is never its own active state; it is what handle_gating and
handle_probing do on their way to suspending.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from slice import callback
from slice.llm import complete
from slice.records import RunState

from .plan import plan_strategy
from .schema import (BUG_LABELS, ERROR_TYPES, OPERATOR_METHOD, REPEATS_BEFORE_PAUSE, STRATEGIES,
                      ErrorClassification, QUESTIONS, ReExplanation, match_operators)

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

def _present_question(store, run_id, settings, question_id: str, attempt_number: int,
                       preface: str | None = None) -> None:
    q = _QUESTIONS_BY_ID[question_id]
    store.append(run_id, "problem", {
        "id": q["id"], "text": q["text"], "roots": q["roots"],
        "a": q["a"], "b": q["b"], "c": q["c"],
        "known_error_patterns": q["known_error_patterns"],
        "phase": "practice", "attempt_number": attempt_number,
    }, produced_by="system")
    text = f"Solve for x: {q['text']}"
    if preface:
        text = f"{preface}\n{text}"
    callback.ask(store, run_id, text, {"resume_state": "drafting"}, settings)


def _present_choice(store, run_id, settings, question_id: str) -> None:
    store.append(run_id, "problem", {"id": question_id, "phase": "choice"}, produced_by="system")
    text = ("You've made the same kind of mistake three times on this one. "
            "Do you want a fully worked example, or a simpler problem first?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


def _present_method_check(store, run_id, settings, question_id: str, candidates: list[str]) -> None:
    """The collision branch: the student's wrong answer matches more than one
    operator's prediction, so no amount of re-asking a question of this same
    shape can tell them apart. Instead of guessing, ask directly - the
    cheapest disambiguation move (MVP Build Context, section 2.1)."""
    store.append(run_id, "problem",
                 {"id": question_id, "phase": "method_check", "candidates": candidates},
                 produced_by="system")
    text = ("Your answer matches more than one kind of mistake, and the numbers alone "
            "can't tell them apart. Did you use factorization or the quadratic formula "
            "for this one?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


# --------------------------------------------------------------- messages

def build_classify_messages(question: dict, wrong_answer: str,
                             matched_operators: list[str]) -> list[dict]:
    """matched_operators is what code already found by checking the student's
    answer against every operator's deterministic prediction (schema.py's
    match_operators) - always 0 or 1 here, since 2+ (a collision) is
    intercepted before classify is ever called. classify's job is to explain
    why in words, not to re-derive a label the numbers already settled."""
    if len(matched_operators) == 1:
        candidate_note = (
            f"A deterministic check of the numbers already found this exact match: "
            f"{matched_operators[0]}. Confirm this label and explain, in one sentence, "
            f"what in the student's answer supports it.")
    else:
        candidate_note = (
            "No fixed operator matched this wrong answer by the numbers alone. "
            "Pick whichever of the four bug types fits best, or 'unclassified' "
            "if none genuinely does.")
    return [
        {"role": "system", "content": _prompt("classify")},
        {"role": "user", "content":
            f"Question: Solve for x: {question['text']}\n"
            f"Correct roots: {question['roots']}\n"
            f"Student's answer: {wrong_answer}\n"
            f"{candidate_note}"},
    ]


def build_reexplain_messages(question: dict, error_type: str, already_tried: set[str],
                              forced_strategy: str | None,
                              allowed: list[str] | None = None) -> list[dict]:
    """`allowed` is plan.plan_strategy's already-gated list; without it, every
    untried strategy is on offer (the pre-planner behaviour)."""
    remaining = allowed or [s for s in STRATEGIES if s not in already_tried] or list(STRATEGIES)
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
        """evaluate + operator match (code) + classify (model). README.md section 8,
        extended per MVP Build Context section 2.1-2.2."""
        q = ctx.latest("problem")
        answers = ctx.history("expert_answer")
        raw = answers[-1].payload.get("answer") or "" if answers else ""

        student_roots = parse_roots(raw)
        correct = roots_match(student_roots, set(q["roots"]))
        ctx.append("attempt", {
            "question_id": q["id"], "attempt_number": q["attempt_number"],
            "student_answer": raw, "correct": correct,
        }, produced_by="code:evaluate")

        if correct:
            return RunState.GATING

        # Deterministic first: does the wrong answer match a known operator's
        # prediction? This is never the model's opinion - see schema.py.
        matched = match_operators(q["a"], q["b"], q["c"], q["roots"], student_roots)
        ctx.append("operator_match", {
            "question_id": q["id"], "attempt_number": q["attempt_number"],
            "matched_operators": matched,
        }, produced_by="code:evaluate")

        if len(matched) >= 2:
            # A genuine collision (e.g. formula_sign_flip / factor_sign_flip
            # both fit). Nothing classify could say here would be more than a
            # guess dressed up as certainty - GATING will see this record and
            # ask the student directly instead of calling classify at all.
            return RunState.GATING

        result: ErrorClassification = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_classify_messages(q, raw, matched),
            schema=ErrorClassification, step="classify",
        )
        payload = result.model_dump()
        payload.update(question_id=q["id"], attempt_number=q["attempt_number"])
        ctx.append("classification", payload, produced_by="agent:classify")
        return RunState.GATING

    def _decide_after_classification(ctx, q: dict, error_type: str) -> RunState:
        """Shared by the normal wrong-answer path and the collision-resolved
        path below - both end up here once a bug label actually exists,
        whichever way it was determined."""
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

    def handle_gating(ctx) -> RunState:
        """log_and_decide. The back-edge lives here, plus (per MVP Build
        Context section 2.3) the collision check that can send a wrong
        answer to a method-check question instead of straight to reexplain."""
        q = ctx.latest("problem")

        # --- resumed from the method-check question: student named a method
        if q.get("phase") == "method_check":
            answers = ctx.history("expert_answer")
            raw = (answers[-1].payload.get("answer") or "").strip().lower() if answers else ""
            chosen_method = ("formula" if ("formula" in raw or "quadratic" in raw)
                              else "factorization" if "factor" in raw else None)
            candidates = q.get("candidates") or []
            resolved = next(
                (op for op in candidates if OPERATOR_METHOD.get(op) == chosen_method),
                candidates[0] if candidates else "unclassified",
            )
            ctx.append("method_choice", {
                "question_id": q["id"], "raw_answer": raw, "chosen_method": chosen_method,
                "resolved_operator": resolved, "defaulted": raw == "",
            }, produced_by="system:timeout" if raw == "" else "student")
            ctx.append("classification", {
                "error_type": resolved, "confidence": 1.0,
                "reasoning": f"Resolved by asking which method the student used "
                             f"({chosen_method or 'no answer given'}).",
                "question_id": q["id"], "attempt_number": q.get("attempt_number"),
            }, produced_by="code:log_and_decide")
            return _decide_after_classification(ctx, q, resolved)

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

        # --- wrong: a collision takes priority over an ordinary re-teach
        om = ctx.history("operator_match")[-1].payload
        if len(om["matched_operators"]) >= 2:
            _present_method_check(ctx.store, ctx.run_id, ctx.settings, q["id"],
                                  om["matched_operators"])
            return RunState.AWAITING_EXPERT

        cls = ctx.history("classification")[-1].payload
        return _decide_after_classification(ctx, q, cls["error_type"])

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

        # Code decides what is on offer - and says why - before the model
        # picks how to explain it. See plan.py: a change of solving method is
        # only offered when it is quicker for this equation.
        plan = plan_strategy(q, error_type, already_tried, forced)
        ctx.append("strategy_plan", {
            "question_id": question_id, "error_type": error_type,
            "already_tried": sorted(already_tried), **plan,
        }, produced_by="code:plan")

        result: ReExplanation = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_reexplain_messages(q, error_type, already_tried, forced,
                                              allowed=plan["allowed"]),
            schema=ReExplanation, step="reexplain",
        )
        payload = result.model_dump()
        payload["question_id"] = question_id
        # A model can ignore the list it was given. Don't rewrite what it said
        # (the label would no longer match the text) - just make it visible.
        if result.strategy not in plan["allowed"]:
            payload["off_plan"] = True
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


def start_run(store, settings, student_id: str | None = None) -> str:
    """Create a run and present question 1. Call once per new session.

    callback.ask() (inside _present_question) already sets the state to
    AWAITING_EXPERT, so nothing further is needed here.

    With a student_id (MVP Build Context section 2.4), this also folds in
    that student's misconception history from every one of their PAST runs
    before presenting question 1 - the "second encounter" proof: a returning
    student's new session visibly starts smarter, because the greeting names
    their most persistent past bug instead of pretending this is a stranger.
    """
    run_id = store.create_run("tracker")
    preface = None
    if student_id:
        from . import students  # local import: keeps students.py optional
        students.link_run(store, student_id, run_id)
        recurring = students.top_recurring(store, student_id, exclude_run_id=run_id)
        if recurring:
            error_type, count = recurring
            label = BUG_LABELS.get(error_type, error_type)
            ctx_note = f"Last time you had trouble with {label} ({count}x) - let's start there."
            store.append(run_id, "greeting", {
                "student_id": student_id, "error_type": error_type, "occurrences": count,
            }, produced_by="system")
            preface = ctx_note
    _present_question(store, run_id, settings, QUESTIONS[0]["id"], attempt_number=1,
                       preface=preface)
    return run_id
