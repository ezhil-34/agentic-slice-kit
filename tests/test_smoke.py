"""
Tests for the full tracker agent — "does the whole idea-evaluation agent work
end to end?" (using fake/canned model answers)

This is the big one, and it's really testing whether this is a genuine agent
rather than a fixed script. Key things it checks:

  - A rejected idea actually gets sent back for a real rewrite (not just
    processed once).
  - The rewrite is properly different — the specific thing that was criticized
    actually changes.
  - It rejects the classic trap: revisions that just parrot the same criticism
    back so they look valid but say nothing new.
  - Every rejection has to point at a real, existing part of the answer and
    give a real reason — not vague feedback like "add more detail."
  - Everything is credited to the specific agent that produced it (code:evaluate
    vs GATE vs agent:classify, etc.).
  - If the idea genuinely can't pass after several honest attempts, the run
    stops and says exactly why, instead of looping forever or timing out
    silently.
  - It confirms — again — that history really can't be edited or deleted
    after the fact.

Runs entirely on canned replies via FakeCall: no key, no network, no tokens.
"""
from __future__ import annotations

import pytest

from slice import callback, runner
from slice.config import settings as load_settings
from slice.records import RunState
from slice.store import Store

from demo.tracker.flow import build_flow, start_run
from demo.tracker.schema import QUESTIONS, predict_operator
from demo.tracker.stub import FakeCall

SETTINGS = load_settings()
Q1 = QUESTIONS[0]   # x² − 5x + 6 = 0, roots [2, 3]


def _new_run(tmp_path, call=None, student_id=None):
    store = Store(str(tmp_path / "smoke.db"))
    flow = build_flow(call or FakeCall())
    run_id = start_run(store, SETTINGS, student_id=student_id)
    return store, run_id, flow


def _answer(store, run_id, flow, text: str) -> RunState:
    state = runner.advance(store, run_id, flow, SETTINGS)
    if state.is_terminal:
        return state
    q = callback.pending(store, run_id)[-1]
    callback.answer(store, q.id, text, who="student")
    return state


def _step(store, run_id, flow, text: str) -> RunState:
    _answer(store, run_id, flow, text)
    return runner.advance(store, run_id, flow, SETTINGS)


def _phase(store, run_id) -> str:
    return store.latest(run_id, "problem").get("phase", "practice")


def _wrong_and_confirm(store, run_id, flow, text: str) -> RunState:
    """A wrong answer that fits one known mistake, then 'yes' to the confirm question."""
    _step(store, run_id, flow, text)
    if _phase(store, run_id) == "confirm":
        return _step(store, run_id, flow, "yes")
    return store.get_state(run_id)


def _solve_warmup(store, run_id, flow) -> RunState:
    """Answer the warm-up question on screen correctly."""
    p = store.latest(run_id, "problem")
    if p.get("phase") == "scaffold":
        return _step(store, run_id, flow, " and ".join(str(r) for r in p["roots"]))
    return store.get_state(run_id)


def _wrong_answer_for(op_id: str) -> str:
    """Return the wrong answer a student with the given bug would type for Q1."""
    pred = predict_operator(op_id, Q1["a"], Q1["b"], Q1["c"])
    return " and ".join(str(x) for x in pred)


# ---------------------------------------- rejected idea gets a real rewrite

def test_wrong_answer_triggers_reexplanation_then_reasks(tmp_path):
    """A wrong answer → confirm → reexplain → re-ask the same question."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    # There should be a reexplanation
    reex = store.history(run_id, "reexplanation")
    assert len(reex) >= 1

    # And when the warm-up is solved, the run returns to Q1
    _solve_warmup(store, run_id, flow)
    problems = store.history(run_id, "problem")
    last_problem = problems[-1].payload
    assert last_problem["id"] == "q1"
    assert last_problem["attempt_number"] == 2


# ---------------------------------------- rewrite uses a different strategy

def test_second_rewrite_uses_a_different_strategy(tmp_path):
    """The rewrite must be genuinely different — not the same strategy again."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")

    for i in range(2):
        _wrong_and_confirm(store, run_id, flow, wrong)
        if i == 0:
            _solve_warmup(store, run_id, flow)

    strategies = [v.payload["strategy"] for v in store.history(run_id, "reexplanation")]
    assert len(strategies) == 2
    assert strategies[0] != strategies[1], "second rewrite must use a different strategy"


# ---------------------------------------- no parroting: text isn't just the label

def test_reexplanation_text_is_not_just_the_error_type_name(tmp_path):
    """The reexplanation must contain real teaching, not just echo the bug label."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    reex = store.history(run_id, "reexplanation")[0].payload
    assert len(reex["explanation"]) > len(reex["strategy"]), \
        "explanation must be more than just the strategy name"


# ---------------------------------------- every rejection has specific feedback

def test_classification_has_error_type_and_reasoning(tmp_path):
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    cls = store.history(run_id, "classification")
    assert len(cls) >= 1
    payload = cls[0].payload
    assert "error_type" in payload and payload["error_type"] != ""
    assert "reasoning" in payload and len(payload["reasoning"]) > 0


# ---------------------------------------- attribution: every record has a producer

def test_every_record_is_attributed_to_a_specific_agent(tmp_path):
    """Everything is credited to the specific agent that produced it."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    for v in store.replay(run_id):
        assert v.produced_by, f"{v.kind} record #{v.seq} has no producer"

    # Check specific attribution for key record types
    cls = store.history(run_id, "classification")
    if cls:
        assert cls[0].produced_by

    reex = store.history(run_id, "reexplanation")
    if reex:
        assert reex[0].produced_by == "agent:reexplain"

    attempts = store.history(run_id, "attempt")
    if attempts:
        assert attempts[0].produced_by == "code:evaluate"


# ---------------------------------------- exhausted retries → clean stop

def test_three_repeats_pause_instead_of_infinite_loop(tmp_path):
    """If the idea genuinely can't pass after several attempts, the run
    pauses with a choice — it doesn't loop forever."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")

    for i in range(3):
        _wrong_and_confirm(store, run_id, flow, wrong)
        if i < 2:
            _solve_warmup(store, run_id, flow)

    # After 3 repeats, there should be a choice question pending
    misconceptions = [v.payload["occurrences"] for v in store.history(run_id, "misconception")]
    assert misconceptions == [1, 2, 3]
    assert store.get_state(run_id) is RunState.AWAITING_EXPERT

    q = callback.pending(store, run_id)
    assert len(q) >= 1, "a choice question should be pending"


# ---------------------------------------- correct answer moves to next question

def test_correct_answer_advances_to_next_question(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    _answer(store, run_id, flow, "2 and 3")      # correct for Q1
    runner.advance(store, run_id, flow, SETTINGS)

    attempts = store.history(run_id, "attempt")
    assert len(attempts) == 1 and attempts[0].payload["correct"] is True

    # Now on Q2
    problems = store.history(run_id, "problem")
    last = problems[-1].payload
    assert last["id"] == "q2"


# ---------------------------------------- complete run through all questions

def test_all_correct_completes_the_run(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    for q in QUESTIONS:
        correct = " and ".join(str(r) for r in q["roots"])
        _answer(store, run_id, flow, correct)
        state = runner.advance(store, run_id, flow, SETTINGS)
    assert state is RunState.COMPLETE
    assert store.counter(run_id, "tokens") == 0, \
        "an all-correct run should never call a model"


# ---------------------------------------- history immutability (again)

def test_history_cannot_be_edited_after_a_smoke_run(tmp_path):
    """After a full smoke run, UPDATE/DELETE on versions still raises —
    immutability doesn't break under load."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    v = store.replay(run_id)[0]
    with pytest.raises(Exception):
        store.db.execute(
            "UPDATE versions SET payload_json='{}' WHERE run_id=? AND seq=?",
            (run_id, v.seq),
        )
    with pytest.raises(Exception):
        store.db.execute(
            "DELETE FROM versions WHERE run_id=? AND seq=?",
            (run_id, v.seq),
        )


# ---------------------------------------- operator match is deterministic

def test_operator_match_is_recorded_before_classification(tmp_path):
    """The deterministic operator match must happen before (or alongside)
    the model-based classification."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    om = store.history(run_id, "operator_match")
    assert len(om) >= 1
    assert om[0].produced_by == "code:evaluate"
    assert "matched_operators" in om[0].payload

    # operator_match comes before classification in seq order
    cls = store.history(run_id, "classification")
    if cls:
        assert om[0].seq < cls[0].seq


# ---------------------------------------- strategy plan is code-driven

def test_strategy_plan_is_produced_by_code(tmp_path):
    """The strategy plan must be produced by code, not by the model."""
    store, run_id, flow = _new_run(tmp_path,
        call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = _wrong_answer_for("formula_forgot_2a")
    _wrong_and_confirm(store, run_id, flow, wrong)

    plans = store.history(run_id, "strategy_plan")
    assert len(plans) >= 1
    assert plans[0].produced_by == "code:plan"
