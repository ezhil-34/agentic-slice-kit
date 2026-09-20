"""
Tests for slice/runner.py — "does the control flow behave correctly, with no
model involved?"

This tests pure orchestration logic using fake steps, no real thinking
involved. It checks: a normal run moves through its steps in order, a paused
run truly stays paused and doesn't keep running in the background, a resumed
run picks up correctly, and — importantly — if something goes wrong (budget
blown, or a step spins without making progress, or a step is missing entirely)
the run stops and clearly records why it failed, instead of just crashing
silently.

Runs entirely on stdlib sqlite3: no key, no network, no tokens.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from slice import callback
from slice.budget import BudgetExceeded
from slice.config import Settings
from slice.llm import CapExhausted, ModelError, PoolExhausted
from slice.records import RunState
from slice.runner import Context, advance
from slice.store import Store


def _settings(**overrides) -> Settings:
    defaults = dict(
        api_key="", model="test", fallback_model="", escalation_model="",
        max_tokens=100, max_tokens_per_run=100000, max_attempts_per_step=3,
        expert_timeout_minutes=5, langfuse_public="", langfuse_secret="",
        langfuse_host="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_flow(handlers: dict, name: str = "test_flow"):
    return SimpleNamespace(name=name, handlers=handlers)


# --------------------------------------------------------- normal happy flow

def test_normal_flow_drafting_to_complete(tmp_path):
    """Handlers that march DRAFTING → GATING → COMPLETE."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: RunState.GATING,
        RunState.GATING: lambda ctx: RunState.COMPLETE,
    })

    state = advance(store, run_id, flow, s)
    assert state is RunState.COMPLETE
    assert store.get_state(run_id) is RunState.COMPLETE


def test_three_step_flow(tmp_path):
    """DRAFTING → GATING → PROBING → COMPLETE."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: RunState.GATING,
        RunState.GATING: lambda ctx: RunState.PROBING,
        RunState.PROBING: lambda ctx: RunState.COMPLETE,
    })

    state = advance(store, run_id, flow, s)
    assert state is RunState.COMPLETE


# ----------------------------------------------------------- pause + resume

def test_pause_stops_the_loop(tmp_path):
    """A handler that suspends to AWAITING_EXPERT must stop advance() there."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    def handle_drafting(ctx):
        callback.ask(ctx.store, ctx.run_id, "Need help?",
                     {"resume_state": "gating"}, ctx.settings)
        return RunState.AWAITING_EXPERT

    flow = _make_flow({
        RunState.DRAFTING: handle_drafting,
        RunState.GATING: lambda ctx: RunState.COMPLETE,
    })

    state = advance(store, run_id, flow, s)
    assert state is RunState.AWAITING_EXPERT
    assert store.get_state(run_id) is RunState.AWAITING_EXPERT


def test_resume_after_pause_picks_up(tmp_path):
    """Answer the question, then advance() again — it should continue."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    def handle_drafting(ctx):
        callback.ask(ctx.store, ctx.run_id, "Help?",
                     {"resume_state": "gating"}, ctx.settings)
        return RunState.AWAITING_EXPERT

    flow = _make_flow({
        RunState.DRAFTING: handle_drafting,
        RunState.GATING: lambda ctx: RunState.COMPLETE,
    })

    advance(store, run_id, flow, s)
    # Answer the question
    q = callback.pending(store, run_id)[0]
    callback.answer(store, q.id, "yes")
    # Resume
    state = advance(store, run_id, flow, s)
    assert state is RunState.COMPLETE


# --------------------------------------------------- budget exceeded → FAILED

def test_budget_exceeded_fails_with_budget_reason(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    def blow_budget(ctx):
        raise BudgetExceeded("token", 1000, 500)

    flow = _make_flow({RunState.DRAFTING: blow_budget})

    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    assert store.get_state(run_id) is RunState.FAILED
    failure = store.history(run_id, "failure")
    assert len(failure) == 1
    assert failure[0].payload["kind"] == "budget"


# -------------------------------------------------- no-progress → FAILED

def test_handler_returning_own_state_fails_with_no_progress(tmp_path):
    """A handler that returns its own state without suspending is a loop.
    The runner must detect this and stop."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: RunState.DRAFTING,  # infinite loop!
    })

    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    failure = store.history(run_id, "failure")
    assert len(failure) == 1
    assert failure[0].payload["kind"] == "no_progress"


# -------------------------------------------------- missing handler → FAILED

def test_missing_handler_fails_with_no_handler(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({})  # no handlers at all!

    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    failure = store.history(run_id, "failure")
    assert len(failure) == 1
    assert failure[0].payload["kind"] == "no_handler"


# --------------------------------------------------- max_steps → FAILED

def test_max_steps_exceeded_fails(tmp_path):
    """A flow that cycles between two states forever hits max_steps."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: RunState.GATING,
        RunState.GATING: lambda ctx: RunState.DRAFTING,
    })

    state = advance(store, run_id, flow, s, max_steps=6)
    assert state is RunState.FAILED
    failure = store.history(run_id, "failure")
    assert len(failure) == 1
    assert failure[0].payload["kind"] == "max_steps"


# ---------------------------------------------- model errors → FAILED

def test_model_error_fails_with_model_reason(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    def model_fails(ctx):
        raise ModelError("connection refused")

    flow = _make_flow({RunState.DRAFTING: model_fails})
    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    assert store.history(run_id, "failure")[0].payload["kind"] == "model"


def test_cap_exhausted_fails_with_cap_reason(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: (_ for _ in ()).throw(
            CapExhausted("cap reached")),
    })
    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    assert store.history(run_id, "failure")[0].payload["kind"] == "cap_exhausted"


def test_pool_exhausted_fails_with_pool_reason(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({
        RunState.DRAFTING: lambda ctx: (_ for _ in ()).throw(
            PoolExhausted("pool empty")),
    })
    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED
    assert store.history(run_id, "failure")[0].payload["kind"] == "pool_exhausted"


# ----------------------------------------- terminal state is a no-op

def test_advance_on_completed_run_returns_immediately(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    store.set_state(run_id, RunState.COMPLETE)
    s = _settings()

    invoked = []
    flow = _make_flow({
        RunState.COMPLETE: lambda ctx: (invoked.append(1), RunState.COMPLETE)[1],
    })

    state = advance(store, run_id, flow, s)
    assert state is RunState.COMPLETE
    assert invoked == [], "handler should NOT be called on a terminal state"


def test_advance_on_failed_run_returns_immediately(tmp_path):
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    store.set_state(run_id, RunState.FAILED)
    s = _settings()

    flow = _make_flow({})
    state = advance(store, run_id, flow, s)
    assert state is RunState.FAILED


# -------------------------------- failure records always have a producer

def test_all_failure_records_have_runner_as_producer(tmp_path):
    """Every failure record must be attributed to 'runner'."""
    store = Store(str(tmp_path / "r.db"))
    run_id = store.create_run("test")
    s = _settings()

    flow = _make_flow({})
    advance(store, run_id, flow, s)
    for f in store.history(run_id, "failure"):
        assert f.produced_by == "runner"
