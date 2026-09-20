"""
The Misconception Tracker's handlers, mapped onto the kit's existing states.

No spine edit was needed - all four active/suspended states this domain uses
(DRAFTING, GATING, PROBING, AWAITING_EXPERT) already exist in slice/records.py.
Mapping, matching README.md's own names for each step:

    DRAFTING  = evaluate (code) + operator match (code); a wrong answer then
                asks the student something (confirm / which mistake / show a
                step) and suspends
    GATING    = log_and_decide (code): reads the student's reply to any of
                those questions, diagnoses, and decides what happens next
    PROBING   = pick a scaffold (code) + explain (model), then present it
    AWAITING_EXPERT = every "ask a person and suspend": a fresh question, a
                scaffold question, and the confirm / method-check /
                intermediate-step / pause questions. They differ only in the
                `phase` tag on the "problem" record and in `resume_state`.

What happens after a WRONG answer (one pipeline for a real question and for a
scaffold question - a wrong scaffold answer gets the same free checks):

    evaluate -> operator match (5 operators, code)
      0 matches          -> ask for ONE intermediate value
      1 match            -> confirm ("Looks like X - is that right?")
                              yes -> diagnosed; no -> ask for the intermediate value
      2+ matches         -> "which of these?"; "something else" -> intermediate value
    intermediate value   -> parse by code (anchored on a delta / discriminant line);
                            only unlabeled working goes to a narrow model EXTRACTION
                            call; code then compares the number. Still unclear, or
                            skipped -> the diagnostic agent (model: bug + confidence,
                            never the next step)
    diagnosed            -> log; three repeats, or two scaffold rounds already
                            spent -> the human pause; otherwise -> a fixed
                            scaffold question + an explanation of it -> evaluate

"skip" and "show me" are checked FIRST in every handler that reads a student's
reply, so a stuck student is never trapped at a stage that forgot to handle them.

Every point where the student is asked anything goes through slice/callback.py's
ask()/answer(), so "Presenting" is never its own active state; it is what the
handlers do on their way to suspending.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from slice import callback
from slice.llm import complete
from slice.records import RunState

from .ladder import (check_discriminant, check_factor_pair, control_word, leaks_answer,
                      pick_scaffold, read_reply, skips_step, solution_text)
from .plan import plan_strategy
from .schema import (BUG_INFO, BUG_LABELS, ERROR_TYPES, ESCALATION_ROUNDS, NO_SOLUTION,
                      OPERATOR_METHOD, REPEATS_BEFORE_PAUSE, SCAFFOLD_BY_ID, STRATEGIES,
                      ErrorClassification, IntermediateValue, QUESTIONS, ReExplanation,
                      has_real_roots, match_operators)

_PROMPTS = Path(__file__).parent / "prompts"
_QUESTIONS_BY_ID = {q["id"]: q for q in QUESTIONS}
_YES = {"y", "yes", "yeah", "yep", "right", "correct", "that's right", "thats right"}


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


# ------------------------------------------------------------ small helpers

def _key(cq: dict) -> str:
    """Which question a diagnosis is about: the scaffold on screen if there is
    one, else the real question. Keeps a scaffold's mistakes out of the real
    question's per-question counters."""
    return cq.get("scaffold_id") or cq["id"]


_CARRY = ("id", "scaffold_id", "text", "a", "b", "c", "roots", "attempt_number", "round")


def _carry(cq: dict) -> dict:
    """What a follow-up question needs to remember about the question it is
    about, so the page can still show that equation and the next handler can
    still grade against it."""
    return {k: cq[k] for k in _CARRY if k in cq}


def _latest_answer(ctx) -> str:
    answers = ctx.history("expert_answer")
    return (answers[-1].payload.get("answer") or "") if answers else ""


def _scaffold_rounds(ctx, root_id: str) -> list[str]:
    return [p.payload["scaffold_id"] for p in ctx.history("problem")
            if p.payload.get("phase") == "scaffold" and p.payload.get("id") == root_id]


def _latest_error_type(ctx, root_id: str) -> str:
    for c in reversed(ctx.history("classification")):
        if c.payload.get("root_id", c.payload["question_id"]) == root_id:
            return c.payload["error_type"]
    return "unclassified"


# --------------------------------------------------------------- presenting
# Not a state of its own - helpers the handlers call on their way to
# AWAITING_EXPERT. See module docstring.

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


def _present_scaffold(store, run_id, settings, root_id: str, scaffold: dict, round_no: int) -> None:
    """A fixed warm-up question. Its "problem" record keeps `id` = the REAL
    question's id (so progress, the page and the pause all still key off it)
    and names the scaffold in `scaffold_id`."""
    store.append(run_id, "problem", {
        "id": root_id, "scaffold_id": scaffold["id"], "text": scaffold["text"],
        "roots": scaffold["roots"], "a": scaffold["a"], "b": scaffold["b"], "c": scaffold["c"],
        "phase": "scaffold", "round": round_no, "attempt_number": 1,
    }, produced_by="system")
    callback.ask(store, run_id, f"A smaller one first, to build up to it.\nSolve for x: {scaffold['text']}",
                 {"resume_state": "drafting"}, settings)


def _present_choice(store, run_id, settings, question_id: str) -> None:
    store.append(run_id, "problem", {"id": question_id, "phase": "choice"}, produced_by="system")
    text = ("We've tried a few ways to help with this one. Do you want a fully worked "
            "example, a simpler problem first, or just to see the answer?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


def _present_confirm(store, run_id, settings, cq: dict, candidates: list[str], wrong_answer: str) -> None:
    """The numbers found a mistake pattern that fits. Code matching a
    prediction and a student having that bug are not the same claim - one
    templated question (no model call) turns "the numbers suggest X" into "the
    student confirms X". With two or more fits it is the collision branch: no
    amount of re-asking a question of this shape can tell them apart, so ask."""
    phase = "confirm" if len(candidates) == 1 else "method_check"
    store.append(run_id, "problem", {**_carry(cq), "phase": phase, "candidates": candidates,
                                     "wrong_answer": wrong_answer}, produced_by="system")
    if phase == "confirm":
        text = f"Looks like {BUG_LABELS[candidates[0]]} - is that right?"
    else:
        text = ("Your answer matches more than one kind of mistake, and the numbers alone "
                "can't tell them apart. Which of these is closest to what you did?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


def _present_intermediate(store, run_id, settings, cq: dict, candidates: list[str],
                           wrong_answer: str) -> None:
    store.append(run_id, "problem", {**_carry(cq), "phase": "intermediate_step",
                                     "candidates": candidates, "wrong_answer": wrong_answer},
                 produced_by="system")
    text = ("I can't pin your mistake down from the answer alone. What did you get for Δ (b² − 4ac)? "
            "Or, if you factored, which two numbers did you multiply?")
    callback.ask(store, run_id, text, {"resume_state": "gating"}, settings)


# --------------------------------------------------------------- messages

def build_classify_messages(question: dict, wrong_answer: str, matched_operators: list[str],
                             intermediate: str | None = None,
                             history: str | None = None) -> list[dict]:
    """The diagnostic agent's input: the question, the wrong answer, what the
    numbers found (and that the student rejected or could not choose between
    it), the student's typed working if any, and their history. It returns a bug
    and a confidence - never what happens next; that is code's job."""
    if matched_operators:
        found = (f"The numbers suggested: {', '.join(matched_operators)}. The student did not "
                 f"confirm it (rejected it, or could not choose between them).")
    else:
        found = "No fixed operator matched this wrong answer by the numbers alone."
    lines = [
        f"Question: Solve for x: {question['text']}",
        f"Correct roots: {question['roots']}",
        f"Student's answer: {wrong_answer}",
        found,
        f"Student's working: {intermediate}" if intermediate else "The student gave no working.",
    ]
    if history:
        lines.append(f"Student's history: {history}")
    lines.append("Pick whichever of the five bug types fits best, or 'unclassified' if none "
                 "genuinely does.")
    return [{"role": "system", "content": _prompt("classify")},
            {"role": "user", "content": "\n".join(lines)}]


def build_extract_messages(question: dict, reply: str) -> list[dict]:
    return [{"role": "system", "content": _prompt("extract")},
            {"role": "user", "content":
                f"Question: Solve for x: {question['text']}\nStudent's reply: {reply}"}]


def build_reexplain_messages(question: dict, error_type: str, already_tried: set[str],
                              forced_strategy: str | None,
                              allowed: list[str] | None = None,
                              scaffold: dict | None = None) -> list[dict]:
    """`allowed` is plan.plan_strategy's already-gated list; without it, every
    untried strategy is on offer (the pre-planner behaviour). `scaffold` is the
    smaller question the student will do next: the explanation is for that."""
    remaining = allowed or [s for s in STRATEGIES if s not in already_tried] or list(STRATEGIES)
    user = [
        f"Question: Solve for x: {question['text']}",
        f"Correct roots: {question['roots']}",
        f"The student's error type: {error_type}",
        f"Strategies already tried and rejected: {sorted(already_tried) or 'none'}",
    ]
    if scaffold:
        user.append(f"The student will now try this smaller question first: {scaffold['text']}. "
                    f"Explain the idea they need for it. Do NOT state the roots of the original "
                    f"question.")
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

    # ---- shared steps ------------------------------------------------------

    def _next_question(ctx, root_id: str) -> RunState:
        order = list(_QUESTIONS_BY_ID)
        idx = order.index(root_id)
        if idx + 1 >= len(order):
            return RunState.COMPLETE
        _present_question(ctx.store, ctx.run_id, ctx.settings, order[idx + 1], attempt_number=1)
        return RunState.AWAITING_EXPERT

    def _move_on(ctx, cq: dict, how: str) -> RunState:
        """The global rule. "skip" abandons the real question; "show me" shows
        the full worked solution (written by code from a/b/c, so it can never
        be wrong). Neither retries the question - pointless once seen."""
        root_id = cq["id"]
        if how == "skip":
            ctx.append("skipped", {"question_id": root_id}, produced_by="student")
        else:
            ctx.append("reviewed", {"question_id": root_id,
                                    "solution": solution_text(_QUESTIONS_BY_ID[root_id])},
                       produced_by="code:reveal")
        return _next_question(ctx, root_id)

    def _classified(ctx, cq: dict, error_type: str, confidence: float, reasoning: str,
                    produced_by: str) -> None:
        ctx.append("classification", {
            "error_type": error_type, "confidence": confidence, "reasoning": reasoning,
            "question_id": _key(cq), "root_id": cq["id"], "attempt_number": cq.get("attempt_number"),
        }, produced_by=produced_by)

    def _decide(ctx, cq: dict, error_type: str) -> RunState:
        """DIAGNOSED -> log, then either the human pause or a scaffold round.
        Two separate limits: the same bug three times on one question, and two
        scaffold rounds already spent - whichever comes first."""
        key = _key(cq)
        occurrence = sum(
            1 for c in ctx.history("classification")
            if c.payload["question_id"] == key and c.payload["error_type"] == error_type
        )
        ctx.append("misconception", {
            "error_type": error_type, "question_id": key, "occurrences": occurrence,
        }, produced_by="code:log_and_decide")

        if occurrence >= REPEATS_BEFORE_PAUSE or len(_scaffold_rounds(ctx, cq["id"])) >= ESCALATION_ROUNDS:
            _present_choice(ctx.store, ctx.run_id, ctx.settings, cq["id"])
            return RunState.AWAITING_EXPERT
        return RunState.PROBING

    def _history_note(ctx) -> str | None:
        from . import students          # local import: keeps students.py optional
        sid = students.run_owner(ctx.store, ctx.run_id)
        recurring = students.top_recurring(ctx.store, sid, exclude_run_id=ctx.run_id) if sid else None
        if not recurring:
            return None
        return f"most recurring past mistake: {recurring[0]} ({recurring[1]}x in earlier sessions)"

    def _diagnose(ctx, cq: dict, intermediate: str | None) -> RunState:
        """The diagnostic agent - only when the numbers, the student's confirmation
        and their working all failed to settle it."""
        result: ErrorClassification = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_classify_messages(cq, cq.get("wrong_answer", ""), cq.get("candidates", []),
                                             intermediate, _history_note(ctx)),
            schema=ErrorClassification, step="classify",
        )
        payload = result.model_dump()
        payload.update(question_id=_key(cq), root_id=cq["id"], attempt_number=cq.get("attempt_number"))
        ctx.append("classification", payload, produced_by="agent:classify")
        return _decide(ctx, cq, result.error_type)

    def _ask_intermediate(ctx, cq: dict, candidates: list[str]) -> RunState:
        _present_intermediate(ctx.store, ctx.run_id, ctx.settings, cq, candidates,
                              cq.get("wrong_answer", ""))
        return RunState.AWAITING_EXPERT

    # ---- DRAFTING: evaluate + operator match --------------------------------

    def handle_drafting(ctx) -> RunState:
        """Grade an answer to a real OR a scaffold question, the same way."""
        cq = ctx.latest("problem")
        raw = _latest_answer(ctx)
        ctrl = control_word(raw)
        if ctrl:
            return _move_on(ctx, cq, ctrl)

        scaffold = cq.get("phase") == "scaffold"
        key, n = _key(cq), cq["attempt_number"]
        student_roots = parse_roots(raw)
        claims_none = raw.strip().lower() == NO_SOLUTION
        real = has_real_roots(cq["a"], cq["b"], cq["c"])
        correct = (not real) if claims_none else (real and roots_match(student_roots, set(cq["roots"])))
        ctx.append("scaffold_attempt" if scaffold else "attempt", {
            "question_id": cq["id"], "attempt_number": n, "student_answer": raw, "correct": correct,
            **({"scaffold_id": cq["scaffold_id"]} if scaffold else {}),
        }, produced_by="code:evaluate")

        if correct:
            return RunState.GATING

        # Deterministic first: does the wrong answer match a known operator's
        # prediction? This is never the model's opinion - see schema.py.
        matched = match_operators(cq["a"], cq["b"], cq["c"], cq["roots"], student_roots)
        ctx.append("operator_match", {
            "question_id": key, "attempt_number": n, "matched_operators": matched,
        }, produced_by="code:evaluate")

        if claims_none or not raw.strip():
            # "No real solution" names no roots (and a timed-out question no
            # answer at all), so no operator can match and there is nothing to
            # confirm or ask about - settled by code, at full confidence.
            why = ("The student said there is no real solution, but the discriminant "
                   "b² − 4ac is not negative, so real roots exist." if claims_none
                   else "No answer was given.")
            _classified(ctx, cq, "unclassified", 1.0, why, "code:evaluate")
            return _decide(ctx, cq, "unclassified")

        if matched:
            _present_confirm(ctx.store, ctx.run_id, ctx.settings, cq, matched, raw)
        else:
            _present_intermediate(ctx.store, ctx.run_id, ctx.settings, cq, [], raw)
        return RunState.AWAITING_EXPERT

    # ---- GATING: read the student's reply, diagnose, decide -----------------

    def _resolve_confirm(ctx, cq: dict, raw: str) -> RunState:
        candidates = cq["candidates"]
        t = raw.strip().lower()
        yes = t in _YES or t.startswith("yes")
        ctx.append("confirmation", {
            "question_id": _key(cq), "candidates": candidates, "raw_answer": raw,
            "confirmed": yes, "chosen": candidates[0] if yes else None, "defaulted": t == "",
        }, produced_by="system:timeout" if t == "" else "student")
        if yes:
            _classified(ctx, cq, candidates[0], 1.0, "Confirmed by the student.", "code:log_and_decide")
            return _decide(ctx, cq, candidates[0])
        return _ask_intermediate(ctx, cq, candidates)

    def _resolve_method_check(ctx, cq: dict, raw: str) -> RunState:
        candidates = cq["candidates"]
        t = raw.strip().lower()
        chosen = t if t in candidates else None
        if chosen is None:              # a method word ("factorization", "the quadratic formula")
            method = ("formula" if ("formula" in t or "quadratic" in t)
                      else "factorization" if "factor" in t else None)
            fits = [op for op in candidates if OPERATOR_METHOD.get(op) == method] if method else []
            chosen = fits[0] if len(fits) == 1 else None    # two on one method: a word can't decide
        ctx.append("method_choice", {
            "question_id": _key(cq), "raw_answer": raw, "chosen_method": OPERATOR_METHOD.get(chosen),
            "resolved_operator": chosen, "defaulted": t == "",
        }, produced_by="system:timeout" if t == "" else "student")
        if chosen:
            _classified(ctx, cq, chosen, 1.0, "Resolved by asking the student which of the "
                        "matching mistakes they made.", "code:log_and_decide")
            return _decide(ctx, cq, chosen)
        return _ask_intermediate(ctx, cq, candidates)       # "something else" / not sure / no answer

    def _extract_value(ctx, cq: dict, raw: str) -> float | None:
        """Extraction ONLY: pull the one number the student's unlabeled working
        claims for the discriminant, or null. It never judges - code compares."""
        result: IntermediateValue = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_extract_messages(cq, raw), schema=IntermediateValue, step="extract",
        )
        return result.value

    def _resolve_intermediate(ctx, cq: dict, raw: str) -> RunState:
        key = _key(cq)
        if skips_step(raw):
            ctx.append("intermediate_step", {
                "question_id": key, "raw_answer": raw, "kind": "skipped", "source": None,
                "verdict": None, "resolved_operator": None,
            }, produced_by="system:timeout" if not raw.strip() else "student")
            return _diagnose(ctx, cq, None)

        reading, source = read_reply(raw), "code"
        if reading["kind"] == "unlabeled":
            value = _extract_value(ctx, cq, raw)
            source = "model"
            if value is not None:
                reading = {"kind": "discriminant", "value": value}

        check, claim = None, None
        if reading["kind"] == "discriminant":
            claim = f"discriminant = {reading['value']:g}"
            check = check_discriminant(cq["a"], cq["b"], cq["c"], reading["value"])
        elif reading["kind"] == "factor_pair":
            p, q = reading["pair"]
            claim = f"factor pair = {p:g} and {q:g}"
            check = check_factor_pair(cq["a"], cq["b"], cq["c"], (p, q))
        operator = check["operator"] if check else None
        ctx.append("intermediate_step", {
            "question_id": key, "raw_answer": raw, "kind": reading["kind"], "source": source,
            "value": reading.get("value"), "pair": list(reading["pair"]) if "pair" in reading else None,
            "verdict": check["verdict"] if check else None, "resolved_operator": operator,
        }, produced_by="student")

        if operator:
            _classified(ctx, cq, operator, 1.0,
                        f"The student's working ({claim}) matches this mistake exactly.",
                        "code:intermediate")
            return _decide(ctx, cq, operator)
        note = f"'{raw.strip()[:200]}'"
        if claim:
            note += f" - read by code as {claim}, which is {check['verdict'].replace('_', ' ')}"
        return _diagnose(ctx, cq, note)

    def _resolve_choice(ctx, cq: dict, raw: str) -> RunState:
        raw = raw.strip().lower()
        defaulted = raw == ""
        strategy = ("simpler_problem" if "simple" in raw
                    else "alternate_method" if "alternate" in raw or "different" in raw
                    else "worked_example")
        ctx.append("human_choice", {
            "question_id": cq["id"], "choice": strategy, "defaulted": defaulted,
        }, produced_by="system:timeout" if defaulted else "student")
        return RunState.PROBING

    def handle_gating(ctx) -> RunState:
        """log_and_decide. Reads the reply to a follow-up question, or - when
        the answer was right - decides what comes next."""
        cq = ctx.latest("problem")
        phase = cq.get("phase", "practice")
        raw = _latest_answer(ctx)

        resolvers = {"confirm": _resolve_confirm, "method_check": _resolve_method_check,
                     "intermediate_step": _resolve_intermediate, "choice": _resolve_choice}
        if phase in resolvers:
            ctrl = control_word(raw)
            if ctrl:
                return _move_on(ctx, cq, ctrl)
            return resolvers[phase](ctx, cq, raw)

        # --- a correct answer (DRAFTING only sends those here)
        if phase == "scaffold":
            # Back to the ORIGINAL, a fresh attempt: the warm-up did its job.
            tries = [a.payload["attempt_number"] for a in ctx.history("attempt")
                     if a.payload["question_id"] == cq["id"]]
            _present_question(ctx.store, ctx.run_id, ctx.settings, cq["id"],
                              attempt_number=max(tries, default=0) + 1,
                              preface="Nice - that warm-up is done. Now back to the real question.")
            return RunState.AWAITING_EXPERT
        return _next_question(ctx, cq["id"])

    # ---- PROBING: scaffold (code) + explain (model) --------------------------

    def handle_probing(ctx) -> RunState:
        """pick a scaffold, then reexplain. README.md section 8."""
        # `id` on the latest "problem" record is always the REAL question's id,
        # whichever phase the student was in - see _present_scaffold.
        question_id = ctx.latest("problem")["id"]
        q = _QUESTIONS_BY_ID[question_id]

        error_type = _latest_error_type(ctx, question_id)
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

        # After the student chose how to continue, no more warm-ups: their
        # choice is the plan. Otherwise a fixed scaffold from the hand-checked
        # bank - never a model-made "easier" question.
        scaffold = None
        if not forced:
            used = set(_scaffold_rounds(ctx, question_id))
            scaffold, why = pick_scaffold(q, error_type, used)
            ctx.append("scaffold_pick", {
                "question_id": question_id, "scaffold_id": scaffold["id"] if scaffold else None,
                "round": len(used) + 1, "reason": why,
            }, produced_by="code:plan")

        result: ReExplanation = call(
            settings=ctx.settings, budget=ctx.budget,
            messages=build_reexplain_messages(q, error_type, already_tried, forced,
                                              allowed=plan["allowed"], scaffold=scaffold),
            schema=ReExplanation, step="reexplain",
        )
        payload = result.model_dump()
        payload["question_id"] = question_id
        # A model can ignore the list it was given. Don't rewrite what it said
        # (the label would no longer match the text) - just make it visible.
        if result.strategy not in plan["allowed"]:
            payload["off_plan"] = True
        if scaffold and leaks_answer(result.explanation, q["roots"]):
            # A warm-up explanation must not hand over the real answer. Swap in
            # the fixed text for this mistake and keep what the model wrote
            # on the record, where a replay shows it.
            info = BUG_INFO.get(error_type, BUG_INFO["unclassified"])
            payload.update(withheld=result.explanation, leaked_answer=True,
                           explanation=f"{info['what']} {info['tip']}")
        ctx.append("reexplanation", payload, produced_by="agent:reexplain")

        if scaffold:
            _present_scaffold(ctx.store, ctx.run_id, ctx.settings, question_id, scaffold,
                              round_no=len(used) + 1)
        else:
            tries = [a.payload["attempt_number"] for a in ctx.history("attempt")
                     if a.payload["question_id"] == question_id]
            _present_question(ctx.store, ctx.run_id, ctx.settings, question_id,
                              attempt_number=max(tries, default=0) + 1)
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
