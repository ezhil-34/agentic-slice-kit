"""
What a student has actually done, worked out from the stored records - for the
question tracker on the practice page and the whole progress page.

Everything here is read-only and derived: the run's own `attempt`,
`classification` and `misconception` records are the truth, nothing is cached.
Only graded records count. A custom-equation check (kind "custom_check", judged
by a model) is never read here, so it can't distort a student's numbers.

Colour vocabulary, used the same way everywhere on screen:

    green   solved first try / a mistake fully corrected, one-off
    orange  solved after retries / a mistake corrected but it kept coming back
    red     a mistake that is still unresolved
    blue    the question you are on now
    grey    skipped, or the answer was shown - not solved, but not still pending
"""
from __future__ import annotations

import time

from slice.store import Store

from . import students
from .schema import QUESTIONS, REPEATS_BEFORE_PAUSE

_Q_IDS = [q["id"] for q in QUESTIONS]


def question_outcomes(store: Store, run_id: str) -> dict[str, dict]:
    """Per question in one run: how many attempts, whether it was solved, and
    whether that was on the first try."""
    out: dict[str, dict] = {qid: {"attempts": 0, "solved": False, "first_try": False,
                                  "skipped": False, "revealed": False}
                            for qid in _Q_IDS}
    for a in store.history(run_id, "attempt"):
        o = out[a.payload["question_id"]]
        o["attempts"] += 1
        if a.payload["correct"] and not o["solved"]:
            o["solved"] = True
            o["first_try"] = o["attempts"] == 1
    # Left without solving: the student's own call, recorded by the flow.
    for kind, flag in (("skipped", "skipped"), ("reviewed", "revealed")):
        for r in store.history(run_id, kind):
            out[r.payload["question_id"]][flag] = True
    return out


def tile_status(outcome: dict, is_current: bool = False) -> str:
    """green / orange / skipped / current / todo - one word per question tile."""
    if outcome["solved"]:
        return "green" if outcome["first_try"] else "orange"
    if outcome.get("skipped") or outcome.get("revealed"):
        return "skipped"
    if is_current:
        return "current"
    return "todo"


def _bug_status(pairs: int, corrected: int, peak: int) -> str:
    if corrected < pairs:
        return "red"                       # still unresolved somewhere
    if peak >= 2 or pairs >= 2:
        return "orange"                    # fixed, but it kept coming back
    return "green"                         # a one-off, fixed


def run_summary(store: Store, run_id: str) -> dict:
    """One session on its own: totals, per-question outcomes, and the mistakes
    made in it (each with how many questions it hit and how many of those got
    corrected). `report` adds these up across sessions; the completion page
    shows one."""
    outcomes = question_outcomes(store, run_id)
    attempts = store.history(run_id, "attempt")
    bugs: dict[str, dict] = {}
    seen: dict[str, set[str]] = {}
    for c in store.history(run_id, "classification"):
        # A mistake made on a scaffold question counts against the REAL question
        # it was a warm-up for (root_id) - not a question that is never "solved".
        et = c.payload["error_type"]
        qid = c.payload.get("root_id", c.payload["question_id"])
        b = bugs.setdefault(et, {"hits": 0, "pairs": 0, "corrected": 0, "peak": 0})
        b["hits"] += 1
        if qid not in seen.setdefault(et, set()):
            seen[et].add(qid)
            b["pairs"] += 1
            b["corrected"] += bool(outcomes.get(qid, {}).get("solved"))
    for m in store.history(run_id, "misconception"):
        b = bugs.setdefault(m.payload["error_type"],
                            {"hits": 0, "pairs": 0, "corrected": 0, "peak": 0})
        b["peak"] = max(b["peak"], m.payload["occurrences"])
    for b in bugs.values():
        b["status"] = _bug_status(b["pairs"], b["corrected"], b["peak"])
        b["paused"] = b["peak"] >= REPEATS_BEFORE_PAUSE
    return {
        "outcomes": outcomes, "attempts": len(attempts),
        "correct": sum(1 for a in attempts if a.payload["correct"]),
        "solved": sum(1 for o in outcomes.values() if o["solved"]),
        "first_try": sum(1 for o in outcomes.values() if o["first_try"]),
        "recovered": sum(1 for o in outcomes.values() if o["solved"] and not o["first_try"]),
        "tried": sum(1 for o in outcomes.values() if o["attempts"]),
        "skipped": sum(1 for o in outcomes.values() if o["skipped"] and not o["solved"]),
        "revealed": sum(1 for o in outcomes.values() if o["revealed"] and not o["solved"]),
        "bugs": bugs,
    }


def report(store: Store, student_id: str) -> dict:
    """Everything the progress page shows, across all of a student's sessions."""
    run_ids = students.past_run_ids(store, student_id)
    times = students.run_times(store, student_id)
    tot = {"attempts": 0, "correct": 0, "solved": 0, "first_try": 0, "recovered": 0, "tried": 0}
    bugs: dict[str, dict] = {}
    sessions: list[dict] = []

    for run_id in run_ids:
        rs = run_summary(store, run_id)
        for k in tot:
            tot[k] += rs[k]
        for et, b in rs["bugs"].items():
            agg = bugs.setdefault(et, {"hits": 0, "pairs": 0, "corrected": 0, "peak": 0})
            agg["hits"] += b["hits"]
            agg["pairs"] += b["pairs"]
            agg["corrected"] += b["corrected"]
            agg["peak"] = max(agg["peak"], b["peak"])
        sessions.append({
            "run_id": run_id, "state": store.get_state(run_id).value,
            "started": times.get(run_id), "solved": rs["solved"],
            "first_try": rs["first_try"], "attempts": rs["attempts"],
        })
    for b in bugs.values():
        b["status"] = _bug_status(b["pairs"], b["corrected"], b["peak"])
        b["paused"] = b["peak"] >= REPEATS_BEFORE_PAUSE

    latest = run_ids[-1] if run_ids else None
    latest_outcomes = question_outcomes(store, latest) if latest else None
    grid = ([tile_status(latest_outcomes[q]) for q in _Q_IDS] if latest else ["todo"] * len(_Q_IDS))
    return {
        "sessions": sessions, "session_count": len(run_ids),
        "attempts": tot["attempts"], "correct": tot["correct"],
        "mastery": round(100 * tot["correct"] / tot["attempts"]) if tot["attempts"] else None,
        "solved": tot["solved"], "first_try": tot["first_try"], "recovered": tot["recovered"],
        "questions_tried": tot["tried"],
        "first_try_rate": round(100 * tot["first_try"] / tot["tried"]) if tot["tried"] else None,
        "bugs": bugs, "grid": grid, "latest_run": latest,
        "latest_solved": sum(1 for o in latest_outcomes.values() if o["solved"]) if latest else 0,
    }


def when(ts: float | None) -> str:
    return time.strftime("%d %b, %H:%M", time.localtime(ts)) if ts else "-"
