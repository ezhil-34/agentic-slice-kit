"""
Tests for slice/budget.py — "does the leash hold?"

This checks the safety limits: a step can only be retried a fixed number of
times before it's cut off, one step's retry allowance can't be borrowed by
another step, and spending (tokens) is checked before a call is allowed to
start, not after it's too late. Crucially, it also checks these limits survive
a restart — because a limit that lives only in memory and resets when the
program restarts isn't a real limit.

At the end of testing, it automatically generates a `budget_report.txt` file
summarizing total token usage and call counts.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from slice.budget import Budget, BudgetExceeded
from slice.config import Settings
from slice.store import Store

# Global tracker for token usage and call counts across tests
_TOKEN_CALLS: list[dict[str, float]] = []


@pytest.fixture(autouse=True, scope="module")
def export_budget_report():
    yield
    # Write summary report to budget_report.txt after all module tests finish
    report_path = Path("test_reports/test_budget_report.txt")
    report_path.parent.mkdir(exist_ok=True)
    total_tokens = sum(c["tokens"] for c in _TOKEN_CALLS)
    total_calls = len(_TOKEN_CALLS)

    lines = [
        "==================================================",
        "             BUDGET TEST USAGE REPORT             ",
        "==================================================",
        f"Total Tokens Used  : {int(total_tokens)} tokens",
        f"Total Token Calls  : {total_calls} calls",
        "==================================================",
        "Call Breakdown:",
    ]
    for idx, c in enumerate(_TOKEN_CALLS, 1):
        lines.append(
            f"  Call #{idx:02d}: {int(c['tokens'])} tokens recorded (Cumulative in run: {int(c['cumulative'])} tokens)"
        )
    lines.append("==================================================")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def _settings(**overrides) -> Settings:
    """A Settings with small limits, easy to trip in a test."""
    defaults = dict(
        api_key="", model="test", fallback_model="", escalation_model="",
        max_tokens=100, max_tokens_per_run=1000, max_attempts_per_step=3,
        expert_timeout_minutes=5, langfuse_public="", langfuse_secret="",
        langfuse_host="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _budget(tmp_path, **settings_kw):
    s = _settings(**settings_kw)
    store = Store(str(tmp_path / "b.db"))
    run_id = store.create_run("test")
    budget = Budget(store, run_id, s)

    # Intercept record_tokens to track call counts and token usage
    orig_record_tokens = budget.record_tokens

    def record_tokens_logged(n: int) -> float:
        res = orig_record_tokens(n)
        _TOKEN_CALLS.append({"tokens": float(n), "cumulative": res})
        return res

    budget.record_tokens = record_tokens_logged
    return budget, store, run_id


# --------------------------------------------------------- per-step attempts

def test_attempt_counts_up_and_raises_at_the_limit(tmp_path):
    budget, _, _ = _budget(tmp_path, max_attempts_per_step=3)
    assert budget.attempt("classify") == 1
    assert budget.attempt("classify") == 2
    assert budget.attempt("classify") == 3
    with pytest.raises(BudgetExceeded, match="attempt") as exc_info:
        budget.attempt("classify")
    assert exc_info.value.kind == "attempt"
    assert exc_info.value.used == 4
    assert exc_info.value.limit == 3


def test_one_steps_retries_cannot_borrow_from_another(tmp_path):
    budget, _, _ = _budget(tmp_path, max_attempts_per_step=2)
    budget.attempt("classify")
    budget.attempt("classify")
    # classify is spent...
    with pytest.raises(BudgetExceeded):
        budget.attempt("classify")
    # ...but reexplain still has its full allowance
    assert budget.attempt("reexplain") == 1
    assert budget.attempt("reexplain") == 2


def test_attempts_read_without_bumping(tmp_path):
    budget, _, _ = _budget(tmp_path)
    budget.attempt("step_a")
    budget.attempt("step_a")
    assert budget.attempts("step_a") == 2
    # Reading didn't change the count
    assert budget.attempts("step_a") == 2


def test_reset_attempts_clears_only_that_step(tmp_path):
    budget, _, _ = _budget(tmp_path, max_attempts_per_step=2)
    budget.attempt("step_a")
    budget.attempt("step_a")
    budget.attempt("step_b")
    budget.reset_attempts("step_a")
    assert budget.attempts("step_a") == 0
    assert budget.attempts("step_b") == 1
    # step_a can be retried again from scratch
    assert budget.attempt("step_a") == 1


# ------------------------------------------------------------ token checking

def test_check_tokens_passes_when_under_limit(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=1000)
    budget.record_tokens(500)
    budget.check_tokens()    # should not raise


def test_check_tokens_raises_when_at_limit(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=1000)
    budget.record_tokens(1000)
    with pytest.raises(BudgetExceeded, match="token"):
        budget.check_tokens()


def test_check_tokens_raises_when_over_limit(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=1000)
    budget.record_tokens(1500)
    with pytest.raises(BudgetExceeded) as exc_info:
        budget.check_tokens()
    assert exc_info.value.kind == "token"
    assert exc_info.value.used == 1500
    assert exc_info.value.limit == 1000


def test_record_tokens_accumulates(tmp_path):
    budget, _, _ = _budget(tmp_path)
    budget.record_tokens(100)
    budget.record_tokens(200)
    budget.record_tokens(50)
    assert budget.tokens_used() == 350


def test_tokens_remaining_is_correct(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=1000)
    budget.record_tokens(300)
    assert budget.tokens_remaining() == 700


def test_tokens_remaining_never_negative(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=100)
    budget.record_tokens(200)
    assert budget.tokens_remaining() == 0


# ---------------------------------------------- limits survive a restart

def test_token_count_survives_restart(tmp_path):
    """Close the store, reopen — the token count is still there. A fence
    that resets on restart is not a fence."""
    db_path = str(tmp_path / "restart.db")
    s = _settings(max_tokens_per_run=1000)
    store = Store(db_path)
    run_id = store.create_run("test")
    b = Budget(store, run_id, s)
    b.record_tokens(600)
    store.close()

    store2 = Store(db_path)
    b2 = Budget(store2, run_id, s)
    assert b2.tokens_used() == 600
    assert b2.tokens_remaining() == 400
    store2.close()


def test_attempt_count_survives_restart(tmp_path):
    db_path = str(tmp_path / "restart.db")
    s = _settings(max_attempts_per_step=3)
    store = Store(db_path)
    run_id = store.create_run("test")
    b = Budget(store, run_id, s)
    b.attempt("classify")
    b.attempt("classify")
    store.close()

    store2 = Store(db_path)
    b2 = Budget(store2, run_id, s)
    assert b2.attempts("classify") == 2
    # One more attempt is allowed, then the limit kicks in
    assert b2.attempt("classify") == 3
    with pytest.raises(BudgetExceeded):
        b2.attempt("classify")
    store2.close()


# ---------------------------------------------------------------- summary

def test_summary_has_expected_keys(tmp_path):
    budget, _, _ = _budget(tmp_path, max_tokens_per_run=5000)
    budget.record_tokens(200)
    s = budget.summary()
    assert s["tokens_used"] == 200
    assert s["tokens_limit"] == 5000
    assert s["tokens_remaining"] == 4800
