# Walkthrough 1

| | |
|---|---|
| Tester | Habinaya — 2nd-year, junior (per your own note) |
| Date / time / length | Session started 19 Sep, 10.00 (per My progress page). End time / total length — **not visible in the screenshots, fill in** |
| Run id | **Not visible in these screenshots** — check the "Open" link on the My progress page, or `tracker.db` |
| Model | real (not the stub): **not visible in these screenshots — confirm from the terminal/`.env` before finalizing** |
| Told about the design beforehand | **fill in — not something the app records** |

## What they did (verbs, in order)

Reconstructed directly from the 8 screenshots, in the order the app shows them:

- Solved **Q1** (`x² − 5x + 6 = 0`) — answered `2` and `3` — correct, first try.
- Moved to **Q2** (`x² + 7x + 12 = 0`), first attempt.
- Typed `4` and `3` as the answer — wrong (the correct roots are `-3, -4`; this is the negated pair).
- The app stopped and showed: *"Your answer matches more than one kind of mistake... which of these is closest to what you did?"* — three choices: sign errors in the quadratic formula, sign errors when factoring, or something else.
- Selected **"Sign errors in the quadratic formula."**
- Was given a smaller **warm-up question** (`x² − 6x + 8 = 0`) with a worked-example explanation of the `−b` sign step, before being sent back to the real question.
- Typed `2` and `4` on the warm-up — correct.
- Returned to **Q2, attempt 2** — the worked example stayed visible above the fresh input boxes.
- Solved **Q2 on attempt 2** (per the banner on the Q3 screen: "Question 2 solved on attempt 2").
- Moved to **Q3** (`x² − 3x − 10 = 0`), first attempt — the x₁ field is focused in the last screenshot; no submission for Q3 was captured.

## Where they hesitated or got stuck

- **System-evidenced stuck point:** Q2, attempt 1. Her wrong answer (`4, 3`) matched two different known mistakes at once (a formula sign-flip and a factoring sign-flip produce the identical wrong numbers), so the app couldn't diagnose it from the number alone and had to stop and ask her directly which one she'd actually done.
- Whether *she* personally paused, re-read the question, or hesitated before typing isn't something a screenshot can show — **fill this in from what you actually watched her do.**

## The three closing questions (their words, verbatim)

**Not captured in any screenshot — these only exist if you actually asked her and wrote down what she said. Do not skip filling these in; a walkthrough without them is missing the part the rubric weighs most.**

1. What did you think it would do?
   >
2. Where did you get stuck?
   >
3. What would you have wanted instead?
   >

## What I saw in the app's record

Transcribed directly from the "Agents at work" / "What the agents did" panels across the screenshots:

- **Q2, attempt 1 (wrong):**
  Evaluator — "Checked your answer against the correct roots - it doesn't match."
  Diagnoser — "Your answer fits more than one kind of mistake."
  Planner — "Asking which of the matching mistakes you made."
- **After she picked "sign errors in the quadratic formula":**
  Planner — "Noted which mistake you said you made."
  Diagnoser — "Working out what went wrong: sign errors in the quadratic formula."
  Planner — "Logged this mistake - 1 time on this question."
  Planner — "Factoring is quicker than the quadratic formula for this equation (2 steps vs 3), so switching method is worth it."
  Planner — "A small warm-up using the method you used (formula), before coming back to this question."
  Tutor — "Writing a different explanation…." → "Wrote a new explanation as a worked example."
  Planner — "Setting up a smaller warm-up question."
- **After the correct warm-up answer:**
  Evaluator — "Your warm-up answer is correct."
  Planner — "Setting up another try at this question."
- **After the correct Q2 retry:** confirmed via the Q3 screen's banner ("Question 2 solved on attempt 2"), Evaluator/Planner logged the same correct→move-on pattern as Q1.
- **Skip / Show me:** not used at any point captured in these screenshots.
- **Did the warm-up help?** Yes, on the evidence shown — the very next attempt on the real question (Q2, attempt 2) was correct.
- **Cross-session record (My progress page):** the misconception is logged as *"Sign errors in the quadratic formula," seen 1x on 1 question, corrected 1 of 1*, status "Under control," with a standing tip ("Put −b in brackets first..."). This is the same run being reflected back correctly.

**One concrete bug this walkthrough surfaced, worth fixing regardless of anything else:** right after she picked a mistake option, the "you answered" summary line rendered as `Sign errors in the quadratic formulaQuadratic formula` — the option's title and its subtitle appear to be concatenated with no space or separator between them. Worth checking whatever template builds that confirmation summary line.

## The change this caused

| | |
|---|---|
| What I observed | The concatenated-label display bug above. Also: the collision→ask→warm-up→retry loop worked exactly as designed on a real, unscripted wrong answer — this is the first time it's been observed outside a stub test. |
| What I changed | **fill in once you've actually made the fix** |
| Commit | **(id and message — name this walkthrough in the message)** |
| Did it work? | **(fill in after walkthrough 2 or 3)** |