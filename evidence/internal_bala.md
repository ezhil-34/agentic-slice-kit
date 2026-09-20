# Internal check — Balamurugan

**Note before anything else: Balamurugan is a teammate, not an outside
tester.** ON-THE-DAY.md's rubric asks for walkthroughs with people outside
the team. This session is a useful internal check, but it should not be
one of your three required walkthroughs when you're assembling evidence for
the demo.

| | |
|---|---|
| Tester | Balamurugan — teammate |
| Date / time / length | **not visible in these 7 screenshots — fill in** |
| Run id | **not visible — check `tracker.db`** |
| Model | real (not the stub): **not conclusively visible from these screenshots** |
| Told about the design beforehand | n/a — he's on the team and already knows the design; this question matters less here than for a real outside tester |

## What they did (verbs, in order)

- **Q1** (`x² − 5x + 6 = 0`) — did not attempt it. Went straight to **"Show me the answer."** Full worked solution shown (`x = 3 or x = 2`), moved on to Q2.
- **Q2** (`x² + 7x + 12 = 0`) — reached, shown fresh. No submission was captured on screen, but since Q3 was subsequently reached, Q2 must have been answered (presumably correctly) off-screen — **inferred, not directly evidenced.**
- **Q3** — explicitly **skipped** via the "Skip question" button, landing on Q4.
- **Q4** (`2x² − 5x + 3 = 0`), attempt 1 — answered `2` and `3` (wrong; the actual roots are `1.5` and `1` — this reads like the coefficients got typed in instead of a solved answer). The system matched it to a single known pattern and asked to confirm: *"Looks like dividing by the wrong number at the end of the formula — is that right?"* He confirmed **"Yes, that's it."**
- Given a **warm-up** (`x² − 6x + 8 = 0`), taught with a worked-example explanation specifically about the `2a` divisor step. Answered it **correctly.**
- Returned to the real Q4, attempt 2 — answered `1/5` and `1` (wrong again — a different-looking slip than attempt 1). The system flagged this as matching a **different** known pattern this time and asked: *"Looks like picking a factor pair that doesn't actually work — is that right?"*
- **Session ends here, unanswered** — per your note, he quit before responding to this second confirm question.

## Where they hesitated or got stuck

- **System-evidenced:** two different wrong attempts on the same question (Q4), each diagnosed as a *different* underlying bug — not a repeat of the same mistake.
- **Worth investigating directly, not just noting:** the run was left suspended on an unanswered confirm question when he quit. Worth checking what actually happens to that run now — does it just sit waiting forever, or does `slice/callback.py`'s expert-timeout setting eventually resolve it on its own? This is exactly the kind of abandoned-session case a real classroom will produce regularly, and it's worth knowing the answer before a demo, not discovering it live.
- Whether he was distracted, testing the skip/reveal buttons on purpose, or genuinely lost interest — **not visible in screenshots, fill in.**

## The three closing questions (their words, verbatim)

Less critical here since he's a teammate, but still worth having if you asked:

1. What did you think it would do?
   >
2. Where did you get stuck?
   >
3. What would you have wanted instead?
   >

## What I saw in the app's record

- **Q1:** Planner — "Showed the full worked solution." → "Moving on to question 2."
- **Q3:** Planner — "Skipped that question." → "Moving on to question 4."
- **Q4, attempt 1 (wrong):** confirmed as "dividing by the wrong number at the end of the formula." Diagnoser logged it, Planner started a worked-example warm-up on the `2a` step.
- **Warm-up:** answered correctly — "Your warm-up answer is correct," back to a fresh attempt.
- **Q4, attempt 2 (wrong):** Evaluator — "Checked your answer against the correct roots - it doesn't match." Diagnoser — "Your answer matches a known mistake pattern." Planner — "Asking whether that's what happened." — pending, unanswered at session end.

**The same display bug, now a third time:** "You answered Yes, that's itThat is what I did" — the identical concatenation seen in both of the last two walkthroughs, on the same confirm-style UI element. Three different sessions, three different testers, same exact rendering. This isn't an edge case anymore — it's the default behavior of whatever component renders a confirmed choice, and it should be the first thing fixed before the next session, not the third.

## The change this caused

| | |
|---|---|
| What I observed | The label bug's third occurrence — strong enough evidence now to fix once at the source rather than treat as isolated. An abandoned/quit session left genuinely suspended mid-question, worth confirming the timeout behavior actually works. |
| What I changed | **fill in once you've actually made the fix** |
| Commit | **(id and message)** |
| Did it work? | **(fill in after checking the abandoned-run behavior, and after a real outside-tester walkthrough 3)** |