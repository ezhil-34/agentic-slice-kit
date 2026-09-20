"""
Tests for slice/callback.py — "what happens while waiting for a human?"

This is for moments the agent needs to ask a real person a question and pause.
It checks: asking a question correctly pauses the run, answering it correctly
resumes the run, an answer can only be given once (no double-answering), and if
nobody answers in time, the run doesn't get stuck forever — it records "no
answer" and moves on instead of guessing.

Runs entirely on stdlib sqlite3: no key, no network, no tokens.
"""
from __future__ import annotations

import time

import pytest

from slice import callback
from slice.config import Settings
from slice.records import RunState
from slice.store import Store


def _settings(**overrides) -> Settings:
    defaults = dict(
        api_key="", model="test", fallback_model="", escalation_model="",
        max_tokens=100, max_tokens_per_run=1000, max_attempts_per_step=3,
        expert_timeout_minutes=5, langfuse_public="", langfuse_secret="",
        langfuse_host="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _setup(tmp_path, **settings_kw):
    s = _settings(**settings_kw)
    store = Store(str(tmp_path / "cb.db"))
    run_id = store.create_run("test")
    store.set_state(run_id, RunState.DRAFTING)
    return store, run_id, s


# ----------------------------------------------------------- ask suspends

def test_ask_suspends_the_run(tmp_path):
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "What colour is the sky?", {"key": "val"}, s)
    assert qid.startswith("q_")
    assert store.get_state(run_id) is RunState.AWAITING_EXPERT


def test_ask_appends_a_question_record(tmp_path):
    store, run_id, s = _setup(tmp_path)
    callback.ask(store, run_id, "How many?", {"context": True}, s)
    h = store.history(run_id, "question")
    assert len(h) == 1
    assert h[0].payload["question"] == "How many?"
    assert h[0].produced_by == "system"


def test_ask_creates_an_open_question_in_the_store(tmp_path):
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "Q?", {}, s)
    q = store.get_question(qid)
    assert q is not None and not q.is_answered
    assert q.question == "Q?"


# --------------------------------------------------------- answer resumes

def test_answer_records_the_text_and_wakes_the_run(tmp_path):
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "Q?", {"resume_state": "probing"}, s)
    result = callback.answer(store, qid, "42", who="expert")
    assert result == run_id
    assert store.get_state(run_id) is RunState.PROBING
    q = store.get_question(qid)
    assert q.is_answered and q.answer == "42"


def test_answer_appends_expert_answer_record(tmp_path):
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "Q?", {"resume_state": "drafting"}, s)
    callback.answer(store, qid, "blue", who="teacher")
    h = store.history(run_id, "expert_answer")
    assert len(h) == 1
    p = h[0].payload
    assert p["answer"] == "blue" and p["who"] == "teacher"
    assert p["source"] == "human_expert"
    assert h[0].produced_by == "teacher"


def test_answer_to_unknown_question_returns_none(tmp_path):
    store, _, _ = _setup(tmp_path)
    assert callback.answer(store, "q_does_not_exist", "hello") is None


# --------------------------------------------------- write-once answers

def test_double_answer_is_ignored(tmp_path):
    """The first answer stands. A second answer to the same question does
    not overwrite it."""
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "Q?", {"resume_state": "drafting"}, s)
    callback.answer(store, qid, "first", who="expert_a")
    callback.answer(store, qid, "second", who="expert_b")
    q = store.get_question(qid)
    assert q.answer == "first"
    # Only one expert_answer record was written
    h = store.history(run_id, "expert_answer")
    assert len(h) == 1
    assert h[0].payload["answer"] == "first"


# -------------------------------------------------------- timeout sweep

def test_sweep_expires_timed_out_questions(tmp_path):
    store, run_id, s = _setup(tmp_path, expert_timeout_minutes=0)
    # timeout_minutes=0 means the question expires immediately
    qid = callback.ask(store, run_id, "Q?", {"resume_state": "probing"}, s)
    # Manually backdate the timeout so it's already expired
    store.db.execute(
        "UPDATE questions SET timeout_at=? WHERE id=?",
        (time.time() - 1, qid),
    )
    expired = callback.sweep(store, run_id)
    assert len(expired) == 1
    assert expired[0].id == qid
    # The question is now answered with empty text
    q = store.get_question(qid)
    assert q.is_answered and q.answer == ""
    # The run resumed
    assert store.get_state(run_id) is RunState.PROBING


def test_sweep_records_timeout_as_unresolved(tmp_path):
    store, run_id, s = _setup(tmp_path, expert_timeout_minutes=0)
    qid = callback.ask(store, run_id, "Q?", {"resume_state": "drafting"}, s)
    store.db.execute(
        "UPDATE questions SET timeout_at=? WHERE id=?",
        (time.time() - 1, qid),
    )
    callback.sweep(store, run_id)
    h = store.history(run_id, "expert_answer")
    # The sweep should have appended an expert_answer record
    timeout_answers = [a for a in h if a.payload.get("source") == "unresolved_no_expert"]
    assert len(timeout_answers) == 1
    assert timeout_answers[0].payload["answer"] is None
    assert timeout_answers[0].payload["who"] is None


def test_sweep_does_not_touch_non_expired_questions(tmp_path):
    store, run_id, s = _setup(tmp_path, expert_timeout_minutes=60)
    qid = callback.ask(store, run_id, "Q?", {}, s)
    expired = callback.sweep(store, run_id)
    assert expired == []
    assert not store.get_question(qid).is_answered


# --------------------------------------------------------- pending filter

def test_pending_returns_only_open_unexpired(tmp_path):
    store, run_id, s = _setup(tmp_path, expert_timeout_minutes=60)
    qid_open = callback.ask(store, run_id, "Open?", {}, s)
    store.set_state(run_id, RunState.DRAFTING)  # reset state for second ask

    qid_answered = callback.ask(store, run_id, "Answered?", {}, s)
    callback.answer(store, qid_answered, "yes")

    pending = callback.pending(store, run_id)
    assert len(pending) == 1
    assert pending[0].id == qid_open


def test_pending_excludes_expired_questions(tmp_path):
    store, run_id, s = _setup(tmp_path, expert_timeout_minutes=0)
    qid = callback.ask(store, run_id, "Q?", {}, s)
    store.db.execute(
        "UPDATE questions SET timeout_at=? WHERE id=?",
        (time.time() - 1, qid),
    )
    assert callback.pending(store, run_id) == []


# -------------------------------------------------- resume_state defaults

def test_answer_defaults_resume_state_to_probing(tmp_path):
    """If no resume_state is set in the question context, the run should
    resume to PROBING (the default in callback.py)."""
    store, run_id, s = _setup(tmp_path)
    qid = callback.ask(store, run_id, "Q?", {}, s)  # no resume_state
    callback.answer(store, qid, "answer")
    assert store.get_state(run_id) is RunState.PROBING
