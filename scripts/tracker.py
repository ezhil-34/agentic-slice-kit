#!/usr/bin/env python3
"""The Misconception Tracker. Spec: README.md (repo root).

    python scripts/tracker.py run --stub                 # no key, no network, no tokens
    python scripts/tracker.py run                          # live, real classify + reexplain
    python scripts/tracker.py run --stub --anonymous       # skip login, quick local testing
    python scripts/tracker.py replay <run_id>

Each round: the run suspends on AWAITING_EXPERT with a question on screen,
you type an answer, and it resumes. That single mechanic - slice/callback.py's
ask()/answer() - is doing four different jobs across a session: presenting a
fresh question, re-presenting the same question after a re-explanation,
asking "which method did you use?" when two bugs predict the same wrong
answer, and asking the "worked example or simpler problem?" pause question.
Same code path every time; only the resume_state (and the "problem" record's
phase tag) differs.

Logging in with a username + 4-digit PIN (MVP Build Context section 2.4) is
what lets a returning student's second session start smarter - it folds in
their misconception history from every past run before question 1. Same
--db file works for both this CLI and web/student.py; a student can start on
one and finish on the other.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slice import callback, runner
from slice.config import settings as load_settings
from slice.records import RunState
from slice.store import Store

from demo.tracker import students
from demo.tracker.flow import build_flow, start_run

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
GREEN, RED, AMBER = "\033[32m", "\033[31m", "\033[33m"


def _c(s: str, colour: str) -> str:
    return s if not sys.stdout.isatty() else f"{colour}{s}{RESET}"


def _login(store: Store) -> str | None:
    """Username + PIN, first login creates the account. Wrong PIN gets one
    retry, then gives up (this is a demo gate, not a security boundary -
    see demo/tracker/students.py)."""
    username = input(f"  {_c('username:', DIM)} ").strip()
    if not username:
        return None
    for _ in range(2):
        pin = input(f"  {_c('pin:', DIM)} ").strip()
        try:
            return students.register_or_login(store, username, pin)
        except students.LoginError as e:
            print(_c(f"  {e}", RED))
    return None


def cmd_run(args: argparse.Namespace) -> int:
    if args.stub:
        from demo.tracker.stub import FakeCall
        call = FakeCall()
        st = load_settings()
    else:
        from slice.llm import complete as call
        st = load_settings()
        if not st.api_key:
            print(_c("No OPENROUTER_API_KEY in .env.", RED),
                  "\nRun with --stub to prove the wiring without a key.")
            return 2

    store = Store(args.db)
    flow = build_flow(call)

    student_id = None
    if not args.anonymous:
        student_id = _login(store)
        if student_id is None:
            print(_c("  no username given - continuing anonymously", DIM))

    resumed = students.open_run_id(store, student_id) if student_id else None
    run_id = resumed or start_run(store, st, student_id=student_id)
    print(f"\n{_c('run', DIM)} {BOLD}{run_id}{RESET}   "
          f"{_c('stub' if args.stub else st.model, DIM)}"
          f"{_c(f'  student={student_id}', DIM) if student_id else ''}"
          f"{_c('  (resumed)', GREEN) if resumed else ''}\n")

    seen = 0
    while True:
        state = runner.advance(store, run_id, flow, st)
        seen = _print_new(store, run_id, seen)

        if state is RunState.COMPLETE:
            print(f"\n  {_c('=>', DIM)} {_c('COMPLETE', GREEN)}   "
                  f"session finished, {int(store.counter(run_id, 'tokens')):,} tok\n")
            return 0
        if state is RunState.FAILED:
            print(f"\n  {_c('=>', DIM)} {_c('FAILED', RED)}\n")
            return 1

        # AWAITING_EXPERT: something is on screen waiting for a typed answer
        open_qs = callback.pending(store, run_id)
        if not open_qs:
            print(_c("no open question but run is suspended - this is a bug", RED))
            return 1
        q = open_qs[-1]
        try:
            answer = input(f"  {_c('>', DIM)} {q.question}\n  {_c('you:', DIM)} ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        callback.answer(store, q.id, answer, who="student")


def _print_new(store: Store, run_id: str, seen: int) -> int:
    records = store.replay(run_id)
    for v in records[seen:]:
        if v.kind == "greeting":
            print(f"  {_c('greeting  ', DIM)} recurring {v.payload['error_type']}"
                  f" ({v.payload['occurrences']}x last time)")
        elif v.kind == "attempt":
            tag = _c("correct", GREEN) if v.payload["correct"] else _c("wrong", RED)
            print(f"  {_c('evaluate  ', DIM)} {tag}   {v.payload['student_answer']!r}")
        elif v.kind == "operator_match":
            matched = v.payload["matched_operators"]
            if len(matched) >= 2:
                print(f"  {_c('match     ', DIM)} {_c('COLLISION', AMBER)} - "
                      f"{', '.join(matched)} all fit; asking which method")
            elif matched:
                print(f"  {_c('match     ', DIM)} exact operator match: {matched[0]}")
            else:
                print(f"  {_c('match     ', DIM)} no fixed operator matched - deferring to classify")
        elif v.kind == "method_choice":
            note = " (defaulted, no answer)" if v.payload["defaulted"] else ""
            print(f"  {_c('resolve   ', DIM)} chose {v.payload['chosen_method']!r}"
                  f" -> {v.payload['resolved_operator']}{note}")
        elif v.kind == "classification":
            print(f"  {_c('classify  ', DIM)} {_c(v.payload['error_type'], AMBER)}"
                  f"   ({v.payload['confidence']:.2f})   {v.payload['reasoning']}")
        elif v.kind == "misconception":
            print(f"  {_c('log       ', DIM)} {v.payload['error_type']}"
                  f" x{v.payload['occurrences']} on {v.payload['question_id']}")
        elif v.kind == "reexplanation":
            print(f"  {_c('reexplain ', DIM)} strategy={_c(v.payload['strategy'], AMBER)}")
            print(f"             {v.payload['explanation']}")
        elif v.kind == "human_choice":
            note = " (defaulted, no answer)" if v.payload["defaulted"] else ""
            print(f"  {_c('choice    ', DIM)} {v.payload['choice']}{note}")
        elif v.kind == "failure":
            print(f"  {_c('stop      ', DIM)} {_c(v.payload['kind'], RED)} - {v.payload['detail']}")
    return len(records)


def cmd_replay(args: argparse.Namespace) -> int:
    store = Store(args.db)
    for v in store.replay(args.run_id):
        body = json.dumps(v.payload, indent=2)
        print(f"\n{_c(f'#{v.seq}', DIM)} {BOLD}{v.kind}{RESET} {_c(v.produced_by, DIM)}")
        print("\n".join("    " + l for l in body.splitlines()))
    print()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="tracker.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start a session, one question at a time")
    r.add_argument("--stub", action="store_true", help="canned replies; no key needed")
    r.add_argument("--anonymous", action="store_true",
                    help="skip the username/PIN login (no cross-session memory)")
    r.set_defaults(fn=cmd_run)

    rp = sub.add_parser("replay", help="every record of a run, in order")
    rp.add_argument("run_id")
    rp.set_defaults(fn=cmd_replay)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
