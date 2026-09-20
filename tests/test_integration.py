"""
Tests for real model integration — "does it actually work against the real
model, not just fakes?"

Every other test uses stand-ins. This one makes a real API call and checks
the response actually matches the strict format required, that spending is
actually counted correctly, and that a full mini-flow (draft → check →
complete) works against the live model, not just against a scripted stand-in.

It skips itself automatically if there's no API key, so it doesn't break
testing for someone without one.
"""
from __future__ import annotations

import os

import pytest

from slice.budget import Budget
from slice.config import settings as load_settings
from slice.store import Store

# Skip the entire module if no API key is available
_settings = load_settings()
_has_key = bool(_settings.api_key)
pytestmark = pytest.mark.skipif(not _has_key,
                                reason="No OPENROUTER_API_KEY set — skipping integration tests")


@pytest.fixture
def ctx(tmp_path):
    """A fresh store + budget + settings, ready for a real call."""
    store = Store(str(tmp_path / "int.db"))
    run_id = store.create_run("integration")
    budget = Budget(store, run_id, _settings)
    return store, run_id, budget


# ---------------------------------------- classify returns a valid schema

def test_real_classify_returns_valid_error_classification(ctx):
    from slice.llm import complete
    from demo.tracker.schema import ErrorClassification
    from demo.tracker.flow import build_classify_messages

    store, run_id, budget = ctx
    q = {"id": "q1", "text": "x² − 5x + 6 = 0", "a": 1, "b": -5, "c": 6,
         "roots": [2, 3], "known_error_patterns": []}
    msgs = build_classify_messages(q, "-2 and -3", ["formula_sign_flip"])

    result = complete(
        settings=_settings, budget=budget, messages=msgs,
        schema=ErrorClassification, step="classify",
    )
    assert isinstance(result, ErrorClassification)
    assert result.error_type in (
        "formula_sign_flip", "formula_forgot_2a",
        "factor_sign_flip", "factor_wrong_pair", "unclassified",
    )
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.reasoning) > 0


# ---------------------------------------- tokens are actually counted

def test_real_call_records_nonzero_tokens(ctx):
    from slice.llm import complete
    from demo.tracker.schema import ErrorClassification
    from demo.tracker.flow import build_classify_messages

    store, run_id, budget = ctx
    q = {"id": "q1", "text": "x² − 5x + 6 = 0", "a": 1, "b": -5, "c": 6,
         "roots": [2, 3], "known_error_patterns": []}
    msgs = build_classify_messages(q, "-2 and -3", ["formula_sign_flip"])

    complete(
        settings=_settings, budget=budget, messages=msgs,
        schema=ErrorClassification, step="classify",
    )
    assert budget.tokens_used() > 0, \
        "a real model call must record tokens spent"
    assert store.counter(run_id, "tokens") > 0


# ---------------------------------------- reexplain returns a valid schema

def test_real_reexplain_returns_valid_reexplanation(ctx):
    from slice.llm import complete
    from demo.tracker.schema import ReExplanation
    from demo.tracker.flow import build_reexplain_messages

    store, run_id, budget = ctx
    q = {"id": "q1", "text": "x² − 5x + 6 = 0", "a": 1, "b": -5, "c": 6,
         "roots": [2, 3], "known_error_patterns": []}
    msgs = build_reexplain_messages(q, "formula_sign_flip", set(), None)

    result = complete(
        settings=_settings, budget=budget, messages=msgs,
        schema=ReExplanation, step="reexplain",
    )
    assert isinstance(result, ReExplanation)
    assert result.strategy in ("worked_example", "alternate_method",
                                "simpler_problem", "real_world_example")
    assert len(result.explanation) > 0


# ---------------------------------------- mini end-to-end flow

def test_mini_flow_draft_classify_complete(ctx):
    """A full mini-flow: one wrong answer (classify it) then one correct
    answer (complete). Uses the real model for classify + reexplain."""
    from slice import callback, runner
    from demo.tracker.flow import build_flow, start_run

    store, run_id_unused, budget_unused = ctx
    # build_flow uses the real complete() by default
    flow = build_flow()
    run_id = start_run(store, _settings)

    # Answer Q1 wrong (negated roots)
    state = runner.advance(store, run_id, flow, _settings)
    if state.is_terminal:
        pytest.skip("run ended before we could interact")
    q = callback.pending(store, run_id)[-1]
    callback.answer(store, q.id, "-2 and -3", who="student")
    state = runner.advance(store, run_id, flow, _settings)

    # If the wrong answer triggered a collision (e.g. -2 and -3 matches both formula and factor sign flip),
    # answer the method check question to resolve the classification.
    if callback.pending(store, run_id):
        q_method = callback.pending(store, run_id)[-1]
        callback.answer(store, q_method.id, "quadratic formula", who="student")
        state = runner.advance(store, run_id, flow, _settings)

    # There should be at least a classification and reexplanation
    assert len(store.history(run_id, "classification")) >= 1
    assert len(store.history(run_id, "reexplanation")) >= 1

    # Now answer correctly
    if not state.is_terminal:
        q2 = callback.pending(store, run_id)[-1]
        callback.answer(store, q2.id, "2 and 3", who="student")
        state = runner.advance(store, run_id, flow, _settings)

    # Tokens were spent
    assert store.counter(run_id, "tokens") > 0
