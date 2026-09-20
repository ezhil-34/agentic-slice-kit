# Walkthrough 2

| | |
|---|---|
| Tester | Syed — 4th-year, your classmate/friend (per your own note) |
| Date / time / length | Session at 19 Sep, 11:00 (per My progress page). Total length — **not visible in the screenshots, fill in** |
| Run id | **Not visible in these screenshots** — check the "Open" link on his My progress page, or `tracker.db` |
| Model | real (not the stub): **not conclusively visible** — the custom-equation checker shows a "Checking… this can take a few seconds" spinner, which reads like a real network call rather than an instant stub reply, but that's a hint, not proof. Confirm from the terminal/`.env`. |
| Told about the design beforehand | **fill in — not something the app records** |

## What they did (verbs, in order)

Reconstructed directly from the 12 screenshots:

- Solved **Q1** correctly, first try (confirmed by the banner on the Q2 screen: "Question 1 solved first time").
- On **Q2** (`x² + 7x + 12 = 0`) — did **not** attempt it. Went straight to **"Show me the answer."** The app displayed the full worked solution (`a=1, b=7, c=12`, discriminant, the formula applied, `x = −3 or x = −4`) and moved directly to Q3 without asking for a retry.
- Separately, used **"Your own equation"** to deliberately test a no-real-roots case: entered `x^2+4x+8=0` with the guessed answer `2, 2`. This is checked by a model, not the app's own maths, and the app says up front it doesn't count toward progress.
- On **Q3** (`x² − 3x − 10 = 0`), first attempt — typed `-5` and `-2` (correct is `5` and `-2` — only one of the two roots was flipped).
- The app matched this to a single known pattern and asked a direct yes/no: *"Looks like picking a factor pair that doesn't actually work — is that right?"*
- Confirmed **"Yes, that's it."**
- Given a **first warm-up** (`x² − 5x + 4 = 0`), taught with a **worked-example** strategy.
- Answered the first warm-up incorrectly — typed `5` and `-1` (correct is `1` and `4`).
- Given a **second warm-up** (`x² + 6x + 8 = 0`), this time taught with a **different strategy — a simpler practice problem** (not a repeat of the worked-example approach).
- Answered the second warm-up correctly.
- Returned to the real Q3, attempt 2, and solved it correctly.
- Moved to **Q4** (`2x² − 5x + 3 = 0`), began a first attempt — no submission captured.

## Where they hesitated or got stuck

- **System-evidenced:** Q3 needed two full warm-up rounds — two different re-teaching strategies — before he got it right. This is the deepest escalation either walkthrough has captured so far, and it resolved rather than getting stuck.
- **Worth reading as deliberate probing, not confusion:** the custom-equation test looks like an intentional stress test of the no-real-roots case, not something he stumbled into.
- Whether he hesitated, re-read, or seemed frustrated at any point — **not visible in static screenshots — fill in from what you actually watched.**

## The three closing questions (their words, verbatim)

**Not captured in any screenshot.**

1. What did you think it would do?
   >
2. Where did you get stuck?
   >
3. What would you have wanted instead?
   >

## What I saw in the app's record

- **The custom-equation check (real finding, worth keeping):** given `x^2+4x+8=0` and the wrong answer `2, 2`, the model correctly computed the discriminant (`16 − 32 = −16`), correctly stated there are no real roots, correctly showed `2, 2` doesn't satisfy the equation, offered a specific plausible error (miscalculating `4ac` or flipping the discriminant's sign), and reported its own working as *"roots it found for this equation: none found"* rather than inventing any. This is exactly the "show its own derivation, never fabricate a root" behavior the custom-question feature needs to be trustworthy — and here it held up on the hardest case (no real solution at all).
- **Q2 reveal:** Planner — "Showed the full worked solution." → "Moving on to question 4" (confirms reveal-then-move-on, not reveal-then-retry).
- **Q3, attempt 1 (wrong):** Evaluator — "Checked your answer against the correct roots - it doesn't match." Diagnoser — "Your answer matches a known mistake pattern." Planner — "Asking whether that's what happened."
- **After confirming "Yes, that's it":** Diagnoser — "Working out what went wrong: picking a factor pair that doesn't actually work." Planner — "Logged this mistake - 1 time on this question." → "Staying with factoring: the quadratic formula would take longer here (3 steps vs 2), so I'll explain it a different way instead." → "A small warm-up using the method you used (factorization), before coming back to this question." Tutor — "Writing a different explanation…." → "Wrote a new explanation as a worked example."
- **After the first warm-up came back wrong**, the same mistake was logged a second time and the Tutor explicitly produced a different kind of explanation this time — "Wrote a new explanation as a **simpler practice problem**" — confirming the no-repeat-strategy rule held under real pressure, not just in a stub test.
- **After the second warm-up (correct):** Evaluator — "Your warm-up answer is correct." Planner — "Setting up another try at this question."
- **Progress page, mid-session (before Q3 was resolved):** the misconception showed as **"Needs work — still unresolved,"** seen 2x, corrected 0 of 1.
- **Progress page, after Q3 was resolved:** the same misconception now shows **"Under control,"** seen 2x, corrected 1 of 1 — confirming the page updates live and correctly as the session progresses, not just at the end.
- **Q2's tile on the progress tracker renders differently from Q3's** ("needed retries" orange) — a distinct, dashed style, which reads as the app tracking "revealed" as its own state rather than lumping it in with "solved after retries." Worth confirming that's intentional.

**A bug this session re-surfaced, in a different spot from walkthrough 1:** the same label-concatenation issue happened again — "You answered Yes, that's itThat is what I did" — this time on the yes/no confirm choice, not the multi-candidate one. Two different confirm screens, same missing separator between a choice's title and its subtitle. This makes it look like a shared rendering component, not two separate bugs — worth fixing once, wherever that summary line is built, rather than patching each screen individually.

## The change this caused

| | |
|---|---|
| What I observed | The concatenation bug recurring in a second location (strengthens the case it's one shared component, not two bugs). The custom-equation checker handling a genuine no-real-roots case correctly and honestly. The two-round warm-up escalation with a real strategy switch, observed outside a stub test for the first time. |
| What I changed | **fill in once you've actually made the fix** |
| Commit | **(id and message — name this walkthrough in the message)** |
| Did it work? | **(fill in after walkthrough 3)** |