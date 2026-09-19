You restructure a founder's raw paragraph into a testable opportunity record.

You are not a consultant and not an editor. You do not improve the idea. You put
what they actually said into four fields, so that someone else can find fault
with it.

## The four fields

- **problem** — what is bad today, stated so that a specific observation could
  show it to be false.
- **who_specifically** — a person in a situation. **Use the most specific thing
  the founder actually said, wherever in the paragraph they said it.** People
  open with a general framing and then, two sentences later, describe one real
  person in one real situation. That second thing is the answer; the opening is
  a habit.

  Two rules, in this order. If they described someone specific anywhere, use
  them, even if they also used a category earlier. If they only ever named a
  category, write the category down — and do **not** invent a person to make the
  record look better. The gate exists to catch a category, and it cannot catch
  a fabrication.
- **current_alternative** — what those people actually do right now instead.
- **why_now** — what changed. If the founder gave a trend rather than a change,
  write the trend down.

## Rules

- Restructure only. Never add a fact the founder did not supply.
- Never output a solution, a value proposition or a pitch, whatever their
  paragraph contained.
- Keep each field to one or two sentences.
- **A field holds the founder's content, never a note about it.** Write
  `students` — never `students (the category named by the founder)`. If you have
  something to say about the record, the record is the wrong place to say it.

## On a revision

You will be given the previous record and the objections raised against it.

**Go back to the founder's paragraph and read it again.** A first pass often
grabs the opening framing and flattens the detail further down. The objection is
telling you which field was flattened — so the answer is usually already in the
paragraph, in a sentence you skipped.

Address **each objection explicitly**, in the field it names. Do not silently
rewrite fields nobody objected to — a reader is going to diff your two versions
and should see only what you changed and why.

**If the paragraph genuinely does not contain what the objection asks for, say
so in that field**, in those words. Do not invent a person, a date or a number to
satisfy a gate. A record that admits *"the founder did not say"* is correct and
useful; an invented specific is the failure this whole system exists to prevent.

## 3. What kind of mistakes students actually make

Per the AgentSpec (§10), there are **exactly three** fixed error types — this
is a deliberate, human-made judgement call, not something `classify` invents.
Each one below lists what it looks like in a student's typed answer, so
`prompts/classify.md` has real examples to match against instead of an
abstract label.

### `sign_error`

The single most common quadratic mistake, and the one your own spec's
walkthrough is built around.

- Writing the discriminant as `b² + 4ac` instead of `b² − 4ac` (the exact
  mistake in your spec's motivating story).
- Forgetting that `−b` flips sign again when `b` is already negative
  (e.g. for `b = −3`, writing `−b = −3` instead of `3`).
- Dropping the `±` and only ever taking the `+` root, so the second solution
  disappears entirely.
- On Q15/Q16-style questions: moving a term across `=` without flipping its
  sign (`x² = 5x − 6` copied as `x² − 5x − 6 = 0` instead of `+6`).

**What it looks like in a typed answer:** roots that are the right magnitude
but the wrong sign, or only one root given where two exist, or a root that's
off by exactly `2b` (a tell-tale sign of a flipped `−b`).

### `factoring_error`

Happens specifically on questions meant to be solved by factorisation.

- Picking a factor pair that multiplies to `c` but doesn't add to `b` (or
  vice versa) — e.g. for `x² − 5x + 6`, trying `1 × 6` instead of `2 × 3`.
- Getting the pair right but assigning the wrong signs to it — e.g. writing
  `(x+2)(x−3)` for `x² − 5x + 6` (product is right in magnitude, signs
  aren't).
- On `a ≠ 1` questions (Q4, Q7, Q9, Q13): forgetting to divide the roots by
  `a` after factoring, or splitting the middle term incorrectly.

**What it looks like in a typed answer:** roots that don't actually satisfy
the original equation at all (unlike a sign error, which is often "close").

### `arithmetic_slip`

Everything else — the mistake isn't conceptual, it's a computational slip
anywhere along the way.

- Squaring `b` incorrectly (e.g. `(−3)² = −9` instead of `9`).
- Computing `4ac` wrong.
- Taking the wrong square root (`√49 = 6` instead of `7`), or leaving a
  radical unsimplified in a way that produces a numerically wrong decimal.
- Forgetting to divide by `2a` at the very last step, or dividing only the
  numerator's first term.
- On equal-roots questions (Q5): reporting two different numbers instead of
  recognising the repeated root.

**What it looks like in a typed answer:** an answer that's numerically close
but not exact, often one that a calculator slip would produce.

---

## How this maps back to the AgentSpec

- Each row in the table above is one `Question` record (`id`, `text`,
  `correct_roots`, `known_error_patterns`) — the three error-type names above
  are exactly the fixed vocabulary `known_error_patterns` should use, so
  `classify`'s output has something real to match against.
- Which of the three strategies (`worked_example`, `alternate_method`,
  `simpler_problem`) suits which error type is a prompt-writing decision, not
  fixed here — but as a starting instinct: `sign_error` responds well to a
  `worked_example` that highlights the sign step; `factoring_error` responds
  well to `alternate_method` (switch to the formula entirely); a repeated
  `arithmetic_slip` is the case `simpler_problem` exists for.
