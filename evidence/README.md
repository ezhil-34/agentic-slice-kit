# Evidence: how to test this with real people

Everything a judge will ask to see lives in this folder. Real user evidence is
about a third of the marks, so keep it factual: what people DID, not what they
said they liked.

```
docs/evidence/
  README.md          this file: how to run a session
  walkthrough-1.md   ┐
  walkthrough-2.md   ├ one per tester (fill in as you go)
  walkthrough-3.md   ┘
  stress-test.md     the hostile-classmate session + the fix
  runs/              the app's own record of each session (see step 4)
  recordings/        screen recording of the stress test (mp4/webm)
```

## Before anyone arrives (15 minutes)

1. **Use the real model, not the stub.** With no key the app runs in demo mode
   and every explanation reads `[stub explanation ...]`. A tester would judge
   the stub, not the product. Put the key in `.env`, then check the page does
   NOT show "Demo mode: canned tutor replies".
2. **Do one session yourself first** and get a wrong answer on purpose. The real
   `classify`, `extract` and `reexplain` prompts have only ever run against the
   stub, so this is where any surprise will show up.
3. **Use a clean database for the testers** so their runs are easy to find:
   `TRACKER_DB=evidence.db`, and give each tester a username (`tester1`,
   `tester2`, `tester3`, `hostile`) with any 4-digit PIN.
4. Start the app (see below) and, on a second screen you do NOT show them, the
   dashboard: `uvicorn web.dashboard:app --port 8001`.

```bash
# the app testers use
TRACKER_DB=evidence.db uvicorn web.student:app --host 0.0.0.0 --port 8002
# Codespaces: forward port 8002 and set it Public, send them the URL.
# Same wifi: http://<your-laptop-ip>:8002 (allow it through the firewall).
```

## Running a walkthrough (20 minutes each, three different people)

Pick people outside your team who have not heard the idea. A student who has
done quadratics recently is ideal; one who has not is also informative.

1. **Say nothing about the design.** Hand over the laptop or the URL:
   *"Have a go. Talk out loud while you do."* Do not explain buttons, do not
   rescue them. Silence is where the findings are.
2. **Write verbs, not adjectives.** "Typed 6 in the first box, left the second
   empty, pressed Check, read the red message twice" - not "found it confusing".
   Note every hesitation, re-read, back-button, and question they ask you.
3. **Make sure they hit a wrong answer.** If they get everything right, say
   "try to get one wrong, on purpose" for the last few minutes. The wrong-answer
   path (confirm -> working -> warm-up -> back) is the whole product.
4. **Ask the three closing questions, and write the answers verbatim:**
   - What did you think it would do?
   - Where did you get stuck?
   - What would you have wanted instead?
5. **Save the app's own record** (objective, and shows which stages they reached):
   ```bash
   # the run id is in the address bar: /session/run_xxxxxxxxxxxx
   python scripts/tracker.py --db evidence.db replay run_xxxxxxxxxxxx > docs/evidence/runs/walkthrough-1.txt
   ```
6. **Change something because of it, that day.** Commit it with a message that
   names the walkthrough and the observation, e.g.
   `Walkthrough 1: testers didn't see the Skip button - move it under the boxes`.
   Put that commit id in the walkthrough file. One visible change per
   walkthrough is the minimum; the second and third should show the first
   change worked (or didn't).

Tip: have tester 1 come back for a second session later. It is the only way to
see the "second encounter" greeting with a real person.

## Running the stress test (15 minutes, one hostile classmate)

Brief, word for word: **"Try to make it do something stupid."** Screen-record it
(Win+G on Windows, or OBS) and save to `docs/evidence/recordings/`. Use
`stress-test.md`, which lists the attacks worth trying for THIS app. Whatever
breaks, fix it and commit the fix with a message that names the failing input.
A stress test where nothing broke and nothing changed is weaker evidence than
one that found something.

## Git (you need it for the commit evidence)

This folder is not a git repository yet. Before the first tester:

```bash
git init
git add -A && git commit -m "Baseline before user testing"
```

Then commit after each walkthrough and after the stress-test fix. Never put
`.env`, `*.db`, `*.db-wal` or `*.db-shm` in a commit.

## Order of the day

| When | Do |
|---|---|
| Now | Real-model dry run, `git init`, book three testers + one hostile one |
| Walkthrough 1 | Fill in `walkthrough-1.md`, commit a change |
| Walkthroughs 2, 3 | Same; check the earlier change helped |
| Stress test | Record it, fix what broke, commit |
| Last hour | Rehearse the demo, freeze the commit |
