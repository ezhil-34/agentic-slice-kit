"""
The student-facing app. Same machinery as scripts/tracker.py -
slice/callback.py's ask()/answer(), slice/runner.py's advance(), and
demo/tracker/flow.py's handlers - exposed over HTTP, so several students can
practise at once from their own browsers, each behind their own login.

    uvicorn web.student:app --port 8002

Point it at the same --db file scripts/tracker.py and web/dashboard.py use
(TRACKER_DB, default tracker.db) and all three see the same runs.

How a turn works, on ONE page:
  1. The student answers; the page posts it with fetch() and the form is
     replaced by "You answered ...".
  2. advance() runs on a background thread (its own Store: sqlite3
     connections must not cross threads). The page polls /status and plays
     the agents' steps under the question, one at a time, each held for at
     least STEP_MS so a person can read it.
  3. The page then reloads into the new state: the next question if the answer
     was right; the same question, with the student's earlier answers listed
     and the tutor's new explanation, if it wasn't.
Without JavaScript the same forms still post normally (the answer redirects to
?play=1, which plays the same trail server-rendered as far as it can).

Security notes: a session belongs to the student who started it (a signed
login cookie is checked on every session URL); run ids are validated before
they touch the database or the markup; PINs are hashed; every DB connection is
closed; answers are length-limited, and the practice form's two root boxes
accept numbers only (checked in the browser and again on the server).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import html
import json
import logging
import os
import re
import threading
import time
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from slice import callback, runner
from slice.budget import Budget, BudgetExceeded
from slice.config import settings as load_settings
from slice.llm import ModelError
from slice.records import RunState
from slice.store import Store

from demo.tracker import progress, students, trail
from demo.tracker.custom import check_consistency, custom_check_messages
from demo.tracker.flow import build_flow, start_run
from demo.tracker.ladder import control_word
from demo.tracker.schema import (BUG_INFO, BUG_LABELS, ERROR_TYPES, NO_SOLUTION, OPERATOR_METHOD,
                                 QUESTIONS, STRATEGY_LABELS, CustomCheck)
from web import ui
from web.ui import esc

log = logging.getLogger("tracker.web")

DB = os.environ.get("TRACKER_DB", "tracker.db")
app = FastAPI(title="Misconception Tracker - student", docs_url=None, redoc_url=None, openapi_url=None)

_SETTINGS = load_settings()
if os.environ.get("TRACKER_STUB") == "1" or not _SETTINGS.api_key:
    from demo.tracker.stub import FakeCall
    _CALL = FakeCall()
    _DEMO_MODE = True
else:
    from slice.llm import complete as _CALL
    _DEMO_MODE = False

_FLOW = build_flow(_CALL)
_QBY = {q["id"]: q for q in QUESTIONS}
_Q_ORDER = [q["id"] for q in QUESTIONS]

# How long each agent step stays on screen at minimum, even if the work behind
# it took a millisecond - long enough to read. TRACKER_STEP_MS to tune it.
STEP_MS = int(os.environ.get("TRACKER_STEP_MS", "1400"))
MAX_ANSWER_CHARS = 200
_RUN_ID = re.compile(r"^run_[0-9a-f]{12}$")


@contextlib.contextmanager
def _open():
    """A Store that is always closed again."""
    store = Store(DB)
    try:
        yield store
    finally:
        store.close()


# ------------------------------------------------------------ in-flight runs
# Which runs have an advance() going right now. Answering a question starts
# advance() on a background thread; /status reads this set to say "working" or
# not, and /session/{id} refuses to run a second advance() on a run already in it.

_IN_FLIGHT: set[str] = set()
_LOCK = threading.Lock()


def _claim(run_id: str) -> bool:
    with _LOCK:
        if run_id in _IN_FLIGHT:
            return False
        _IN_FLIGHT.add(run_id)
        return True


def _release(run_id: str) -> None:
    with _LOCK:
        _IN_FLIGHT.discard(run_id)


def _is_working(run_id: str) -> bool:
    with _LOCK:
        return run_id in _IN_FLIGHT


def _advance_worker(run_id: str) -> None:
    store = Store(DB)
    try:
        runner.advance(store, run_id, _FLOW, _SETTINGS)
    except Exception as e:  # noqa: BLE001 - runner.advance only converts model/budget errors
        # Anything else would leave the run parked mid-state with no record of
        # why, and the next page load would silently retry the same crash.
        log.exception("advance() crashed for %s", run_id)
        try:
            store.append(run_id, "failure",
                         {"kind": "crash", "detail": f"{type(e).__name__}: {e}"},
                         produced_by="runner")
            store.set_state(run_id, RunState.FAILED)
        except Exception:  # noqa: BLE001 - the release below must still happen
            pass
    finally:
        try:
            store.close()
        finally:
            _release(run_id)


def _start_advance(run_id: str) -> threading.Thread | None:
    """Run advance() for this run on a background thread. None if one is
    already running for it (that one will pick up the new answer's state)."""
    if not _claim(run_id):
        return None
    try:
        t = threading.Thread(target=_advance_worker, args=(run_id,), daemon=True)
        t.start()
    except BaseException:
        _release(run_id)
        raise
    return t


def _status_label(store: Store, run_id: str) -> str:
    """What is running right now, in words - see demo/tracker/trail.py."""
    return trail.active_step(store.replay(run_id)).doing


def _status_payload(store: Store, run_id: str) -> dict:
    """One poll's worth for the page. `working` is read FIRST: if it is already
    False, the worker has finished and the records read next are the complete
    turn - the page can then pace them out and move on."""
    working = _is_working(run_id)
    records = store.replay(run_id)
    steps = [s._asdict() for s in trail.steps_from_records(records)]
    if not working and records and store.get_state(run_id) is RunState.COMPLETE:
        steps.append(trail.Step("Planner", "That was the last question - putting your summary together.")._asdict())
    active = trail.active_step(records)
    return {"working": working, "label": active.doing if working else None,
            "active": active._asdict() if working else None, "steps": steps}


# -------------------------------------------------------------------- login
# A signed cookie carrying the student_id. Sessions are additionally checked
# against who owns them (_guard), so knowing a run id is not enough.

_COOKIE = "tracker_student"
_SECRET: bytes | None = None
_FAILS: dict[str, list[float]] = {}
_MAX_FAILS, _FAIL_WINDOW = 5, 300


def _secret() -> bytes:
    """TRACKER_SECRET if set, else a random secret kept in the database so
    login cookies survive a server restart."""
    global _SECRET
    if _SECRET is None:
        env = os.environ.get("TRACKER_SECRET")
        if env:
            _SECRET = env.encode()
        else:
            with _open() as store:
                _SECRET = students.app_secret(store).encode()
    return _SECRET


def _sign(student_id: str) -> str:
    return hmac.new(_secret(), student_id.encode(), hashlib.sha256).hexdigest()


def _current_student(request: Request) -> str | None:
    raw = request.cookies.get(_COOKIE, "")
    student_id, _, sig = raw.rpartition(".")
    if student_id and hmac.compare_digest(sig, _sign(student_id)):
        return student_id
    return None


def _throttled(key: str) -> bool:
    now = time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < _FAIL_WINDOW]
    _FAILS[key] = recent
    return len(recent) >= _MAX_FAILS


def _to_login(message: str | None = None) -> RedirectResponse:
    return RedirectResponse("/?" + urlencode({"error": message}) if message else "/", status_code=303)


def _guard(store: Store, request: Request, run_id: str, api: bool = False) -> Response | None:
    """None if this browser may use this run; otherwise the response to send.
    Unknown or malformed ids are 404 (never a 500, never markup); a run that
    belongs to a student needs that student's cookie. Anonymous runs (the CLI's
    --anonymous) stay open to whoever holds the id."""
    known = bool(_RUN_ID.match(run_id))
    if known:
        try:
            store.get_state(run_id)
        except KeyError:
            known = False
    if not known:
        return JSONResponse({"error": "not found"}, status_code=404) if api else _error_page(
            404, "That session doesn't exist", "The link may be old or mistyped.")
    owner = students.run_owner(store, run_id)
    if owner is not None and _current_student(request) != owner:
        return JSONResponse({"error": "sign in"}, status_code=401) if api else _to_login(
            "Please sign in to open that session.")
    return None


# -------------------------------------------------------------------- pages

def _page(title: str, body: str, *, student: str | None = None, active: str = "",
          script: str = "", main_attrs: str = "", wide: bool = False, status: int = 200) -> HTMLResponse:
    return HTMLResponse(ui.shell(title, body, student=student, active=active, script=script,
                                 main_attrs=main_attrs, wide=wide), status_code=status)


def _error_page(code: int, heading: str, detail: str) -> HTMLResponse:
    return _page(heading, f"""
      <div class="center"><div class="card">
        <p class="q-num">Error {code}</p><h1>{esc(heading)}</h1>
        <p class="muted">{esc(detail)}</p>
        <a class="btn" href="/">Back to the start</a>
      </div></div>""", status=code)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _error_page(404, "Page not found", "There's nothing at that address.")
    return _error_page(exc.status_code, "Something isn't right", "That request couldn't be handled.")


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return _error_page(500, "Something went wrong on our side",
                       "It has been logged. Your progress is saved - try again in a moment.")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
    return resp


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


# -------------------------------------------------------------------- login

@app.get("/", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if not error and _current_student(request):
        return RedirectResponse("/resume", status_code=303)
    banner = ('<p class="banner warn">Demo mode: canned tutor replies, no API key found.</p>'
              if _DEMO_MODE else "")
    error_html = f'<p class="banner err" role="alert">{esc(error[:200])}</p>' if error else ""
    return _page("Sign in", f"""
      <div class="center">
        <div class="login-hero">
          <span class="brand-mark"><svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 22l7-12 7 12z" fill="none" stroke="#fff" stroke-width="3" stroke-linejoin="round"/></svg></span>
          <h1>Quadratic equations, taught by agents that remember</h1>
          <p class="muted">Sign in to start, or pick up exactly where you left off.</p>
        </div>
        <div class="card">
          {banner}{error_html}
          <form method="post" action="/login" class="stack">
            <label class="small muted" for="username">Username</label>
            <input id="username" type="text" name="username" autocomplete="username" maxlength="32" required autofocus>
            <label class="small muted" for="pin">PIN (4 to 8 digits)</label>
            <input id="pin" type="password" name="pin" inputmode="numeric" autocomplete="current-password"
                   pattern="[0-9]{{4,8}}" minlength="4" maxlength="8" required>
            <button class="btn" type="submit">Continue</button>
          </form>
          <p class="hint small muted" style="margin:1rem 0 0;text-align:center">First time? Pick any username and PIN - that creates your account.</p>
        </div>
      </div>""")


@app.post("/login")
def login(username: str = Form(""), pin: str = Form("")):
    key = username.strip().lower()[:students.MAX_USERNAME]
    if _throttled(key):
        return _to_login("Too many wrong PINs for that username. Wait a few minutes and try again.")
    with _open() as store:
        try:
            student_id = students.register_or_login(store, username, pin)
        except students.LoginError as e:
            _FAILS.setdefault(key, []).append(time.time())
            return _to_login(str(e))
        _FAILS.pop(key, None)
        run_id = students.open_run_id(store, student_id) or start_run(store, _SETTINGS, student_id)
    resp = RedirectResponse(f"/session/{run_id}", status_code=303)
    resp.set_cookie(_COOKIE, f"{student_id}.{_sign(student_id)}", httponly=True, samesite="lax",
                    max_age=7 * 24 * 3600)
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(_COOKIE)
    return resp


@app.get("/resume")
def resume(request: Request):
    """Back to the practice questions from a side page (progress, custom).
    Goes to the student's unfinished session, else their most recent one."""
    student_id = _current_student(request)
    if student_id is None:
        return _to_login()
    with _open() as store:
        run_ids = students.past_run_ids(store, student_id)
        run_id = students.open_run_id(store, student_id) or (run_ids[-1] if run_ids else None)
    return RedirectResponse(f"/session/{run_id}" if run_id else "/", status_code=303)


@app.post("/session/new")
def new_session(request: Request):
    student_id = _current_student(request)
    if student_id is None:
        return _to_login()
    with _open() as store:
        run_id = start_run(store, _SETTINGS, student_id)
    return RedirectResponse(f"/session/{run_id}", status_code=303)


# ------------------------------------------------------------------ session

_ERRORS = {
    "empty": "Fill in both boxes, or tick “No real solution”.",
    "nodigit": "Enter your answers as numbers, e.g. 2 and 3 (fractions like 1/2 are fine).",
    "needboth": "Fill in both boxes, or tick “No real solution”.",
    "badnum": "Numbers only, please - e.g. 2, -3, 0.5 or 1/2.",
    "long": f"Keep it under {MAX_ANSWER_CHARS} characters.",
    "busy": "Still working on your last answer - one moment.",
    "done": "That question was already answered.",
}


# One root as typed into a box: an integer, a decimal, or a simple fraction.
_ROOT_BOX = re.compile(r"-?\d+(?:\.\d+)?(?:/[1-9]\d*)?")
_ROOT_BOX_MAX = 20


def _compose_answer(phase: str, answer: str, root1: str | None, root2: str | None,
                    no_solution: str) -> tuple[str, str | None]:
    """Turn the practice form's fields into the one answer string the flow
    grades: "<x1> and <x2>", or NO_SOLUTION. Returns (text, error code).

    A post with no root fields at all (a plain `answer`, as scripts and older
    clients send) passes through untouched to _validate_answer."""
    if control_word(answer):            # "skip" / "show me" work at every stage, exactly
        return answer.strip(), None
    if phase not in ("practice", "scaffold") or (root1 is None and root2 is None and not no_solution):
        return answer, None
    if no_solution:                     # the browser omits the (greyed-out) root boxes
        return NO_SOLUTION, None
    r1, r2 = (root1 or "").strip(), (root2 or "").strip()
    if not (r1 and r2):
        return "", "needboth"
    if not all(len(r) <= _ROOT_BOX_MAX and _ROOT_BOX.fullmatch(r) for r in (r1, r2)):
        return "", "badnum"
    return f"{r1} and {r2}", None


def _validate_answer(phase: str, text: str) -> str | None:
    """A code for what's wrong with this answer, or None. Cheap checks that
    keep a blank or non-numeric answer from being graded (and counted) as a
    wrong attempt, and keep a huge paste out of the model's prompt."""
    t = text.strip()
    if not t:
        return "empty"
    if len(t) > MAX_ANSWER_CHARS:
        return "long"
    if (phase in ("practice", "scaffold") and t.lower() != NO_SOLUTION and not control_word(t)
            and not re.search(r"\d", t)):
        return "nodigit"
    return None


@app.get("/session/{run_id}", response_class=HTMLResponse)
def session_page(request: Request, run_id: str, play: str = "", e: str = ""):
    with _open() as store:
        denied = _guard(store, request, run_id)
        if denied:
            return denied
        student = _current_student(request)

        if _is_working(run_id):
            return _busy_page(store, run_id, student)
        if play == "1":
            if trail.steps_from_records(store.replay(run_id)):
                return _busy_page(store, run_id, student)
            return RedirectResponse(f"/session/{run_id}", status_code=303)

        # Normally the answer's background advance() has already finished and
        # this is instant. If the run is mid-advance, don't start a second one.
        if not _claim(run_id):
            return _busy_page(store, run_id, student)
        try:
            state = runner.advance(store, run_id, _FLOW, _SETTINGS)
        finally:
            _release(run_id)

        if state is RunState.COMPLETE:
            return _page("Session complete", _complete_body(store, run_id), student=student,
                         active="practice", wide=True)
        if state is RunState.FAILED:
            return _failed_page(store, run_id, student)
        page = _practice_page(store, run_id, student, _ERRORS.get(e, ""))
        return page or _error_page(409, "Nothing to answer right now",
                                   "Refresh in a moment, or start a new session.")


@app.post("/session/{run_id}/answer")
def submit_answer(request: Request, run_id: str, answer: str = Form(""),
                  root1: str | None = Form(None), root2: str | None = Form(None),
                  no_solution: str = Form("")):
    wants_json = request.headers.get("x-requested-with") == "fetch"

    def reject(code: str, status: int = 422):
        if wants_json:
            return JSONResponse({"error": _ERRORS[code]}, status_code=status)
        return RedirectResponse(f"/session/{run_id}?e={code}", status_code=303)

    with _open() as store:
        denied = _guard(store, request, run_id, api=wants_json)
        if denied:
            return denied
        if _is_working(run_id):
            return reject("busy", 409)
        open_qs = callback.pending(store, run_id)
        if not open_qs:
            return reject("done", 409)
        phase = (store.latest(run_id, "problem") or {}).get("phase", "practice")
        answer, problem = _compose_answer(phase, answer, root1, root2, no_solution)
        problem = problem or _validate_answer(phase, answer)
        if problem:
            return reject(problem)
        callback.answer(store, open_qs[-1].id, answer.strip(), who="student")
        _start_advance(run_id)
    if wants_json:
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/session/{run_id}?play=1", status_code=303)


@app.get("/session/{run_id}/working")
def working_legacy(run_id: str):
    return RedirectResponse(f"/session/{quote(run_id, safe='')}?play=1", status_code=303)


@app.get("/session/{run_id}/status")
def session_status(request: Request, run_id: str):
    with _open() as store:
        denied = _guard(store, request, run_id, api=True)
        if denied:
            return denied
        return JSONResponse(_status_payload(store, run_id))


# ---------------------------------------------------------- session: views

def _main_attrs(run_id: str, phase: str, play: bool) -> str:
    return (f'data-run="{esc(run_id)}" data-phase="{esc(phase)}" data-play="{1 if play else 0}" '
            f'data-step-ms="{STEP_MS}" data-agents="{esc(_agents_json())}"')


def _agents_json() -> str:
    return json.dumps(ui.AGENTS)


def _q_num(qid: str | None) -> int | None:
    return _Q_ORDER.index(qid) + 1 if qid in _Q_ORDER else None


def _agents_panel(steps, hidden: bool, running: bool = False) -> str:
    title = ('<span class="spin"></span>Agents at work' if running
             else "What the agents did after your last answer")
    return f"""
      <section class="card agents" id="agents" aria-labelledby="agents-title"{" hidden" if hidden else ""}>
        <div class="agents-head"><h2 id="agents-title">{title}</h2>{ui.agent_key()}</div>
        <ol class="trail{" playing" if running else ""}" id="trail" aria-live="polite">{ui.trail_rows(steps)}</ol>
      </section>"""


def _attempt_rows(store: Store, run_id: str, qid: str) -> str:
    attempts = [a for a in store.history(run_id, "attempt") if a.payload["question_id"] == qid]
    if not attempts:
        return ""
    cls = {c.payload.get("attempt_number"): c.payload for c in store.history(run_id, "classification")
           if c.payload.get("question_id") == qid}
    matches = {m.payload.get("attempt_number"): m.payload for m in store.history(run_id, "operator_match")
               if m.payload.get("question_id") == qid}
    rows = []
    for a in attempts:
        p = a.payload
        n = p["attempt_number"]
        if p["correct"]:
            rows.append(f'<li class="attempt ok"><span class="a-num">Attempt {n}</span>'
                        f'<span>You answered <code>{esc(p["student_answer"])}</code></span>'
                        f'<span class="a-why">✓ correct</span></li>')
            continue
        why = ""
        if n in cls:
            why = "✗ " + BUG_LABELS.get(cls[n]["error_type"], cls[n]["error_type"])
        elif len(matches.get(n, {}).get("matched_operators", [])) >= 2:
            why = "✗ fits more than one kind of mistake"
        else:
            why = "✗ not a match"
        rows.append(f'<li class="attempt"><span class="a-num">Attempt {n}</span>'
                    f'<span>You answered <code>{esc(p["student_answer"]) or "(nothing)"}</code></span>'
                    f'<span class="a-why">{esc(why)}</span></li>')
    return (f'<p class="hist-title">Your answers so far on this question</p>'
            f'<ol class="attempts">{"".join(rows)}</ol>')


def _tutor_block(store: Store, run_id: str, qid: str) -> str:
    """The tutor's latest explanation for this question, only if it came after
    the latest attempt (an explanation for an older attempt would be stale)."""
    attempts = [a for a in store.history(run_id, "attempt") if a.payload["question_id"] == qid]
    reex = [r for r in store.history(run_id, "reexplanation") if r.payload.get("question_id") == qid]
    if not attempts or not reex or reex[-1].seq < attempts[-1].seq:
        return ""
    plans = [p for p in store.history(run_id, "strategy_plan") if p.payload.get("question_id") == qid]
    style = STRATEGY_LABELS.get(reex[-1].payload["strategy"], reex[-1].payload["strategy"])
    why = f'<p class="why">Why this approach: {esc(plans[-1].payload["reason"])}</p>' if plans else ""
    return f"""<div class="tutor"><p class="who-t">Tutor · {esc(style)}</p>
      <p>{esc(reex[-1].payload["explanation"])}</p>{why}</div>"""


def _practice_page(store: Store, run_id: str, student: str | None, error: str = "") -> HTMLResponse | None:
    open_qs = callback.pending(store, run_id)
    if not open_qs:
        return None
    ask = open_qs[-1].question
    prob = store.latest(run_id, "problem") or {}
    qid = prob.get("id")
    spec = _QBY.get(qid)
    if spec is None:
        return None
    phase = prob.get("phase", "practice")
    q_num = _q_num(qid)
    records = store.replay(run_id)

    outcomes = progress.question_outcomes(store, run_id)
    tiles = [progress.tile_status(outcomes[q], q == qid) for q in _Q_ORDER]
    retried = outcomes[qid]["attempts"] > 0 and not outcomes[qid]["solved"]

    steps = trail.steps_from_records(records)
    turn = trail.turn_records(records)
    banner = ""
    just = next((v.payload for v in turn if v.kind == "attempt" and v.payload["correct"]), None)
    if just and just["question_id"] != qid:
        tries = just["attempt_number"]
        banner = (f'<p class="banner ok"><b>✓ Correct!</b> Question {_q_num(just["question_id"])} solved'
                  f'{" first time" if tries == 1 else f" on attempt {tries}"}.</p>')
    if phase == "practice" and any(v.kind == "scaffold_attempt" and v.payload["correct"] for v in turn):
        banner += '<p class="banner ok"><b>✓ Warm-up done.</b> Now back to the real question.</p>'
    left = next((v for v in turn if v.kind in ("skipped", "reviewed")), None)
    if left:
        n = _q_num(left.payload["question_id"])
        banner += (f'<p class="banner info"><b>Skipped</b> question {n}.</p>' if left.kind == "skipped"
                   else f'<p class="banner info"><b>The answer to question {n}:</b> '
                        f'{esc(left.payload["solution"])}</p>')
    if error:
        banner += f'<p class="banner err" role="alert">{esc(error)}</p>'

    greeting = ""
    g = store.history(run_id, "greeting")
    if g and q_num == 1 and outcomes[qid]["attempts"] == 0:
        label = BUG_LABELS.get(g[-1].payload["error_type"], g[-1].payload["error_type"])
        greeting = (f'<p class="banner info"><b>Welcome back.</b> Last time you had trouble with '
                    f'{esc(label)}. Let\'s start there.</p>')

    history = _attempt_rows(store, run_id, qid)
    tutor = _tutor_block(store, run_id, qid)
    n_try = outcomes[qid]["attempts"] + 1
    pill = (f'<span class="pill orange">Attempt {n_try}</span>' if n_try > 1
            else '<span class="pill blue">First attempt</span>')
    if prob.get("scaffold_id"):
        pill = '<span class="pill blue">Warm-up</span>'
    action = f"/session/{quote(run_id, safe='')}/answer"
    equation = prob.get("text") or spec["text"]      # a warm-up shows ITS equation, not the real one

    # "Skip" and "Show me" are on every page, whatever the stage: a stuck student
    # is never trapped. Each posts a fixed word the server checks first.
    escape = """<div class="escape">
              <button class="link-btn" type="submit" name="answer" value="skip" formnovalidate>Skip question</button>
              <button class="link-btn" type="submit" name="answer" value="show me" formnovalidate>Show me the answer</button>
            </div>"""

    def choice_form(options: list[tuple[str, str, str]], lead: str = "") -> str:
        cols = "choices three" if len(options) == 3 else "choices"
        return f"""
          {lead}
          <p class="ask">{esc(ask)}</p>
          <form id="answer-form" method="post" action="{action}">
            <div class="{cols}">{"".join(
                f'<button class="choice" type="submit" name="answer" value="{esc(v)}"><b>{esc(t)}</b><span>{esc(d)}</span></button>'
                for v, t, d in options)}</div>
            <p class="field-error" id="field-error" role="alert"></p>
            {escape}
          </form>"""

    if phase == "confirm":
        interaction = choice_form([("yes", "Yes, that's it", "That is what I did"),
                                   ("no", "No, something else", "That wasn't it")], history)
    elif phase == "method_check":
        how = {"formula": "Quadratic formula", "factorization": "Factoring"}
        options = [(op, BUG_LABELS.get(op, op).capitalize(), how.get(OPERATOR_METHOD.get(op), ""))
                   for op in prob.get("candidates", [])]
        options.append(("other", "Something else", "None of these"))
        interaction = choice_form(options, history)
    elif phase == "choice":
        interaction = choice_form([
            ("worked example", "Show me a worked example", "Walk through this exact equation, step by step"),
            ("simpler problem", "Try a simpler problem first", "Rebuild the skill on an easier equation"),
            ("show me", "Just show me the answer", "See the full solution and move on")], history + tutor)
    elif phase == "intermediate_step":
        interaction = f"""
          <p class="hint">Type it however you like - for example <code>Δ = 16 − 32 = −16</code>,
            or <code>−2 and −3</code>.</p>
          {history}
          <p class="ask">{esc(ask)}</p>
          <form id="answer-form" method="post" action="{action}" novalidate>
            <div class="answer">
              <input type="text" name="answer" placeholder="Your working" autocomplete="off" autofocus
                     maxlength="{MAX_ANSWER_CHARS}" aria-label="Your working" aria-describedby="field-error">
              <button class="btn" type="submit">Send</button>
            </div>
            <p class="field-error" id="field-error" role="alert"></p>
            <div class="escape">
              <button class="link-btn" type="submit" name="answer" value="skip this step" formnovalidate>Skip this step</button>
              <button class="link-btn" type="submit" name="answer" value="skip" formnovalidate>Skip question</button>
              <button class="link-btn" type="submit" name="answer" value="show me" formnovalidate>Show me the answer</button>
            </div>
          </form>"""
    else:                                               # practice, or a scaffold warm-up
        lead = ("A smaller one first, to build up to the real question. " if phase == "scaffold" else "")
        interaction = f"""
          <p class="hint">{lead}Find both values of x. If they are the same, type it in both boxes.
            Numbers only - fractions like 1/2 are fine.</p>
          {history}{tutor}
          <form id="answer-form" method="post" action="{action}" novalidate>
            <div class="roots">
              <label>x₁<input type="text" name="root1" placeholder="e.g. 2" autocomplete="off" autofocus
                     maxlength="{_ROOT_BOX_MAX}" aria-describedby="field-error"></label>
              <label>x₂<input type="text" name="root2" placeholder="e.g. 3" autocomplete="off"
                     maxlength="{_ROOT_BOX_MAX}" aria-describedby="field-error"></label>
            </div>
            <label class="none-box"><input type="checkbox" name="no_solution" value="1">
              <span>No real solution</span></label>
            <div class="answer"><button class="btn" type="submit">Check answer</button></div>
            <p class="field-error" id="field-error" role="alert"></p>
            {escape}
          </form>"""

    body = f"""
      {ui.stepper(tiles, q_num, retried)}
      <section class="card" aria-labelledby="qtitle">
        <div class="q-meta"><span class="q-num" id="qtitle">Question {q_num} of {len(_Q_ORDER)}</span>{pill}</div>
        {banner}{greeting}
        <div class="equation" aria-label="Equation">{esc(equation)} </div>
        {interaction}
        <div class="said" id="said" hidden><span>You answered</span><code></code></div>
      </section>
      {_agents_panel(steps, hidden=not steps)}"""
    return _page(f"Question {q_num}", body, student=student, active="practice", script=ui.SESSION_JS,
                 main_attrs=_main_attrs(run_id, phase, False))


def _busy_page(store: Store, run_id: str, student: str | None) -> HTMLResponse:
    """The same page while an answer is being processed: the question the
    student just answered stays put, and the agents' trail plays underneath."""
    records = store.replay(run_id)
    ans = next((v.payload for v in reversed(records) if v.kind == "expert_answer"), {})
    shown = (ans.get("question") or "").split("\n")[-1]
    said = ans.get("answer") or ""
    turn = trail.turn_records(records)
    turn_attempt = next((v.payload for v in turn if v.kind == "attempt"), None)
    qid = turn_attempt["question_id"] if turn_attempt else (store.latest(run_id, "problem") or {}).get("id")
    outcomes = progress.question_outcomes(store, run_id)
    tiles = [progress.tile_status(outcomes[q], q == qid) for q in _Q_ORDER]
    # The equation the answered question was about, from the "problem" record that
    # was on screen when the answer arrived (a follow-up question - confirm, show a
    # step - names no equation in its own text, but its record carries it).
    last_ans = max((i for i, v in enumerate(records) if v.kind == "expert_answer"), default=-1)
    on_screen = next((v.payload for v in reversed(records[:max(last_ans, 0)]) if v.kind == "problem"), {})
    equation = on_screen.get("text") or (_QBY.get(on_screen.get("id")) or {}).get("text")
    if shown.startswith("Solve for x: "):
        main = f'<div class="equation">{esc(shown[len("Solve for x: "):])}</div>'
    else:
        main = ((f'<div class="equation">{esc(equation)}</div>' if equation else "")
                + f'<p class="ask">{esc(shown or "Your answer")}</p>')
    body = f"""
      {ui.stepper(tiles, _q_num(qid))}
      <section class="card">
        <div class="q-meta"><span class="q-num">Question {_q_num(qid) or ""} of {len(_Q_ORDER)}</span>
          <span class="pill blue">Checking</span></div>
        {main}
        <div class="said"><span>You answered</span><code>{esc(said) or "(nothing)"}</code></div>
      </section>
      {_agents_panel([], hidden=False, running=True)}
      <noscript><meta http-equiv="refresh" content="3;url=/session/{quote(run_id, safe='')}"></noscript>"""
    return _page("Checking your answer", body, student=student, active="practice", script=ui.SESSION_JS,
                 main_attrs=_main_attrs(run_id, "busy", True))


def _failed_page(store: Store, run_id: str, student: str | None) -> HTMLResponse:
    failure = store.history(run_id, "failure")
    detail = failure[-1].payload["detail"] if failure else "unknown"
    return _page("Session stopped", f"""
      <section class="card">
        <p class="banner err">This session stopped before it could finish.</p>
        <h1>Your progress so far is saved</h1>
        <p class="muted">Start a fresh session to keep practising - everything you've already solved still counts
          on your progress page.</p>
        <details class="small muted"><summary>Technical details</summary><p><code>{esc(detail)}</code></p></details>
        <form method="post" action="/session/new" style="margin-top:1rem"><button class="btn" type="submit">Start a new session</button></form>
      </section>""", student=student, active="practice")


def _bug_card(et: str, b: dict, compact: bool = False) -> str:
    info = BUG_INFO.get(et, BUG_INFO["unclassified"])
    status = b["status"]
    badge = {"green": ("Under control", "green"), "orange": ("Getting there", "orange"),
             "red": ("Needs work", "red")}[status]
    pairs, corrected = b["pairs"], b["corrected"]
    fixed_pct = round(100 * corrected / pairs) if pairs else 0
    tip = "" if compact else f'<p class="tip"><b>Try this:</b> {esc(info["tip"])}</p>'
    return f"""
      <article class="card bug {status}">
        <div class="row between"><h3>{esc(BUG_LABELS.get(et, et).capitalize())}</h3>
          <span class="pill {badge[1]}">{badge[0]}</span></div>
        <p class="what">{esc(info["what"])}</p>
        <div class="meter" role="img" aria-label="{corrected} of {pairs} corrected"><i class="ok" style="width:{fixed_pct}%"></i><i class="open" style="width:{100 - fixed_pct}%"></i></div>
        <div class="facts"><span>Seen <b>{b["hits"]}×</b></span>
          <span>on <b>{pairs}</b> question{"" if pairs == 1 else "s"}</span>
          <span>corrected <b>{corrected} of {pairs}</b></span>
          {f'<span>repeated up to <b>{b["peak"]}×</b> on one question</span>' if b["peak"] > 1 else ""}</div>
        {tip}
      </article>"""


def _complete_body(store: Store, run_id: str) -> str:
    rs = progress.run_summary(store, run_id)
    mastery = round(100 * rs["correct"] / rs["attempts"]) if rs["attempts"] else None
    first_pct = round(100 * rs["first_try"] / rs["tried"]) if rs["tried"] else None
    bugs = "".join(_bug_card(et, b, compact=True) for et, b in
                   sorted(rs["bugs"].items(), key=lambda kv: -kv[1]["hits"]))
    if not bugs:
        bugs = ('<div class="card empty"><b>A clean run</b>No mistakes to work through this session.</div>')
    tiles = [progress.tile_status(rs["outcomes"][q]) for q in _Q_ORDER]
    parts = [f'{rs[k]} {word}' for k, word in (("skipped", "skipped"), ("revealed", "shown the answer"))
             if rs[k]]
    left_out = f' ({", ".join(parts)}.)' if parts else ""
    seen = store.history(run_id, "reviewed")
    shown = ('<div class="card tight" style="text-align:left"><b>Answers you asked to see</b><ul>' + "".join(
        f'<li class="small">{esc(r.payload["solution"])}</li>' for r in seen) + '</ul></div>') if seen else ""
    return f"""
      {ui.stepper(tiles, None)}
      <section class="card" style="text-align:center">
        <p class="big-check">🎉</p>
        <h1>Session complete</h1>
        <p class="muted">You solved {rs["solved"]} of {len(_Q_ORDER)} questions in {rs["attempts"]} attempts.{left_out}</p>
        {shown}
        <div class="stats" style="text-align:left;margin-top:1rem">
          <div class="stat">{ui.ring(mastery)}<div class="l">mastery<br><span class="small">correct ÷ all attempts</span></div></div>
          <div class="stat"><div class="n {ui.tone(first_pct)}">{rs["first_try"]}</div><div class="l">solved on the first try</div></div>
          <div class="stat"><div class="n {"orange" if rs["recovered"] else "blue"}">{rs["recovered"]}</div><div class="l">mistakes you corrected and solved</div></div>
        </div>
        <div class="row" style="justify-content:center;margin-top:.4rem">
          <form method="post" action="/session/new"><button class="btn" type="submit">Practise again</button></form>
          <a class="btn ghost" href="/profile">See my progress</a>
        </div>
      </section>
      <h2 style="margin-top:1.4rem">What you worked through</h2>
      {bugs}"""


# ------------------------------------------------------------------ profile

@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request):
    student_id = _current_student(request)
    if student_id is None:
        return _to_login()
    with _open() as store:
        rep = progress.report(store, student_id)
    return _page("My progress", _profile_body(student_id, rep), student=student_id,
                 active="progress", wide=True)


def _profile_body(student_id: str, rep: dict) -> str:
    n = len(_Q_ORDER)
    first_pct = rep["first_try_rate"]
    bugs = rep["bugs"]
    # Unresolved first, then by how often; "unclassified" last so a real
    # misconception is never buried under the catch-all.
    order = sorted(bugs, key=lambda et: ({"red": 0, "orange": 1, "green": 2}[bugs[et]["status"]],
                                         et == "unclassified", -bugs[et]["hits"]))
    if bugs:
        bug_html = "".join(_bug_card(et, bugs[et]) for et in order)
    elif rep["attempts"]:
        bug_html = '<div class="card empty"><b>No mistakes on record</b>Every answer so far has been right. Nice.</div>'
    else:
        bug_html = '<div class="card empty"><b>Nothing here yet</b>Answer a few questions and your misconceptions will appear here.</div>'

    tiles = "".join(
        f'<li class="tile {t}" title="Question {i + 1}: {"solved first try" if t == "green" else "solved after retries" if t == "orange" else "skipped or shown" if t == "skipped" else "not yet"}">'
        f'<span aria-hidden="true">{"✓" if t == "green" else i + 1}</span></li>'
        for i, t in enumerate(rep["grid"]))
    def _sess_row(s: dict) -> str:
        status = {"complete": "finished", "failed": "stopped"}.get(s["state"], "in progress")
        link = "" if s["state"] == "failed" else f'<a class="small" href="/session/{s["run_id"]}">Open</a>'
        return (f'<div class="sess"><span><b>{esc(progress.when(s["started"]))}</b> '
                f'<span class="muted small">{status}</span></span>'
                f'<span class="small muted">{s["solved"]} solved · {s["first_try"]} first try · {s["attempts"]} attempts</span>'
                f'{link}</div>')

    sessions = "".join(_sess_row(s) for s in reversed(rep["sessions"][-6:])) or '<p class="muted small">No sessions yet.</p>'

    return f"""
      <div class="row between" style="margin:0 0 1rem">
        <div><h1>My progress</h1>
          <p class="muted" style="margin:0">{esc(student_id)} · {rep["session_count"]} session{"" if rep["session_count"] == 1 else "s"} · {rep["attempts"]} graded attempt{"" if rep["attempts"] == 1 else "s"}</p></div>
        <div class="row"><a class="btn" href="/resume">← Back to questions</a>
          <a class="btn ghost" href="/custom">Your own equation</a></div>
      </div>

      <div class="stats">
        <div class="stat">{ui.ring(rep["mastery"])}<div class="l"><b>Mastery</b><br>correct ÷ all attempts</div></div>
        <div class="stat"><div class="n {ui.tone(first_pct)}">{"–" if first_pct is None else f"{first_pct}%"}</div><div class="l"><b>First-try rate</b><br>questions right straight away</div></div>
        <div class="stat"><div class="n {"green" if rep["recovered"] else "blue"}">{rep["recovered"]}</div><div class="l"><b>Mistakes corrected</b><br>wrong first, then solved</div></div>
        <div class="stat"><div class="n blue">{rep["latest_solved"]}<span class="small muted">/{n}</span></div><div class="l"><b>Solved this session</b><br>latest run</div></div>
      </div>

      <section class="card">
        <h2>Latest session, question by question</h2>
        <ol class="grid10" style="list-style:none;padding:0;margin:0">{tiles}</ol>
        <p class="legend"><span><i class="green"></i>Right first time</span><span><i class="orange"></i>Needed retries</span><span><i class="todo"></i>Not yet</span></p>
      </section>

      <h2 style="margin:1.4rem 0 .6rem">Your misconceptions</h2>
      <p class="muted small" style="margin-top:-.3rem">
        <span class="pill green">Under control</span> fixed, one-off &nbsp;
        <span class="pill orange">Getting there</span> fixed, but it kept coming back &nbsp;
        <span class="pill red">Needs work</span> still unresolved</p>
      {bug_html}

      <section class="card"><h2>Recent sessions</h2>{sessions}</section>"""


# ------------------------------------------------------------------- custom
# The one path where a MODEL judges correctness - see demo/tracker/custom.py.
# One direct call to the same _CALL the graded flow uses; no runner.advance(),
# no Flow, no RunState. Logged on the student's own run as a "custom_check"
# record, a different kind from "attempt"/"classification", so nothing that
# counts a student's graded work can pick it up by accident.

@app.get("/custom", response_class=HTMLResponse)
def custom_page(request: Request):
    student_id = _current_student(request)
    if student_id is None:
        return _to_login()
    return _page("Your own equation", _custom_body(), student=student_id, active="custom")


@app.post("/custom/check", response_class=HTMLResponse)
def custom_check(request: Request, equation: str = Form(""), answer: str = Form("")):
    student_id = _current_student(request)
    if student_id is None:
        return _to_login()
    equation, answer = equation.strip(), answer.strip()
    if not equation or not answer:
        return _page("Your own equation", _custom_body(equation, answer, error="Fill in both the equation and your answer."),
                     student=student_id, active="custom")
    if len(equation) > MAX_ANSWER_CHARS or len(answer) > MAX_ANSWER_CHARS:
        return _page("Your own equation", _custom_body(equation[:MAX_ANSWER_CHARS], answer[:MAX_ANSWER_CHARS],
                                                       error=_ERRORS["long"]),
                     student=student_id, active="custom")
    with _open() as store:
        run_ids = students.past_run_ids(store, student_id)
        run_id = students.open_run_id(store, student_id) or (run_ids[-1] if run_ids else None)
        if run_id is None:      # logged in but never started a run - nowhere to log it
            return _to_login()
        try:
            result: CustomCheck = _CALL(
                settings=_SETTINGS, budget=Budget(store, run_id, _SETTINGS),
                messages=custom_check_messages(equation, answer),
                schema=CustomCheck, step="custom_check",
            )
        except (ModelError, BudgetExceeded) as e:
            return _page("Your own equation", _custom_body(equation, answer, error=str(e)),
                         student=student_id, active="custom")
        consistent = check_consistency(result, answer)
        payload = result.model_dump()
        payload.update(equation=equation, student_answer=answer, student_id=student_id,
                       verdict_matches_own_roots=consistent)
        store.append(run_id, "custom_check", payload, produced_by="agent:custom_check")
    return _page("Your own equation", _custom_body(equation, answer, result=result, consistent=consistent),
                 student=student_id, active="custom")


_EXAMPLES = [("x² − 5x + 6 = 0", "2 and 3"), ("x² − 9 = 0", "3 and -3"), ("2x² + 3x − 2 = 0", "1/2 and -2")]


def _custom_body(equation: str = "", answer: str = "", result: CustomCheck | None = None,
                 consistent: bool | None = None, error: str | None = None) -> str:
    result_html = ""
    if error:
        result_html = f'<p class="banner err" role="alert">Could not check that one: {esc(error)}</p>'
    elif result is not None:
        ok = result.student_correct
        roots = ", ".join(esc(r) for r in result.computed_roots) or "none found"
        warn = ""
        if consistent is False:
            warn = ('<p class="banner warn" style="margin-top:.7rem"><b>Treat with caution.</b> The checker&rsquo;s verdict '
                    'does not match the roots it wrote down itself.</p>')
        result_html = f"""
          <div class="verdict {"ok" if ok else "bad"}" role="status">
            <h3>{"✓ Correct" if ok else "✗ Not quite"}</h3>
            <p>{esc(result.feedback)}</p>
            <p class="small muted" style="margin:0">The checker&rsquo;s own working - roots it found for this equation: <code>{roots}</code></p>
            {warn}
          </div>"""
    chips = "".join(f'<button type="button" class="chip" data-eq="{esc(e)}" data-ans="{esc(a)}">{esc(e)}</button>'
                    for e, a in _EXAMPLES)
    return f"""
      <div class="row between" style="margin:0 0 1rem">
        <div><h1>Try your own equation</h1><p class="muted" style="margin:0">Type any quadratic and your answer to it.</p></div>
        <a class="btn ghost" href="/resume">← Back to questions</a>
      </div>
      <section class="card">
        <p class="banner info">This one is checked by an AI model, not by our own maths, so it is shown with its working
          and <b>does not count</b> towards your progress.</p>
        {result_html}
        <form id="custom-form" method="post" action="/custom/check" class="stack">
          <label class="small muted" for="equation">Equation</label>
          <input id="equation" type="text" name="equation" maxlength="{MAX_ANSWER_CHARS}" placeholder="e.g. x² − 5x + 6 = 0" value="{esc(equation)}" required>
          <label class="small muted" for="answer">Your answer</label>
          <input id="answer" type="text" name="answer" maxlength="{MAX_ANSWER_CHARS}" placeholder="e.g. 2 and 3" value="{esc(answer)}" required autocomplete="off">
          <div class="small muted">Or try an example:</div><div class="chips">{chips}</div>
          <button class="btn" id="custom-go" type="submit">Check with the AI model</button>
        </form>
      </section>
      <script>
        document.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {{
          document.getElementById('equation').value = c.dataset.eq;
          document.getElementById('answer').value = c.dataset.ans;
        }}));
        document.getElementById('custom-form').addEventListener('submit', () => {{
          const b = document.getElementById('custom-go'); b.disabled = true;
          b.innerHTML = '<span class="spin"></span>Checking - this can take a few seconds';
        }});
      </script>"""
