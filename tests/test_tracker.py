"""
Tests for demo/tracker - the Misconception Tracker.

Runs entirely on canned replies via FakeCall: no key, no network, no tokens.
Mirrors tests/test_smoke.py's shape (a Store in a fresh tmp_path, drive the
loop, assert against what the store actually recorded) but drives a full
interactive session, since this domain's back-edge depends on what a
"student" answers at each pause, not just one straight-through advance().
"""
from __future__ import annotations

import pytest

from slice import callback, runner
from slice.config import settings as load_settings
from slice.records import RunState
from slice.store import Store

from demo.tracker import students
from demo.tracker.flow import build_flow, start_run
from demo.tracker.schema import QUESTIONS, collisions, match_operators, predict_operator
from demo.tracker.stub import FakeCall

SETTINGS = load_settings()
Q1 = QUESTIONS[0]  # x² − 5x + 6 = 0, roots [2, 3]


def _new_run(tmp_path, call=None, student_id: str | None = None):
    store = Store(str(tmp_path / "t.db"))
    flow = build_flow(call or FakeCall())
    run_id = start_run(store, SETTINGS, student_id=student_id)
    return store, run_id, flow


def _answer(store, run_id, flow, text: str) -> RunState:
    """Advance until suspended or finished, then answer whatever is open -
    the same two-step shape scripts/tracker.py's own loop uses."""
    state = runner.advance(store, run_id, flow, SETTINGS)
    if state.is_terminal:
        return state
    q = callback.pending(store, run_id)[-1]
    callback.answer(store, q.id, text, who="student")
    return state


def _finish_question(store, run_id, flow, question: dict) -> RunState:
    """Answer one question correctly and process it. Assumes it is current."""
    correct_text = " and ".join(str(r) for r in question["roots"])
    _answer(store, run_id, flow, correct_text)
    return runner.advance(store, run_id, flow, SETTINGS)


# --------------------------------------------------------- operator layer

def test_sign_flip_operators_collide_on_every_question():
    """The collision this whole project is built around is computed from the
    real question bank, not assumed - it must actually hold for all 10."""
    hits = collisions()
    pairs = {(a, b) for a, b, _ in hits}
    assert ("formula_sign_flip", "factor_sign_flip") in pairs
    hit_question_ids = {qid for a, b, qid in hits if {a, b} == {"formula_sign_flip", "factor_sign_flip"}}
    assert hit_question_ids == {q["id"] for q in QUESTIONS}


def test_match_operators_finds_the_collision():
    negated = {-r for r in Q1["roots"]}
    matched = match_operators(Q1["a"], Q1["b"], Q1["c"], Q1["roots"], negated)
    assert set(matched) == {"formula_sign_flip", "factor_sign_flip"}


def test_match_operators_finds_forgot_2a_alone():
    forgot_2a = predict_operator("formula_forgot_2a", Q1["a"], Q1["b"], Q1["c"])
    matched = match_operators(Q1["a"], Q1["b"], Q1["c"], Q1["roots"], forgot_2a)
    assert matched == ["formula_forgot_2a"]


def test_garbage_answer_falls_to_wrong_pair_catch_all():
    matched = match_operators(Q1["a"], Q1["b"], Q1["c"], Q1["roots"], {999.0, -999.0})
    assert matched == ["factor_wrong_pair"]


def test_correct_answer_matches_nothing():
    # match_operators is only ever called on a wrong answer in flow.py, but
    # it should never claim a match against the correct roots either way.
    matched = match_operators(Q1["a"], Q1["b"], Q1["c"], Q1["roots"], set(Q1["roots"]))
    assert matched == []


# ------------------------------------------------------------ the collision path

def test_collision_asks_which_method_instead_of_calling_classify(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    negated = " and ".join(str(-r) for r in Q1["roots"])
    _answer(store, run_id, flow, negated)
    runner.advance(store, run_id, flow, SETTINGS)

    # No classify call should have happened yet - the collision short-circuits it.
    assert store.history(run_id, "classification") == []
    om = store.history(run_id, "operator_match")[-1].payload
    assert set(om["matched_operators"]) == {"formula_sign_flip", "factor_sign_flip"}

    q = callback.pending(store, run_id)[-1]
    assert "factorization" in q.question.lower() and "quadratic formula" in q.question.lower()


@pytest.mark.parametrize("method_answer,expected_operator", [
    ("factorization", "factor_sign_flip"),
    ("the quadratic formula", "formula_sign_flip"),
])
def test_method_check_resolves_to_the_matching_operator(tmp_path, method_answer, expected_operator):
    store, run_id, flow = _new_run(tmp_path)
    negated = " and ".join(str(-r) for r in Q1["roots"])
    _answer(store, run_id, flow, negated)
    runner.advance(store, run_id, flow, SETTINGS)          # -> method_check question
    _answer(store, run_id, flow, method_answer)
    runner.advance(store, run_id, flow, SETTINGS)          # resolves + logs + reexplains

    choice = store.history(run_id, "method_choice")[-1].payload
    assert choice["resolved_operator"] == expected_operator
    assert choice["defaulted"] is False

    cls = store.history(run_id, "classification")[-1].payload
    assert cls["error_type"] == expected_operator
    assert cls["confidence"] == 1.0

    misc = store.history(run_id, "misconception")[-1].payload
    assert misc["occurrences"] == 1


def test_unanswered_method_check_defaults_without_getting_stuck(tmp_path):
    """An expired/empty answer to the method-check question must still let
    the run continue - same "nobody knew is a legitimate finding" principle
    slice/callback.py applies everywhere else."""
    store, run_id, flow = _new_run(tmp_path)
    negated = " and ".join(str(-r) for r in Q1["roots"])
    _answer(store, run_id, flow, negated)
    runner.advance(store, run_id, flow, SETTINGS)
    _answer(store, run_id, flow, "")                       # no method named
    state = runner.advance(store, run_id, flow, SETTINGS)

    choice = store.history(run_id, "method_choice")[-1].payload
    assert choice["defaulted"] is True
    assert choice["resolved_operator"] in ("formula_sign_flip", "factor_sign_flip")
    assert state in (RunState.AWAITING_EXPERT, RunState.PROBING, RunState.COMPLETE)


# ------------------------------------------------------ the ordinary path

def test_happy_path_completes_with_zero_model_calls(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    for q in QUESTIONS:
        state = _finish_question(store, run_id, flow, q)
    assert state is RunState.COMPLETE
    attempts = store.history(run_id, "attempt")
    assert len(attempts) == len(QUESTIONS)
    assert all(a.payload["correct"] for a in attempts)
    assert store.counter(run_id, "tokens") == 0, "an all-correct run should never call a model"


def test_three_repeats_trigger_pause_and_switch_strategy(tmp_path):
    """The single-operator (non-collision) path: same bug three times ->
    strategy changes each retry, then a pause with a direct question."""
    store, run_id, flow = _new_run(tmp_path, call=FakeCall(always_error_type="formula_forgot_2a"))
    forgot_2a = predict_operator("formula_forgot_2a", Q1["a"], Q1["b"], Q1["c"])
    wrong_text = " and ".join(str(x) for x in forgot_2a)

    for _ in range(3):
        _answer(store, run_id, flow, wrong_text)
        runner.advance(store, run_id, flow, SETTINGS)

    misconceptions = [v.payload["occurrences"] for v in store.history(run_id, "misconception")]
    assert misconceptions == [1, 2, 3]
    strategies = [v.payload["strategy"] for v in store.history(run_id, "reexplanation")]
    assert len(strategies) == 2, "the third repeat should pause, not reexplain a third time"
    assert strategies[0] != strategies[1], "reexplain must not repeat a tried strategy"

    q = callback.pending(store, run_id)[-1]
    assert "worked example" in q.question.lower() or "simpler" in q.question.lower()


def test_history_cannot_be_rewritten(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    _answer(store, run_id, flow, "not a number at all")
    runner.advance(store, run_id, flow, SETTINGS)
    v = store.history(run_id, "attempt")[0]
    with pytest.raises(Exception):
        store.db.execute("UPDATE versions SET payload_json='{}' WHERE run_id=? AND seq=?",
                          (run_id, v.seq))


def test_every_record_is_attributed_to_a_real_producer(tmp_path):
    store, run_id, flow = _new_run(tmp_path, call=FakeCall(always_error_type="formula_forgot_2a"))
    forgot_2a = predict_operator("formula_forgot_2a", Q1["a"], Q1["b"], Q1["c"])
    wrong_text = " and ".join(str(x) for x in forgot_2a)
    _answer(store, run_id, flow, wrong_text)
    runner.advance(store, run_id, flow, SETTINGS)
    for v in store.replay(run_id):
        assert v.produced_by, f"{v.kind} record #{v.seq} has no producer"


# --------------------------------------------------------------- students

def test_register_then_wrong_pin_is_rejected(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    sid = students.register_or_login(store, "Priya", "1234")
    assert sid == "priya"
    assert students.register_or_login(store, "priya", "1234") == "priya"
    with pytest.raises(students.LoginError):
        students.register_or_login(store, "priya", "0000")


def test_returning_student_is_greeted_with_the_worst_recurring_bug(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    sid = students.register_or_login(store, "priya", "1234")
    flow = build_flow(FakeCall())

    run_1 = start_run(store, SETTINGS, student_id=sid)
    negated = " and ".join(str(-r) for r in Q1["roots"])
    _answer(store, run_1, flow, negated)
    runner.advance(store, run_1, flow, SETTINGS)             # -> method_check
    _answer(store, run_1, flow, "the quadratic formula")
    runner.advance(store, run_1, flow, SETTINGS)             # resolves formula_sign_flip

    run_2 = start_run(store, SETTINGS, student_id=sid)
    greeting = store.history(run_2, "greeting")
    assert len(greeting) == 1
    assert greeting[0].payload["error_type"] == "formula_sign_flip"
    assert greeting[0].payload["occurrences"] == 1

    q = callback.pending(store, run_2)[-1]
    assert "last time" in q.question.lower()


def test_open_run_resumes_instead_of_starting_fresh(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    sid = students.register_or_login(store, "priya", "1234")
    run_1 = start_run(store, SETTINGS, student_id=sid)
    assert students.open_run_id(store, sid) == run_1


# ------------------------------------------------- the strategy planner

from demo.tracker.plan import method_effort, plan_strategy       # noqa: E402
from demo.tracker.schema import STRATEGIES                        # noqa: E402
from demo.tracker.trail import steps_from_records                 # noqa: E402

Q4 = QUESTIONS[3]     # 2x² − 5x + 3 = 0: a != 1, so factoring is the longer method


def test_factoring_is_quicker_only_for_a_equals_one_with_integer_roots():
    assert method_effort(Q1, "factorization") < method_effort(Q1, "formula")
    assert method_effort(Q4, "factorization") > method_effort(Q4, "formula")


def test_switching_method_is_offered_only_when_the_other_method_is_quicker():
    # formula student on Q1: factoring is quicker -> on offer, with the reason
    plan = plan_strategy(Q1, "formula_forgot_2a", set())
    assert "alternate_method" in plan["allowed"]
    assert plan["switch_method"]["worth_it"] is True and "quicker" in plan["reason"]

    # factoring student on Q1: the formula is slower -> NOT on offer, and it says so
    plan = plan_strategy(Q1, "factor_wrong_pair", set())
    assert "alternate_method" not in plan["allowed"]
    assert plan["switch_method"]["worth_it"] is False
    assert plan["reason"].startswith("Staying with factoring") and "longer" in plan["reason"]

    # on Q4 it reverses
    assert "alternate_method" in plan_strategy(Q4, "factor_wrong_pair", set())["allowed"]
    assert "alternate_method" not in plan_strategy(Q4, "formula_forgot_2a", set())["allowed"]


def test_unknown_method_never_pushes_a_switch():
    plan = plan_strategy(Q1, "unclassified", set())
    assert "alternate_method" not in plan["allowed"]
    assert "can't tell which method" in plan["reason"]


def test_a_student_asked_strategy_skips_the_gate():
    plan = plan_strategy(Q1, "factor_wrong_pair", set(), forced="alternate_method")
    assert plan["allowed"] == ["alternate_method"] and "You asked" in plan["reason"]


def test_planner_never_returns_an_empty_list_and_includes_real_world_example():
    assert "real_world_example" in STRATEGIES
    tried = set(STRATEGIES)                      # everything tried once already
    plan = plan_strategy(Q1, "factor_wrong_pair", tried)
    assert plan["allowed"] and "alternate_method" not in plan["allowed"]
    only_switch_left = set(STRATEGIES) - {"alternate_method"}
    assert plan_strategy(Q1, "factor_wrong_pair", only_switch_left)["allowed"]


def test_a_repeat_on_a_slower_alternate_method_keeps_the_method_and_changes_the_style(tmp_path):
    """factoring student, Q1: three ways of explaining, never the formula."""
    store, run_id, flow = _new_run(tmp_path, call=FakeCall(always_error_type="factor_wrong_pair"))
    for _ in range(2):
        _answer(store, run_id, flow, "999 and -999")
        runner.advance(store, run_id, flow, SETTINGS)
    strategies = [v.payload["strategy"] for v in store.history(run_id, "reexplanation")]
    assert len(strategies) == 2 and strategies[0] != strategies[1]
    assert "alternate_method" not in strategies
    plans = [v.payload for v in store.history(run_id, "strategy_plan")]
    assert [p["switch_method"]["worth_it"] for p in plans] == [False, False]
    assert all(v.produced_by == "code:plan" for v in store.history(run_id, "strategy_plan"))


def test_a_repeat_on_a_quicker_alternate_method_does_switch(tmp_path):
    """formula student, Q1: factoring is quicker, so the second re-teach uses it."""
    store, run_id, flow = _new_run(tmp_path, call=FakeCall(always_error_type="formula_forgot_2a"))
    wrong = " and ".join(str(x) for x in predict_operator("formula_forgot_2a", Q1["a"], Q1["b"], Q1["c"]))
    for _ in range(2):
        _answer(store, run_id, flow, wrong)
        runner.advance(store, run_id, flow, SETTINGS)
    strategies = [v.payload["strategy"] for v in store.history(run_id, "reexplanation")]
    assert strategies == ["worked_example", "alternate_method"]


def test_a_model_choice_outside_the_offered_list_is_flagged_not_rewritten(tmp_path):
    class Stubborn(FakeCall):
        def __call__(self, **kw):
            if kw.get("step") == "reexplain":
                from demo.tracker.schema import ReExplanation
                kw["budget"].record_tokens(1)
                return ReExplanation(strategy="alternate_method", explanation="switch!")
            return super().__call__(**kw)

    store, run_id, flow = _new_run(tmp_path, call=Stubborn(always_error_type="factor_wrong_pair"))
    _answer(store, run_id, flow, "999 and -999")
    runner.advance(store, run_id, flow, SETTINGS)
    re = store.history(run_id, "reexplanation")[-1].payload
    assert re["strategy"] == "alternate_method" and re["off_plan"] is True


# ------------------------------------------------------- the agent trail

def test_trail_for_a_correct_answer_is_evaluator_then_planner(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    _answer(store, run_id, flow, "2 and 3")
    runner.advance(store, run_id, flow, SETTINGS)
    steps = steps_from_records(store.replay(run_id))
    assert [(s.agent, s.doing) for s in steps] == [
        ("Evaluator", "Your answer is correct."), ("Planner", "Moving on to question 2.")]


def test_trail_only_covers_the_latest_turn(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    _answer(store, run_id, flow, "2 and 3")
    runner.advance(store, run_id, flow, SETTINGS)
    _answer(store, run_id, flow, "not a number")             # a fresh turn on question 2
    runner.advance(store, run_id, flow, SETTINGS)
    steps = steps_from_records(store.replay(run_id))
    assert steps[0].agent == "Evaluator" and "doesn't match" in steps[0].doing
    assert "question 2" not in " ".join(s.doing for s in steps)


def test_collision_trail_says_it_is_asking_about_the_method(tmp_path):
    store, run_id, flow = _new_run(tmp_path)
    _answer(store, run_id, flow, "-2 and -3")
    runner.advance(store, run_id, flow, SETTINGS)
    steps = steps_from_records(store.replay(run_id))
    assert [s.agent for s in steps] == ["Evaluator", "Diagnoser", "Planner"]
    assert "more than one kind of mistake" in steps[1].doing and "which method" in steps[2].doing
