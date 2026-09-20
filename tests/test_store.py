"""
Tests for slice/store.py — "does the memory work at all?"

This tests the database layer everything else sits on. It checks: a run
remembers its state, old versions of data aren't lost when new ones are added
("latest wins" but history is kept), you can't secretly edit or delete past
records (append-only, enforced by the database itself, not just good behavior),
and — most importantly — if the whole process crashes and restarts, a paused
run picks back up exactly where it left off. If this file fails, nothing else
can be trusted, because everything else depends on this memory being solid.

Runs entirely on stdlib sqlite3: no key, no network, no tokens.
"""
from __future__ import annotations

import sqlite3

import pytest

from slice.records import RunState
from slice.store import Store


# ------------------------------------------------------------ run lifecycle

def test_create_run_returns_a_unique_id(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    a = store.create_run("test_domain")
    b = store.create_run("test_domain")
    assert a.startswith("run_") and b.startswith("run_")
    assert a != b


def test_new_run_starts_in_drafting(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("test_domain")
    assert store.get_state(run_id) is RunState.DRAFTING


def test_set_state_round_trips(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    for state in (RunState.GATING, RunState.PROBING, RunState.AWAITING_EXPERT,
                  RunState.COMPLETE, RunState.FAILED):
        store.set_state(run_id, state)
        assert store.get_state(run_id) is state


def test_get_state_of_nonexistent_run_raises(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    with pytest.raises(KeyError, match="no such run"):
        store.get_state("run_does_not_exist")


def test_meta_round_trips(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d", meta={"student": "priya", "level": 3})
    m = store.meta(run_id)
    assert m["student"] == "priya" and m["level"] == 3


def test_meta_defaults_to_empty_dict(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    assert store.meta(run_id) == {}


def test_list_runs_ordered_newest_first(tmp_path):
    import time
    store = Store(str(tmp_path / "s.db"))
    a = store.create_run("d")
    time.sleep(0.01)
    b = store.create_run("d")
    runs = store.list_runs()
    ids = [r["id"] for r in runs]
    assert ids.index(b) < ids.index(a), "newest should be first"


# -------------------------------------------------------- append-only versions

def test_append_returns_incrementing_seq(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    s1 = store.append(run_id, "thesis", {"v": 1}, produced_by="agent_a")
    s2 = store.append(run_id, "thesis", {"v": 2}, produced_by="agent_a")
    s3 = store.append(run_id, "verdict", {"pass": True}, produced_by="agent_b")
    assert s1 == 1 and s2 == 2 and s3 == 3


def test_latest_returns_newest_of_kind(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    store.append(run_id, "thesis", {"v": 2}, produced_by="a")
    store.append(run_id, "verdict", {"pass": False}, produced_by="b")
    assert store.latest(run_id, "thesis")["v"] == 2
    assert store.latest(run_id, "verdict")["pass"] is False


def test_latest_returns_none_for_missing_kind(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    assert store.latest(run_id, "nonexistent") is None


def test_history_returns_all_versions_oldest_first(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    store.append(run_id, "thesis", {"v": 2}, produced_by="a")
    store.append(run_id, "thesis", {"v": 3}, produced_by="a")
    h = store.history(run_id, "thesis")
    assert len(h) == 3
    assert [v.payload["v"] for v in h] == [1, 2, 3]
    assert h[0].seq < h[1].seq < h[2].seq


def test_history_preserves_produced_by(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="SPOT")
    store.append(run_id, "verdict", {"pass": True}, produced_by="GATE")
    assert store.history(run_id, "thesis")[0].produced_by == "SPOT"
    assert store.history(run_id, "verdict")[0].produced_by == "GATE"


def test_replay_returns_all_kinds_in_order(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    store.append(run_id, "verdict", {"pass": False}, produced_by="b")
    store.append(run_id, "thesis", {"v": 2}, produced_by="a")
    r = store.replay(run_id)
    assert len(r) == 3
    assert [v.kind for v in r] == ["thesis", "verdict", "thesis"]
    assert r[0].seq < r[1].seq < r[2].seq


# ------------------------------------------ append-only: no UPDATE, no DELETE

def test_update_on_versions_is_rejected_by_trigger(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    with pytest.raises(Exception, match="append-only"):
        store.db.execute(
            "UPDATE versions SET payload_json='{}' WHERE run_id=? AND seq=1",
            (run_id,),
        )


def test_delete_on_versions_is_rejected_by_trigger(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    with pytest.raises(Exception, match="append-only"):
        store.db.execute(
            "DELETE FROM versions WHERE run_id=? AND seq=1",
            (run_id,),
        )


def test_history_is_immutable_after_multiple_appends(tmp_path):
    """Even after many writes, older records are untouchable."""
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    for i in range(5):
        store.append(run_id, "step", {"i": i}, produced_by="a")
    # Try to update the first record
    with pytest.raises(Exception):
        store.db.execute(
            "UPDATE versions SET payload_json='{\"i\": 999}' WHERE run_id=? AND seq=1",
            (run_id,),
        )
    # First record is still the original
    assert store.history(run_id, "step")[0].payload["i"] == 0


# ---------------------------------------- crash + restart: resume from disk

def test_resume_after_crash_preserves_state(tmp_path):
    """Simulate a crash: write data, close the store, open a fresh one on the
    same file. Everything must be exactly as it was."""
    db_path = str(tmp_path / "crash.db")
    store = Store(db_path)
    run_id = store.create_run("d")
    store.set_state(run_id, RunState.AWAITING_EXPERT)
    store.append(run_id, "thesis", {"v": 1}, produced_by="a")
    store.append(run_id, "thesis", {"v": 2}, produced_by="a")
    store.close()

    # "Restart": brand-new Store object, same file
    store2 = Store(db_path)
    assert store2.get_state(run_id) is RunState.AWAITING_EXPERT
    h = store2.history(run_id, "thesis")
    assert len(h) == 2
    assert h[0].payload["v"] == 1 and h[1].payload["v"] == 2
    store2.close()


def test_resume_preserves_counters(tmp_path):
    db_path = str(tmp_path / "crash.db")
    store = Store(db_path)
    run_id = store.create_run("d")
    store.bump(run_id, "tokens", 500)
    store.bump(run_id, "attempts:classify", 2)
    store.close()

    store2 = Store(db_path)
    assert store2.counter(run_id, "tokens") == 500
    assert store2.counter(run_id, "attempts:classify") == 2
    store2.close()


# ---------------------------------------------------------- counter operations

def test_bump_increments_and_returns_new_value(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    assert store.bump(run_id, "tokens", 100) == 100
    assert store.bump(run_id, "tokens", 50) == 150


def test_counter_returns_zero_for_unknown(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    assert store.counter(run_id, "nonexistent") == 0.0


def test_reset_counter_clears_only_that_counter(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    store.bump(run_id, "a", 10)
    store.bump(run_id, "b", 20)
    store.reset_counter(run_id, "a")
    assert store.counter(run_id, "a") == 0.0
    assert store.counter(run_id, "b") == 20.0


# ----------------------------------------------------------- question layer

def test_ask_and_get_question_round_trip(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    qid = store.ask(run_id, "What colour?", {"key": "val"}, timeout_minutes=30)
    q = store.get_question(qid)
    assert q is not None
    assert q.question == "What colour?" and q.context["key"] == "val"
    assert q.run_id == run_id
    assert not q.is_answered


def test_answer_marks_question_answered(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    qid = store.ask(run_id, "Q?", {}, timeout_minutes=30)
    store.answer(qid, "blue")
    q = store.get_question(qid)
    assert q.is_answered and q.answer == "blue"


def test_answer_is_write_once(tmp_path):
    """A second answer to the same question is silently ignored — the first
    answer stands, no overwrite."""
    store = Store(str(tmp_path / "s.db"))
    run_id = store.create_run("d")
    qid = store.ask(run_id, "Q?", {}, timeout_minutes=30)
    store.answer(qid, "first")
    store.answer(qid, "second")      # should be a no-op
    assert store.get_question(qid).answer == "first"


def test_open_questions_filters_by_run(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    r1 = store.create_run("d")
    r2 = store.create_run("d")
    store.ask(r1, "Q for r1", {}, timeout_minutes=30)
    store.ask(r2, "Q for r2", {}, timeout_minutes=30)
    assert len(store.open_questions(r1)) == 1
    assert store.open_questions(r1)[0].question == "Q for r1"
    assert len(store.open_questions()) == 2  # no filter: both


def test_get_question_returns_none_for_unknown(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    assert store.get_question("q_does_not_exist") is None
