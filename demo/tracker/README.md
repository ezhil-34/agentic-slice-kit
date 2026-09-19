# demo/tracker — the Misconception Tracker, one agent at a time

This is the buildable code behind the repo-root `README.md` (our AgentSpec).
That document is the *why*; this one is the *what's in each file* and, most
usefully, **what each of the four agents does, on its own** - since that is
what a teammate picking up one file needs, and what a judge asking "which
part is actually agentic" is really asking about.

Nothing in `slice/` was changed to build this. All four states this domain
uses - `DRAFTING`, `GATING`, `PROBING`, `AWAITING_EXPERT` - already existed in
`slice/records.py`. A domain that needs a spine edit is testing the edit, not
the domain - see `demo/smoke/SMOKE-SPEC.md` for where that idea comes from.

---

## The four agents

Each one is a single handler function in `flow.py`. Read them in this order -
it's the order a run actually visits them.

### 1. Evaluate — `handle_drafting` (state: `DRAFTING`)

**What it does:** reads the student's just-typed answer, parses out the
number(s) in it, and checks them against the question's correct roots.

**Is it a model call?** No. Plain code (`parse_roots`, `roots_match` at the
top of `flow.py`). This is deliberate: correctness of a numeric answer is
never something we want to be "the model's opinion" - see repo README.md
section 8.

**Reads:** the current `problem` record (the active question) and the
student's latest typed answer.
**Writes:** one `attempt` record, always - correct or not.

### 2. Classify — also inside `handle_drafting`, only on a wrong answer

**What it does:** asks a model which of three known error types
(`sign_error`, `factoring_error`, `arithmetic_slip`) the wrong answer looks
like, and why.

**Is it a model call?** Yes - the first of exactly two in this build.
Deliberately kept separate in spirit from evaluate, even though they share a
handler: evaluate can never be wrong about *whether* an answer is right;
classify can be wrong about *why*, and that judgement deserves its own record
so a wrong classification is visible on replay rather than silently folded
into the attempt.

**Reads:** the question, its `known_error_patterns` hint list, and the wrong
answer. **Writes:** one `classification` record, always including a
`reasoning` string - never a bare label - so nothing here guesses silently
(repo README.md, refusal #3, general project prompt).

### 3. Log & decide — `handle_gating` (state: `GATING`)

**This is the back-edge. If you only read one handler, read this one.**

**What it does:** three jobs, no model call in any of them:
1. If the student was resuming from the three-strikes pause, records their
   choice (or the fact that nobody answered) and hands off to reexplain.
2. If the last attempt was correct, advances to the next question - or to
   `COMPLETE` if the bank is exhausted.
3. If the last attempt was wrong, counts how many times *this exact error
   type* has now occurred on *this exact question*, logs a `misconception`
   record, and decides: send the work backwards to reexplain (occurrence 1
   or 2), or stop trying to fix it alone and ask the student directly
   (occurrence 3 - `REPEATS_BEFORE_PAUSE` in `schema.py`).

**Why this one file is the whole claim:** every other handler in this
project could be deleted and replaced with a fixed script. This one cannot -
whether it sends work backwards, moves forward, or pauses depends entirely on
what the student just typed, not on a predetermined sequence. That's the
literal test from the hackathon prep material: *"can you draw the exact
sequence of steps before you run it? If you can't, it's an agent."* You
can't, here.

**Reads:** the attempt/classification history for the current question.
**Writes:** `misconception`, `human_choice`, or nothing (just moves state).

### 4. Reexplain — `handle_probing` (state: `PROBING`)

**What it does:** asks a model to re-teach the concept, picking one of three
strategies (`worked_example`, `alternate_method`, `simpler_problem`) -
whichever the student explicitly asked for, or whichever hasn't been tried
yet for this error type on this question. Then re-presents the same
question.

**Is it a model call?** Yes - the second of exactly two.

**Reads:** the error type, and every `reexplanation` already tried for this
question (so it never repeats a strategy - enforced in code via the
`already_tried` set, not left to the prompt to remember).
**Writes:** one `reexplanation` record, then a fresh `problem` record for the
retry.

---

## The fifth thing: Presenting isn't a handler

You will not find a `handle_presenting` function, on purpose. Showing a
question to the student and suspending is `slice/callback.py`'s `ask()`,
called from inside `handle_gating` (moving to the next question, or pausing
on the three-strikes question) and `handle_probing` (re-presenting the same
question after a re-explanation). All three moments - a fresh question, a
retry, and the pause - are the same underlying mechanic with a different
`resume_state`, which is exactly the point of building human-in-the-loop as
a *state* rather than a special case per call site.

## One naming trap worth knowing about

Our domain's active-question record is called `"problem"`, not
`"question"`. `slice/callback.py` already writes its *own* bookkeeping
record under the kind name `"question"` every time it asks something - using
that name for our own record silently shadows it, since `ctx.latest(kind)`
just returns whichever record of that kind was written most recently. We hit
this once, live, while building: `ctx.latest("question")` was quietly
returning callback's bookkeeping record instead of our math problem, and
`q["roots"]` failed with a `KeyError` because callback's record has no such
field. Renamed ours to `"problem"` and it's been correct since. Worth
remembering if you extend this: any new record kind should be checked
against what `slice/` itself already writes (`question`, `expert_answer`,
`failure`) before you pick a name.

---

## Files

| file | owns |
|---|---|
| `schema.py` | `ErrorClassification`, `ReExplanation` (the two model-validated outputs), the fixed 10-question bank, and the three constants (`ERROR_TYPES`, `STRATEGIES`, `REPEATS_BEFORE_PAUSE`) that are our domain's opinions, not the kit's |
| `flow.py` | `parse_roots`/`roots_match` (evaluate's logic), the three handlers, `build_flow()`, `start_run()` |
| `prompts/classify.md` | the classify agent's instructions |
| `prompts/reexplain.md` | the reexplain agent's instructions |
| `stub.py` | `FakeCall` - canned classify/reexplain replies that react to the actual prompt content, so the whole loop can be proven with no key (see repo README.md build order, Phase 1) |

Driver: `scripts/tracker.py`, at the repo root's `scripts/` folder alongside
`smoke.py` - `python scripts/tracker.py --db tracker.db run --stub`.

## Running it

```bash
# Prove the state machine first - no key needed
python scripts/tracker.py --db tracker.db run --stub

# Once that's solid, run it for real
python scripts/tracker.py --db tracker.db run

# Inspect any past run, record by record
python scripts/tracker.py --db tracker.db replay <run_id>
```

Try typing the same wrong answer to question 1 (`x² − 5x + 6 = 0`) three
times in a row under `--stub` - you'll see the strategy change on the second
wrong attempt, and the pause question appear on the third. That's the entire
claim, provable in under a minute with no API key.
