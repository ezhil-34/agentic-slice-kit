"""
Which ways of re-teaching are actually worth offering a student who just made
the same mistake again - decided by code, with a reason on record.

Changing HOW something is explained (a worked example, a simpler problem, a
real-world example) costs the student nothing. Changing the solving METHOD
(`alternate_method`: factoring instead of the formula, or the reverse) does:
it is a second procedure to learn in the middle of a question they are
already stuck on. So that one is only offered when the other method is
genuinely quicker for THIS equation, judged by `method_effort` below. When it
isn't, the planner says so and offers the other strategies instead. The model
still picks the explanation among what is offered; it never gets to argue
for a switch the numbers don't support.

`method_effort` is a rough, honest step count, not a pedagogy theory:

    formula        3 steps: discriminant, square root, both signs / 2a.
                   The same for every equation.
    factorization  2 steps when a = 1 and the roots are integers (find two
                   numbers with product c and sum -b, read off the roots);
                   4 otherwise (a != 1 needs the split-the-middle-term
                   method, or the roots aren't integers).
"""
from __future__ import annotations

from .schema import OPERATOR_METHOD, STRATEGIES, STRATEGY_LABELS

_METHOD_NAME = {"formula": "the quadratic formula", "factorization": "factoring"}


def method_effort(question: dict, method: str) -> int:
    if method == "formula":
        return 3
    if method == "factorization":
        integer_roots = all(abs(r - round(r)) < 1e-9 for r in question["roots"])
        return 2 if question["a"] == 1 and integer_roots else 4
    raise ValueError(f"unknown method {method!r}")


def _other(method: str) -> str:
    return "factorization" if method == "formula" else "formula"


def plan_strategy(question: dict, error_type: str, already_tried: set[str],
                  forced: str | None = None) -> dict:
    """Returns the plan: which strategies are on offer, and why.

    `forced` is a strategy the student explicitly asked for (the pause
    question) - that is their call, not ours, and skips the gate."""
    if forced:
        return {
            "allowed": [forced], "switch_method": None,
            "reason": f"You asked for a {STRATEGY_LABELS.get(forced, forced)}, so that is what comes next.",
        }

    current = OPERATOR_METHOD.get(error_type)
    if current is None:
        switch = {"worth_it": False, "current": None, "alternate": None}
        reason = ("I can't tell which method you used, so I won't push a new one - "
                  "trying a different way of explaining instead.")
    else:
        alt = _other(current)
        e_cur, e_alt = method_effort(question, current), method_effort(question, alt)
        worth = e_alt < e_cur
        switch = {"worth_it": worth, "current": current, "alternate": alt,
                  "effort_current": e_cur, "effort_alternate": e_alt}
        if worth:
            reason = (f"{_METHOD_NAME[alt].capitalize()} is quicker than {_METHOD_NAME[current]} "
                      f"for this equation ({e_alt} steps vs {e_cur}), so switching method is worth it.")
        else:
            reason = (f"Staying with {_METHOD_NAME[current]}: {_METHOD_NAME[alt]} would take longer "
                      f"here ({e_alt} steps vs {e_cur}), so I'll explain it a different way instead.")

    pool = [s for s in STRATEGIES if s not in already_tried] or list(STRATEGIES)
    allowed = [s for s in pool if s != "alternate_method" or switch["worth_it"]]
    # Everything left untried was the method switch and it isn't worth it:
    # go round again on the styles rather than force the switch.
    if not allowed:
        allowed = [s for s in STRATEGIES if s != "alternate_method"]
    return {"allowed": allowed, "switch_method": switch, "reason": reason}
