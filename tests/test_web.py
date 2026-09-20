"""
Tests for web/student.py - the FastAPI app students actually use: one-page
practice with the agents' trail under the question, ownership of sessions,
input validation, the progress page, and the custom-equation check.

Like tests/test_tracker.py this runs on canned replies only. web.student picks
its model call at IMPORT time (a real one if .env holds a key), so the fixture
forces TRACKER_STUB=1 and reloads the module for every test - never trust the
developer's .env to keep a test off the network. Nothing here asserts on wall-
clock timing: the "model" blocks on a threading.Event the test controls, and
the test waits on the in-flight set, not on sleeps.
"""
from __future__ import annotations

import html
import importlib
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from slice import callback
from slice.llm import ModelError
from slice.records import RunState
from slice.store import Store

from demo.tracker import progress, students
from demo.tracker.custom import check_consistency
from demo.tracker.flow import build_flow, start_run
from demo.tracker.schema import BUG_LABELS, QUESTIONS, CustomCheck, predict_operator
from demo.tracker.stub import FakeCall

Q1 = QUESTIONS[0]  # x² − 5x + 6 = 0, roots [2, 3]
FORGOT_2A = " and ".join(str(x) for x in predict_operator("formula_forgot_2a", Q1["a"], Q1["b"], Q1["c"]))
CORRECT = "2 and 3"
JSON = {"X-Requested-With": "fetch"}


class BlockingCall(FakeCall):
    """A FakeCall whose classify step parks until the test releases it, so a
    test can look at the app while a model call is 'in flight'."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, **kw):
        if kw.get("step") == "classify":
            self.entered.set()
            assert self.release.wait(10), "the test never released the blocked call"
        return super().__call__(**kw)


class CrashingCall(FakeCall):
    def __call__(self, **kw):
        if kw.get("step") == "classify":
            raise ValueError("boom")          # not a ModelError: runner.advance won't catch it
        return super().__call__(**kw)


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_DB", str(tmp_path / "w.db"))
    monkeypatch.setenv("TRACKER_STUB", "1")
    monkeypatch.delenv("TRACKER_SECRET", raising=False)
    from web import student
    student = importlib.reload(student)
    assert isinstance(student._CALL, FakeCall), "web tests must never reach a real model"
    student.client = TestClient(student.app, follow_redirects=False)
    return student


def _login(web, name="priya", pin="1234", client=None) -> str:
    r = (client or web.client).post("/login", data={"username": name, "pin": pin})
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[-1]


def _wait_idle(web, run_id: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while web._is_working(run_id):
        assert time.monotonic() < deadline, "the background advance never finished"
        time.sleep(0.01)


def _answer(web, run_id: str, text: str, client=None):
    r = (client or web.client).post(f"/session/{run_id}/answer", data={"answer": text})
    _wait_idle(web, run_id)
    return r


def _page(web, run_id: str) -> str:
    r = web.client.get(f"/session/{run_id}")
    assert r.status_code == 200, r.text[:300]
    return r.text


def _store(web) -> Store:
    return Store(web.DB)


def _finish_all(web, run_id: str) -> None:
    for q in QUESTIONS:
        _answer(web, run_id, " and ".join(str(r) for r in q["roots"]))


# ------------------------------------------------ the turn: status + trail

def test_answer_returns_at_once_and_the_page_shows_the_agents_at_work(web, monkeypatch):
    blocking = BlockingCall(always_error_type="formula_forgot_2a")
    monkeypatch.setattr(web, "_FLOW", build_flow(blocking))
    run_id = _login(web)

    r = web.client.post(f"/session/{run_id}/answer", data={"answer": FORGOT_2A})
    assert r.status_code == 303 and r.headers["location"] == f"/session/{run_id}?play=1"

    assert blocking.entered.wait(10), "advance() never reached classify"
    status = web.client.get(f"/session/{run_id}/status").json()
    assert status["working"] is True and status["label"] == "Explaining what went wrong…"
    assert status["active"] == {"agent": "Diagnoser", "doing": "Explaining what went wrong…"}
    assert [x["agent"] for x in status["steps"]] == ["Evaluator", "Diagnoser"]

    # The same page, mid-turn: the answered question stays, the trail plays below it.
    mid = web.client.get(f"/session/{run_id}?play=1")
    assert mid.status_code == 200 and 'data-play="1"' in mid.text and "Agents at work" in mid.text
    assert FORGOT_2A in mid.text and Q1["text"] in mid.text
    assert '<form id="answer-form"' not in mid.text, "no second submit while the first is being processed"
    # A plain page load mid-advance must not start a second advance() on the run.
    assert 'data-play="1"' in web.client.get(f"/session/{run_id}").text

    blocking.release.set()
    _wait_idle(web, run_id)
    done = web.client.get(f"/session/{run_id}/status").json()
    assert done["working"] is False and done["active"] is None
    assert [x["agent"] for x in done["steps"]][-2:] == ["Tutor", "Planner"]
    assert "stub explanation" in _page(web, run_id)


def test_fetch_submit_answers_json_and_starts_the_turn(web):
    run_id = _login(web)
    r = web.client.post(f"/session/{run_id}/answer", data={"answer": CORRECT}, headers=JSON)
    assert r.status_code == 200 and r.json() == {"ok": True}
    _wait_idle(web, run_id)
    assert "Question 2 of 10" in _page(web, run_id)


def test_a_correct_answer_shows_two_steps_then_the_next_question(web):
    run_id = _login(web)
    _answer(web, run_id, CORRECT)
    steps = web.client.get(f"/session/{run_id}/status").json()["steps"]
    assert steps == [{"agent": "Evaluator", "doing": "Your answer is correct."},
                     {"agent": "Planner", "doing": "Moving on to question 2."}]
    page = _page(web, run_id)
    assert "Correct!" in page and "Question 2 of 10" in page and "solved first time" in page


def test_finishing_the_last_question_ends_the_trail_with_a_summary_step(web):
    run_id = _login(web)
    _finish_all(web, run_id)
    steps = web.client.get(f"/session/{run_id}/status").json()["steps"]
    assert steps[-1] == {"agent": "Planner",
                         "doing": "That was the last question - putting your summary together."}


def test_a_repeated_mistake_shows_every_agent_in_order(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)
    steps = web.client.get(f"/session/{run_id}/status").json()["steps"]
    assert [x["agent"] for x in steps] == [
        "Evaluator", "Diagnoser", "Diagnoser", "Planner", "Planner", "Tutor", "Planner"]
    assert "Factoring is quicker than the quadratic formula" in steps[4]["doing"]


def test_background_crash_still_clears_in_flight_and_marks_the_run_failed(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(CrashingCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)                      # returns only once idle again

    assert not web._is_working(run_id), "a crashed thread must not leave the poller spinning"
    status = web.client.get(f"/session/{run_id}/status").json()
    assert status["working"] is False and status["steps"][-1]["agent"] == "Runner"
    st = _store(web)
    assert st.get_state(run_id) is RunState.FAILED
    assert st.history(run_id, "failure")[-1].payload["kind"] == "crash"
    page = _page(web, run_id)
    assert "stopped before it could finish" in page and "Start a new session" in page
    assert "boom" in page and "<details" in page, "the raw error is tucked under 'Technical details'"


def test_a_second_advance_cannot_start_while_one_is_in_flight(web):
    assert web._claim("run_x") is True
    assert web._start_advance("run_x") is None
    assert web._claim("run_x") is False
    web._release("run_x")
    assert not web._is_working("run_x")


def test_status_labels_follow_the_newest_record(web):
    store = _store(web)
    run_id = store.create_run("tracker")
    assert web._status_label(store, run_id) == "Checking your answer…"
    for kind, payload, label in [
        ("expert_answer", {"answer": "1"}, "Checking your answer…"),
        ("attempt", {"correct": False}, "Checking your answer…"),
        ("operator_match", {"matched_operators": ["formula_forgot_2a"]}, "Explaining what went wrong…"),
        ("operator_match", {"matched_operators": ["formula_sign_flip", "factor_sign_flip"]},
         "Found more than one possible mistake…"),
        ("classification", {}, "Explaining what went wrong…"),
        ("misconception", {}, "Writing a different explanation…"),
        ("strategy_plan", {}, "Writing a different explanation…"),
        ("reexplanation", {}, "Writing a different explanation…"),
    ]:
        store.append(run_id, kind, payload, produced_by="system")
        assert web._status_label(store, run_id) == label, kind


# ------------------------------------------------ holes: ids, ownership, login

@pytest.mark.parametrize("bad", ["x%22%3C!--%3Cscript%3E", "run_nope", "run_000000000000"])
def test_bad_or_unknown_run_ids_are_a_clean_404_that_never_echoes_the_input(web, bad):
    _login(web)
    for path in (f"/session/{bad}", f"/session/{bad}/status"):
        r = web.client.get(path)
        assert r.status_code == 404
        assert "<script>" not in r.text and "run_nope" not in r.text
    assert web.client.post(f"/session/{bad}/answer", data={"answer": "1"}).status_code == 404


def test_a_session_belongs_to_its_student(web):
    mine = _login(web, "priya")
    stranger = TestClient(web.app, follow_redirects=False)
    r = stranger.get(f"/session/{mine}")
    assert r.status_code == 303 and r.headers["location"].startswith("/?error=")
    assert stranger.get(f"/session/{mine}/status").status_code == 401
    assert stranger.post(f"/session/{mine}/answer", data={"answer": CORRECT}, headers=JSON).status_code == 401
    other = TestClient(web.app, follow_redirects=False)
    _login(web, "sam", client=other)
    assert other.get(f"/session/{mine}").status_code == 303, "another logged-in student is a stranger too"
    # Nothing the strangers did touched priya's run.
    assert _store(web).history(mine, "expert_answer") == []


def test_an_anonymous_cli_run_stays_open_to_whoever_holds_its_id(web):
    run_id = start_run(_store(web), web._SETTINGS, None)
    assert TestClient(web.app, follow_redirects=False).get(f"/session/{run_id}").status_code == 200


@pytest.mark.parametrize("method,path", [("get", "/profile"), ("get", "/custom"), ("get", "/resume"),
                                         ("post", "/session/new")])
def test_pages_about_the_student_need_login(web, method, path):
    r = getattr(web.client, method)(path)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_a_forged_cookie_is_not_a_login(web):
    web.client.cookies.set("tracker_student", "priya.notarealsignature")
    assert web.client.get("/profile").headers["location"] == "/"


def test_login_with_an_awkward_username_round_trips_through_the_cookie(web):
    _login(web, name="Zoë O'Neil")
    r = web.client.get("/profile")
    assert r.status_code == 200 and html.escape("zoë o'neil") in r.text


def test_the_login_cookie_survives_a_server_restart(web):
    _login(web)
    cookie = web.client.cookies.get("tracker_student")
    fresh = importlib.reload(web)                      # a "restart": same DB, new process state
    c = TestClient(fresh.app, follow_redirects=False)
    c.cookies.set("tracker_student", cookie)
    assert c.get("/profile").status_code == 200


def test_logout_clears_the_cookie(web):
    _login(web)
    assert web.client.post("/logout").status_code == 303
    assert web.client.get("/profile").headers["location"] == "/"


def test_logged_in_visitors_skip_the_login_page(web):
    _login(web)
    assert web.client.get("/").headers["location"] == "/resume"


@pytest.mark.parametrize("name,pin,msg", [("newkid", "12", "4 to 8 digits"), ("newkid", "abcd", "4 to 8 digits"),
                                          ("", "1234", "empty"), ("x" * 40, "1234", "32 characters")])
def test_bad_registrations_are_refused_with_a_reason(web, name, pin, msg):
    r = web.client.post("/login", data={"username": name, "pin": pin})
    assert r.status_code == 303 and r.headers["location"].startswith("/?error=")
    assert msg in web.client.get(r.headers["location"]).text


def test_pins_are_stored_hashed_and_old_plaintext_ones_are_upgraded(web):
    _login(web, "priya", "1234")
    st = _store(web)
    stored = st.db.execute("SELECT pin FROM students WHERE student_id='priya'").fetchone()["pin"]
    assert stored.startswith("pbkdf2$") and "1234" not in stored
    st.db.execute("INSERT INTO students(student_id, pin, created_at) VALUES ('oldtimer', '4321', 0)")
    _login(web, "oldtimer", "4321")
    assert st.db.execute("SELECT pin FROM students WHERE student_id='oldtimer'").fetchone()["pin"].startswith("pbkdf2$")
    assert web.client.post("/login", data={"username": "oldtimer", "pin": "0000"}).headers["location"].startswith("/?error=")


def test_repeated_wrong_pins_are_throttled(web):
    _login(web, "priya", "1234")
    web.client.post("/logout")
    for _ in range(5):
        loc = web.client.post("/login", data={"username": "priya", "pin": "0000"}).headers["location"]
        assert "Wrong PIN" in web.client.get(loc).text
    r = web.client.post("/login", data={"username": "priya", "pin": "1234"})     # even the right one now
    assert "Too many wrong PINs" in web.client.get(r.headers["location"]).text


def test_security_headers_and_a_quiet_favicon(web):
    r = web.client.get("/")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert web.client.get("/favicon.ico").status_code == 204
    missing = web.client.get("/no/such/page")
    assert missing.status_code == 404 and "Page not found" in missing.text


def test_an_unexpected_error_is_a_friendly_page_not_a_stack_trace(web, monkeypatch):
    _login(web)
    monkeypatch.setattr(web.progress, "report", lambda *a, **k: 1 / 0)
    quiet = TestClient(web.app, follow_redirects=False, raise_server_exceptions=False)
    quiet.cookies.set("tracker_student", web.client.cookies.get("tracker_student"))
    r = quiet.get("/profile")
    assert r.status_code == 500 and "Something went wrong on our side" in r.text
    assert "ZeroDivisionError" not in r.text and "Traceback" not in r.text


# ------------------------------------------------ holes: what may be submitted

@pytest.mark.parametrize("text,msg", [("", "Type an answer first."), ("   ", "Type an answer first."),
                                      ("no idea", "as numbers"), ("9" * 300, "under 200")])
def test_unusable_answers_are_rejected_before_they_are_graded(web, text, msg):
    run_id = _login(web)
    r = web.client.post(f"/session/{run_id}/answer", data={"answer": text}, headers=JSON)
    assert r.status_code == 422 and msg in r.json()["error"]
    st = _store(web)
    assert st.history(run_id, "attempt") == [] and st.history(run_id, "expert_answer") == []
    assert len(callback.pending(st, run_id)) == 1, "the question stays open"


def test_without_javascript_a_rejected_answer_comes_back_as_a_message_on_the_page(web):
    run_id = _login(web)
    r = web.client.post(f"/session/{run_id}/answer", data={"answer": ""})
    assert r.status_code == 303 and r.headers["location"].endswith("?e=empty")
    banner = 'role="alert">Type an answer first.'
    assert banner in web.client.get(r.headers["location"]).text
    assert banner not in web.client.get(f"/session/{run_id}?e=<script>").text


def test_a_second_submit_while_working_is_refused_not_double_graded(web, monkeypatch):
    blocking = BlockingCall(always_error_type="formula_forgot_2a")
    monkeypatch.setattr(web, "_FLOW", build_flow(blocking))
    run_id = _login(web)
    web.client.post(f"/session/{run_id}/answer", data={"answer": FORGOT_2A}, headers=JSON)
    assert blocking.entered.wait(10)
    assert web.client.post(f"/session/{run_id}/answer", data={"answer": "2 and 3"}, headers=JSON).status_code == 409
    blocking.release.set()
    _wait_idle(web, run_id)
    assert len(_store(web).history(run_id, "attempt")) == 1


# ------------------------------------------------ the practice page

def test_the_first_page_shows_the_question_and_a_question_tracker(web):
    page = _page(web, _login(web))
    assert "Question 1 of 10" in page and Q1["text"] in page and "First attempt" in page
    assert page.count('class="tile') == 10 and "tile current" in page
    assert re.search(r'id="agents"[^>]*hidden', page), "no trail before the first answer"
    assert 'data-step-ms="1400"' in page and r"/\d/.test(val)" in page


def test_a_wrong_answer_keeps_the_same_question_and_mentions_the_students_answer_again(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)
    page = _page(web, run_id)
    assert "Question 1 of 10" in page and Q1["text"] in page, "same question, same page"
    assert "Attempt 2" in page                                            # pill: this is your second try
    assert "Your answers so far on this question" in page
    assert f"You answered <code>{FORGOT_2A}</code>" in page                # their earlier response, again
    assert html.escape(BUG_LABELS["formula_forgot_2a"]) in page            # ...with what was wrong
    assert "Tutor · worked example" in page and "Why this approach: Factoring is quicker" in page
    assert "tile current retry" in page
    # and the agents' trail for that turn is on the same page, under the question
    assert re.search(r'id="agents"(?![^>]*hidden)', page) and page.count('class="trail-row"') >= 6
    assert page.index('id="answer-form"') < page.index('id="agents"')


def test_answers_pile_up_in_the_history_and_a_correct_one_moves_on(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)
    _answer(web, run_id, "7 and 8")
    page = _page(web, run_id)
    assert "You answered <code>7 and 8</code>" in page and f"<code>{FORGOT_2A}</code>" in page
    _answer(web, run_id, CORRECT)
    nxt = _page(web, run_id)
    assert "Question 2 of 10" in nxt and "Correct!" in nxt and "solved on attempt 3" in nxt
    assert "tile orange" in nxt, "solved after retries is orange in the tracker"


def test_a_first_time_right_answer_turns_the_tracker_green(web):
    run_id = _login(web)
    _answer(web, run_id, CORRECT)
    assert "tile green" in _page(web, run_id)


def test_three_repeats_offer_a_choice_on_the_same_page_and_the_choice_is_honoured(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    for _ in range(3):
        _answer(web, run_id, FORGOT_2A)
    page = _page(web, run_id)
    assert 'data-phase="choice"' in page and "Question 1 of 10" in page
    assert "Show me a worked example" in page and "Try a simpler problem first" in page
    assert page.count('<li class="attempt">') == 3, "all three of their answers are listed"
    assert 'type="text" name="answer"' not in page, "the choice is buttons, not free text"

    web.client.post(f"/session/{run_id}/answer", data={"answer": "simpler problem"})
    _wait_idle(web, run_id)
    after = _page(web, run_id)
    assert 'data-phase="practice"' in after and "Tutor · simpler practice problem" in after
    assert "You asked for a simpler practice problem" in after


def test_a_sign_flip_asks_which_method_with_buttons_then_carries_on(web):
    run_id = _login(web)
    _answer(web, run_id, "-2 and -3")
    page = _page(web, run_id)
    assert 'data-phase="method_check"' in page and "fits more than one kind of mistake" in page
    for label in ("Factorization", "Quadratic formula", "Not sure"):
        assert f"<b>{label}</b>" in page
    assert "You answered <code>-2 and -3</code>" in page
    web.client.post(f"/session/{run_id}/answer", data={"answer": "factorization"}, headers=JSON)
    _wait_idle(web, run_id)
    after = _page(web, run_id)
    assert 'data-phase="practice"' in after and "Tutor · worked example" in after
    assert "Staying with factoring" in after


def test_finishing_shows_a_summary_and_practise_again_starts_a_fresh_session(web):
    run_id = _login(web)
    _finish_all(web, run_id)
    done = _page(web, run_id)
    assert "Session complete" in done and "10 of 10" in done and "Practise again" in done
    assert "A clean run" in done
    r = web.client.post("/session/new")
    assert r.status_code == 303 and r.headers["location"] != f"/session/{run_id}"
    assert "Question 1 of 10" in web.client.get(r.headers["location"]).text


def test_the_completion_page_names_the_mistakes_worked_through(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)
    _finish_all(web, run_id)
    done = _page(web, run_id)
    assert "Session complete" in done and "Dividing by the wrong number" in done and "Under control" in done


# ------------------------------------------------ progress page

def test_progress_for_a_student_with_no_attempts_yet(web):
    _login(web)
    page = web.client.get("/profile").text
    assert "Nothing here yet" in page and "My progress" in page


def test_progress_numbers_and_colours(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)          # Q1: wrong ...
    _answer(web, run_id, CORRECT)            # ... then right  -> orange, one mistake corrected
    _answer(web, run_id, "6 and 7")          # Q2 (roots -3, -4): wrong
    page = web.client.get("/profile").text
    rep = progress.report(_store(web), "priya")
    assert rep["attempts"] == 3 and rep["correct"] == 1 and rep["mastery"] == 33
    assert rep["recovered"] == 1 and rep["first_try"] == 0 and rep["latest_solved"] == 1
    assert "33%" in page and "Mistakes corrected" in page
    assert "tile orange" in page                                # Q1 needed a retry
    bug = rep["bugs"]["formula_forgot_2a"]                     # hit Q1 (corrected) and Q2 (open)
    assert (bug["pairs"], bug["corrected"], bug["status"]) == (2, 1, "red")
    assert "Needs work" in page and "corrected <b>1 of 2</b>" in page
    assert "Try this:" in page and "Work out 2a on its own line" in page


def test_a_repeated_but_fixed_mistake_is_orange_and_a_one_off_is_green(web, monkeypatch):
    monkeypatch.setattr(web, "_FLOW", build_flow(FakeCall(always_error_type="formula_forgot_2a")))
    run_id = _login(web)
    _answer(web, run_id, FORGOT_2A)
    _answer(web, run_id, FORGOT_2A)          # same bug twice on Q1 (peak 2)
    _answer(web, run_id, CORRECT)
    assert progress.report(_store(web), "priya")["bugs"]["formula_forgot_2a"]["status"] == "orange"
    assert "Getting there" in web.client.get("/profile").text

    sam = TestClient(web.app, follow_redirects=False)
    rid = _login(web, "sam", client=sam)
    _answer(web, rid, FORGOT_2A, client=sam)
    _answer(web, rid, CORRECT, client=sam)
    assert progress.report(_store(web), "sam")["bugs"]["formula_forgot_2a"]["status"] == "green"
    assert "Under control" in sam.get("/profile").text


def test_progress_aggregates_every_session(web):
    run_1 = _login(web)
    _answer(web, run_1, CORRECT)
    run_2 = web.client.post("/session/new").headers["location"].rsplit("/", 1)[-1]
    _answer(web, run_2, CORRECT)
    rep = progress.report(_store(web), "priya")
    assert rep["session_count"] == 2 and rep["attempts"] == 2 and rep["mastery"] == 100
    assert rep["first_try_rate"] == 100 and rep["latest_run"] == run_2
    assert web.client.get("/profile").text.count('class="sess"') == 2


def test_session_and_side_pages_link_to_each_other(web):
    run_id = _login(web)
    page = _page(web, run_id)
    assert 'href="/profile"' in page and 'href="/custom"' in page and 'href="/resume"' in page
    assert 'href="/resume"' in web.client.get("/profile").text and 'href="/resume"' in web.client.get("/custom").text
    assert web.client.get("/resume").headers["location"] == f"/session/{run_id}"


def test_resume_returns_to_the_unfinished_session_not_a_finished_one(web):
    run_1 = _login(web)
    _finish_all(web, run_1)
    assert web.client.get("/resume").headers["location"] == f"/session/{run_1}"     # only run, finished
    run_2 = start_run(_store(web), web._SETTINGS, "priya")
    assert web.client.get("/resume").headers["location"] == f"/session/{run_2}"


# ------------------------------------------------ custom check

def _custom(web, equation="x² − 5x + 6 = 0", answer=CORRECT):
    return web.client.post("/custom/check", data={"equation": equation, "answer": answer})


def test_custom_check_shows_the_models_own_roots_and_logs_a_distinct_record(web):
    run_id = _login(web)
    page = _custom(web)
    assert page.status_code == 200
    assert "✓ Correct" in page.text and "<code>2, 3</code>" in page.text and "stub feedback" in page.text

    rec = _store(web).history(run_id, "custom_check")
    assert len(rec) == 1 and rec[0].produced_by == "agent:custom_check"
    assert rec[0].payload["computed_roots"] == ["2", "3"]
    assert rec[0].payload["student_correct"] is True
    assert rec[0].payload["verdict_matches_own_roots"] is True
    assert rec[0].payload["student_id"] == "priya"


def test_custom_check_never_touches_the_graded_counts(web):
    run_id = _login(web)
    _custom(web, answer="9 and 9")
    _custom(web)
    store = _store(web)
    assert store.history(run_id, "attempt") == []
    assert store.history(run_id, "classification") == []
    assert store.history(run_id, "misconception") == []
    assert students.attempt_totals(store, "priya") == (0, 0)
    assert students.misconception_summary(store, "priya") == {}
    assert progress.report(store, "priya")["attempts"] == 0


def test_custom_check_does_not_go_through_runner_advance(web, monkeypatch):
    _login(web)

    def boom(*a, **k):
        raise AssertionError("custom check must not use runner.advance")
    monkeypatch.setattr(web.runner, "advance", boom)
    assert "✓ Correct" in _custom(web).text


def test_a_wrong_custom_answer_is_reported_wrong(web):
    _login(web)
    page = _custom(web, answer="4 and 5")
    assert "✗ Not quite" in page.text and "does not match the roots" not in page.text


def test_verdict_that_contradicts_the_models_own_roots_is_flagged_not_hidden(web, monkeypatch):
    run_id = _login(web)
    monkeypatch.setattr(web, "_CALL", lambda **kw: CustomCheck(
        computed_roots=["2", "3"], student_correct=True, feedback="looks fine"))
    page = _custom(web, answer="5 and 6")
    assert "✓ Correct" in page.text, "the model's verdict is shown as given, not silently overridden"
    assert "does not match the roots it wrote down" in page.text
    assert _store(web).history(run_id, "custom_check")[-1].payload["verdict_matches_own_roots"] is False


def test_model_failure_is_a_friendly_message_and_logs_nothing(web, monkeypatch):
    run_id = _login(web)

    def down(**kw):
        raise ModelError("provider unreachable")
    monkeypatch.setattr(web, "_CALL", down)
    page = _custom(web)
    assert page.status_code == 200 and "Could not check that one" in page.text
    assert _store(web).history(run_id, "custom_check") == []


def test_custom_input_is_escaped_and_bounded(web):
    _login(web)
    page = _custom(web, equation="<script>alert(1)</script>", answer="<b>")
    assert "<script>alert(1)</script>" not in page.text and "&lt;script&gt;" in page.text
    assert "Fill in both" in _custom(web, equation="", answer="").text
    assert "under 200" in _custom(web, equation="x" * 500).text


@pytest.mark.parametrize("roots,correct,answer,expected", [
    (["2", "3"], True, "x = 2 or x = 3", True),
    (["2", "3"], True, "5 and 6", False),           # verdict says right, roots say otherwise
    (["2", "3"], False, "5 and 6", True),
    (["-1/3", "1"], True, "1 and -1/3", True),
    (["1 + sqrt(2)"], True, "2.41", None),          # radical: numerically undecidable
    ([], False, "2", None),                         # no real roots stated
    (["2", "3"], True, "1+√2", None),               # student wrote a radical
    (["2", "3"], True, "no idea", None),            # nothing numeric to compare
])
def test_check_consistency(roots, correct, answer, expected):
    r = CustomCheck(computed_roots=roots, student_correct=correct, feedback="")
    assert check_consistency(r, answer) is expected


def test_stub_custom_check_reply_is_a_valid_schema_instance():
    from types import SimpleNamespace
    budget = SimpleNamespace(record_tokens=lambda n: None)
    out = FakeCall()(settings=None, budget=budget, schema=CustomCheck, step="custom_check",
                     messages=[{"role": "user", "content": "Equation: x\nStudent's answer: 2 and 3"}])
    assert isinstance(out, CustomCheck) and out.student_correct is True
