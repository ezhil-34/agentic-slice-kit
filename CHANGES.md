# What's in this delivery, and how to apply it

Everything here implements Section 2 of the MVP Build Context, plus a full
interactive student UI, plus tests. It's built and tested against your real
`agentic-slice-kit` repo (the v2 zip), not written blind.

## Apply it

1. **Delete** `tests/test_smoke.py` — it imports `demo.smoke`, which no
   longer exists in your repo, and it was already breaking `pytest`
   collection entirely before any of this work started. This is a
   pre-existing issue, unrelated to today's build.
2. **Copy every file in this zip** over the matching path in your repo
   (`demo/tracker/schema.py`, `demo/tracker/flow.py`, etc. — same layout).
   Two are brand new: `demo/tracker/students.py` and `web/student.py`.
3. `pip install -r requirements.txt` again — one new dependency,
   `python-multipart` (FastAPI needs it for the login/answer forms; without
   it `web/student.py` fails at import with a clear error naming it).
4. `python -m pytest` — should show **77 passed, 3 skipped** (the 3 skips
   are the real-API integration tests, which need a live key; that's
   expected and unrelated to this work).

## Try it

```bash
# CLI, as before, now with login:
python scripts/tracker.py run --stub
#  -> prompts for a username + PIN first; second run under the same name
#     greets you about your worst recurring mistake from last time.

# Or skip login for quick manual testing:
python scripts/tracker.py run --stub --anonymous

# The new student-facing web page:
uvicorn web.student:app --reload --port 8002
#  -> open http://localhost:8002, log in, answer questions in the browser.
#     Type the negative of a question's correct roots (e.g. "-2 and -3" on
#     the first question) to see the new method-check question fire.

# The existing observability dashboard, updated for the new bug labels:
uvicorn web.dashboard:app --reload --port 8001
```

All three point at the same `tracker.db` (or `--db`/`TRACKER_DB` override),
so a session started on the CLI can be watched live on the dashboard, or
finished in the browser.

---

# What was built, and why

## 1. Operators: deterministic diagnosis instead of a model guessing

`demo/tracker/schema.py` now defines four bug types as **computable
predictions**, not labels a model invents from scratch:

| Operator | Method | What it predicts |
|---|---|---|
| `formula_sign_flip` | formula | negate both correct roots |
| `formula_forgot_2a` | formula | `(−b ± √D) / a` instead of `/2a` |
| `factor_sign_flip` | factorization | negate both correct roots — **same prediction as formula_sign_flip, on purpose** |
| `factor_wrong_pair` | factorization | a catch-all: doesn't satisfy the equation at all, and doesn't match either sign-flip prediction |

`match_operators(a, b, c, correct_roots, student_roots)` checks the
student's actual typed answer against each prediction, computed fresh from
that question's own coefficients — never hand-curated per question, never
the model's opinion. `collisions()` proves the sign-flip collision is real
by computing it against the live question bank rather than asserting it;
I ran this and confirmed it holds on all 10 questions.

**This changes what `classify` (the model call) is for.** It used to be
asked to invent a label from three generic categories. Now:
- **0 operators match** → classify picks freely among the four (or
  `unclassified`) — the safety net for a genuinely novel mistake.
- **Exactly 1 matches** → classify is told the deterministic answer and
  asked only to *explain it in words* — the label itself is no longer a
  guess.
- **2+ match (a collision)** → **classify is never called at all.** This is
  the important case: no amount of asking the model to look harder resolves
  a genuine ambiguity, so `handle_gating` intercepts it before classify runs
  and asks the student directly which method they used instead.

## 2. The method-check branch (`flow.py`, `handle_gating`)

When a collision is detected, the run pauses with: *"Your answer matches
more than one kind of mistake... did you use factorization or the quadratic
formula?"* — the same `slice/callback.py` ask/suspend/resume mechanism as
every other pause in this project, just a new `phase` tag (`method_check`)
on the `"problem"` record. Once answered, `handle_gating` resolves it to
the one operator matching that method, writes a `classification` record
with `confidence: 1.0` (it's not a guess — it's resolved), and falls into
the **same** occurrence-counting / pause-at-3 logic the ordinary wrong-answer
path uses (via a shared `_decide_after_classification` helper) — so nothing
about the three-strikes behavior changed, it just gained a second way to
arrive at a known bug label.

I verified this by hand and in tests: a sign-flip wrong answer → asks which
method → "factorization" resolves to `factor_sign_flip`, "quadratic formula"
resolves to `formula_sign_flip` → misconception logged → reexplain fires.

**One scope cut, made deliberately:** the MVP doc offered two disambiguation
options — asking directly, or presenting a method-exclusive next question.
I built the first (cheaper, and explicitly named as such in the doc) and did
not build the second, since the current 10-question bank is rational-root
throughout and doesn't have method-exclusive questions to present. Worth a
conscious decision if you want the second version later, not an oversight.

## 3. Student login and cross-session memory (`demo/tracker/students.py`)

A new `students` table and `student_runs` table, added on the **same**
sqlite file via `store.db` (the raw connection `Store` already exposes) —
`slice/store.py` itself was never touched. Username + 4-digit PIN, stored
as-is, exactly as scoped ("a login gate, not a bank").

The payoff is `start_run`'s new `student_id` parameter: it folds in that
student's worst recurring bug from every past run and prepends it to
question 1 — *"Last time you had trouble with sign errors in the quadratic
formula (1x) — let's start there."* I ran this across two sessions and
confirmed the greeting fires correctly and names the right bug.

`students.open_run_id()` also means logging in again resumes an unfinished
session instead of starting over — both the CLI and the web UI use it.

## 4. The student web UI (`web/student.py`) — new

A complete second web app alongside the existing observability dashboard,
built the same way (self-contained FastAPI, no build step, no JS required —
plain forms, so there's nothing that can silently fail during a live demo).
Login → current question → submit → feedback panel (shows the reexplanation
text prominently when a retry follows a wrong answer — this is the actual
"teach them" moment) → completion summary. I drove this through
`TestClient` end-to-end, including triggering and resolving the
method-check collision entirely through the HTTP interface, and confirmed
it reaches "Session complete" after all 10 questions.

Multiple students can use it at once, each under their own login — every
request opens a fresh `Store` connection (same pattern `web/dashboard.py`
already used), and state lives in the shared sqlite file, not in the
server process.

## 5. Tests (`tests/test_tracker.py`) — new, 16 tests

Covers what didn't exist a test for before: the operator predictions
themselves, the collision detection, both resolution paths (factorization
vs. formula), the "nobody answered" default, the ordinary 3-strikes path
still working unchanged, history immutability, record attribution, and the
full login/greeting/resume student lifecycle. Runs against `FakeCall` only
— no key needed. All 16 pass, alongside the 61 pre-existing kit tests
(with the broken `test_smoke.py` removed).

## What I did not touch

`slice/` — zero edits, as scoped. The four states this domain uses
(`DRAFTING`, `GATING`, `PROBING`, `AWAITING_EXPERT`) were already enough —
extending by adding a phase tag and a new record kind was sufficient, with
nothing added to the spine itself. `corpus/`, `slice/retrieve.py`, and
`web/expert.py` are likewise untouched — none of them apply to this domain.

## Still open, if there's time

- The MVP doc's "present a method-exclusive next question" as an
  alternative/addition to asking directly (see the scope-cut note above).
- Radical-root questions (Q11–16 from the domain reference) — still
  deliberately deferred, same as before this session's work.
- Real-model (non-stub) testing of `classify`/`reexplain` against the new
  prompts — everything here is verified against `FakeCall`; the prompts
  were rewritten for the new vocabulary but a live run is worth doing
  before the demo to see actual model behavior on the new bug labels.
