# Walkthrough 3

**This one counts.** Srimathi is a classmate, not a teammate — this is your
third genuine outside-tester walkthrough (alongside Habinaya and Syed).

**One methodology note, stated up front rather than buried:** this session
was a *directed* test, not a blind first encounter — you told her
specifically to see whether the app teaches differently on a repeat
mistake, and to try giving intermediate steps. That's still real, valuable
evidence, but it answers a different question than an unprompted session
does ("does the feature work when someone is deliberately trying it" rather
than "does a stranger discover and use it naturally"). Worth having both
kinds before the demo, not just this one.

| | |
|---|---|
| Tester | Srimathi — classmate, final year (per your note) |
| Date / time / length | **not visible in these 4 screenshots — fill in** |
| Run id | **not visible — check `tracker.db`** |
| Model | real (not the stub): **not conclusively visible from these screenshots** |
| Told about the design beforehand | **Yes, partially** — you told her the goal was to see the different-approach re-teaching and to try giving intermediate steps. Not a blind test; noted above. |

## What they did (verbs, in order)

Directly evidenced by the 4 screenshots:

- Solved **Q1** (`x² − 5x + 6 = 0`) correctly, first try — answered `2` and `3`.
- Moved to **Q2** (`x² + 7x + 12 = 0`), first attempt — answered `3` and `4` (the correct roots are `-3` and `-4` — both signs flipped, the same collision pattern seen in Habinaya's session on this exact equation).
- The app detected the collision: *"Your answer fits more than one kind of mistake."*
- **[Gap — not directly shown in these 4 screenshots, but strongly implied]** The log line right after this is "Planner: Noted that it was something else" — this reads like she was shown a multiple-choice confirm (the same "sign errors in the formula / sign errors when factoring / something else" pattern from Habinaya's session) and picked **"something else,"** but that specific screen isn't among the 4 images given. Worth confirming directly rather than assuming.
- The app then asked an **open, free-text** question — new, and not seen in either of the other two walkthroughs: *"I can't pin your mistake down from the answer alone. What did you get for Δ (b² − 4ac)? Or, if you factored, which two numbers did you multiply?"* with a plain text box, plus "Skip this step," "Skip question," and "Show me the answer" as options.
- She began typing her factoring working directly into the box: `(x-3)(x-4` — using minus signs, where the correct factoring is `(x+3)(x+4)`. This is a live, in-progress capture (the paren isn't even closed yet) — a sign error specifically in the factoring step, matching what you described watching.

**Everything past this point is your account, not these screenshots:** you
said she went on to submit this, and the app correctly recognized it as a
factoring sign error. I have no screenshot of that outcome — if you have
one, it's worth adding, since it's the part that actually proves the
free-text parsing worked, not just that the question got asked.

## Where they hesitated or got stuck

Given this was a directed feature test rather than an organic session,
"stuck" isn't really the right frame here — she was deliberately probing a
specific mechanism, not working through confusion. Nothing in the
screenshots reads as her being lost; the pauses (if any) while typing
`(x-3)(x-4` aren't something a static image can show either way.

## The three closing questions (their words, verbatim)

Less naturally applicable to a directed test, but still worth having if
you asked them:

1. What did you think it would do?
   >
2. Where did you get stuck?
   >
3. What would you have wanted instead?
   >

## What I saw in the app's record

- **Q2, attempt 1 (wrong):** Evaluator — "Checked your answer against the correct roots - it doesn't match." Diagnoser — "Your answer fits more than one kind of mistake."
- **Immediately before the free-text question:** Planner — "Noted that it was something else." → "Asking to see one step of your working."
- **The free-text question itself is a real, new capability confirmed here:** unlike the fixed-choice confirms in the other two walkthroughs, this one accepts open text ("Type it however you like — for example Δ = 16 − 32 = −16, or −2 and −3") — meaning the intermediate-step parsing discussed at length earlier in this project (anchoring on a labeled Δ line, or reading a stated factor pair) is now something to verify against a *real* typed answer, not just a design.
- **Worth checking directly, not assumed:** whether `(x-3)(x-4)` actually gets parsed correctly into "the two numbers she multiplied" and compared against the correct factors to land on `factor_sign_flip`. This exact input — parenthesized factor notation with a sign error — is close to the hardest realistic case the parser needs to handle, and it's exactly the kind of input you'd want a test to have hit.

**A real inconsistency worth resolving, not ignoring:** Habinaya hit the identical collision on this exact equation (`x² + 7x + 12 = 0`, both roots negated) and was shown a 2-button confirm first. Srimathi hit the same collision and — based on the log — seems to have gone through that confirm and landed on "something else" before reaching the free-text question, but that confirm screen isn't in these 4 images. Before writing this off as "it's just a later step in the same flow," it's worth actually re-running this exact scenario once yourself to confirm the two sessions really did follow the same path — a silent behavior difference on identical input would be a real bug, not a coincidence of what got screenshotted.

## The change this caused

| | |
|---|---|
| What I observed | First real evidence the free-text intermediate-step question actually fires, escalating past a rejected multiple-choice confirm — this is more of the designed escalation ladder confirmed built than either prior walkthrough showed. |
| What I changed | **fill in — and specifically, get the outcome screenshot for what happened after she hit Send** |
| Commit | **(id and message)** |
| Did it work? | **fill in — this is the one open question this walkthrough can't answer on its own: did the parser actually get her factoring right?** |