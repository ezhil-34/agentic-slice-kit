"""
Per-student persistence: login, and folding a returning student's
misconception history across every past run into the start of a new one.

MVP Build Context section 2.4, deliberately simple: a `students` table
(username + a 4-8 digit PIN, stored salted and hashed - still a login gate,
not a bank) and a `student_runs` table linking a student to every run_id
they've ever started. Both live on the SAME sqlite file the kit's own Store
already uses - reached through `store.db`, the raw sqlite3 connection Store
exposes - so slice/store.py itself is never edited. This is the same
principle as the rest of the project: extend by adding, not by touching the
spine.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from slice.store import Store

_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    student_id  TEXT PRIMARY KEY,
    pin         TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_secret (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    secret TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS student_runs (
    student_id  TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (student_id, run_id)
);
"""


def ensure_tables(store: Store) -> None:
    store.db.executescript(_SCHEMA)


class LoginError(ValueError):
    """Wrong PIN for an existing username."""


MAX_USERNAME = 32


def _hash_pin(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 100_000).hex()
    return f"pbkdf2${salt}${digest}"


def _pin_matches(stored: str, pin: str) -> bool:
    if stored.startswith("pbkdf2$"):
        _, salt, _ = stored.split("$")
        return hmac.compare_digest(stored, _hash_pin(pin, salt))
    return hmac.compare_digest(stored, pin)          # legacy row, stored as typed


def register_or_login(store: Store, username: str, pin: str) -> str:
    """First login with a username creates the record. A later login with
    the same username checks the PIN matches. Returns the student_id
    (lower-cased username) to key everything else off - the run, the
    misconception ledger, all of it already key off a plain string id.

    PINs are stored salted and hashed. An account created before that (PIN
    stored as typed) still logs in, and is upgraded to a hash on the spot."""
    ensure_tables(store)
    student_id = username.strip().lower()
    if not student_id:
        raise LoginError("Username can't be empty.")
    if len(student_id) > MAX_USERNAME or any(ord(ch) < 32 for ch in student_id):
        raise LoginError(f"Usernames can be up to {MAX_USERNAME} characters.")
    row = store.db.execute(
        "SELECT pin FROM students WHERE student_id=?", (student_id,)
    ).fetchone()
    if row is None:
        if not (pin.isascii() and pin.isdigit() and 4 <= len(pin) <= 8):
            raise LoginError("Pick a PIN of 4 to 8 digits.")
        store.db.execute(
            "INSERT INTO students(student_id, pin, created_at) VALUES (?,?,?)",
            (student_id, _hash_pin(pin), time.time()),
        )
        return student_id
    if not _pin_matches(row["pin"], pin):
        raise LoginError("Wrong PIN for that username.")
    if not row["pin"].startswith("pbkdf2$"):
        store.db.execute("UPDATE students SET pin=? WHERE student_id=?",
                         (_hash_pin(pin), student_id))
    return student_id


def app_secret(store: Store) -> str:
    """A random signing secret, created once and kept in the database so a
    login cookie survives a server restart."""
    ensure_tables(store)
    store.db.execute("INSERT OR IGNORE INTO app_secret(id, secret) VALUES (1, ?)",
                     (secrets.token_hex(32),))
    return store.db.execute("SELECT secret FROM app_secret WHERE id=1").fetchone()["secret"]


def run_owner(store: Store, run_id: str) -> str | None:
    """Which student a run belongs to; None for an anonymous run (the CLI's
    --anonymous), which anyone holding its id may open."""
    ensure_tables(store)
    row = store.db.execute(
        "SELECT student_id FROM student_runs WHERE run_id=?", (run_id,)).fetchone()
    return row["student_id"] if row else None


def run_times(store: Store, student_id: str) -> dict[str, float]:
    ensure_tables(store)
    return {r["run_id"]: r["created_at"] for r in store.db.execute(
        "SELECT run_id, created_at FROM student_runs WHERE student_id=?", (student_id,))}


def link_run(store: Store, student_id: str, run_id: str) -> None:
    ensure_tables(store)
    store.db.execute(
        "INSERT OR IGNORE INTO student_runs(student_id, run_id, created_at) VALUES (?,?,?)",
        (student_id, run_id, time.time()),
    )


def past_run_ids(store: Store, student_id: str, exclude_run_id: str | None = None) -> list[str]:
    ensure_tables(store)
    rows = store.db.execute(
        "SELECT run_id FROM student_runs WHERE student_id=? ORDER BY created_at",
        (student_id,),
    ).fetchall()
    return [r["run_id"] for r in rows if r["run_id"] != exclude_run_id]


def open_run_id(store: Store, student_id: str) -> str | None:
    """The student's most recent run, if it's still in progress (not
    COMPLETE/FAILED) - so logging in again resumes instead of starting a
    fresh session every time."""
    from slice.records import RunState
    for run_id in reversed(past_run_ids(store, student_id)):
        state = store.get_state(run_id)
        if not state.is_terminal:
            return run_id
    return None


def misconception_summary(store: Store, student_id: str,
                           exclude_run_id: str | None = None) -> dict[str, int]:
    """Total (peak) occurrences per error_type across every past run of this
    student's - read back through the store's own history(), the same
    mechanism a single run already uses internally, just spanning several
    run_ids instead of one."""
    totals: dict[str, int] = {}
    for run_id in past_run_ids(store, student_id, exclude_run_id):
        for v in store.history(run_id, "misconception"):
            error_type = v.payload["error_type"]
            totals[error_type] = max(totals.get(error_type, 0), v.payload["occurrences"])
    return totals


def attempt_totals(store: Store, student_id: str) -> tuple[int, int]:
    """(correct, total) over every graded `attempt` record in every run of
    this student's. Only "attempt" - the record kind flow.py's evaluate step
    writes - so a custom-equation check (kind "custom_check", judged by a
    model rather than by code) never moves a student's mastery figure."""
    correct = total = 0
    for run_id in past_run_ids(store, student_id):
        for a in store.history(run_id, "attempt"):
            total += 1
            correct += bool(a.payload["correct"])
    return correct, total


def top_recurring(store: Store, student_id: str,
                   exclude_run_id: str | None = None) -> tuple[str, int] | None:
    """The single most persistent past bug, if any - what the "second
    encounter" greeting in flow.py's start_run() names."""
    summary = misconception_summary(store, student_id, exclude_run_id)
    if not summary:
        return None
    error_type = max(summary, key=summary.get)
    return error_type, summary[error_type]
