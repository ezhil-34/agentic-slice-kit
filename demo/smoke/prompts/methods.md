# Quadratic Equations — Domain Reference

This is the subject-matter backbone for the Misconception Tracker. It exists
so that whoever writes `prompts/classify.md`, `prompts/reexplain.md`, and the
question bank is working from the same facts — not improvising them under
time pressure on Day 1.

It has three parts, matching the three things a hand-written question needs:
**how to solve one**, **what to actually ask**, and **how a student typically
gets it wrong**.

---

## 1. Methods to solve a quadratic equation

CBSE Class 10 teaches three methods. All three should appear somewhere in the
question bank, because each one invites a different kind of slip.

### Method 1 — Factorisation

Write `ax² + bx + c = 0` as a product of two linear factors and set each to
zero.

**Steps**
1. Find two numbers that multiply to `a×c` and add to `b`.
2. Split the middle term using those two numbers.
3. Factor by grouping.
4. Set each factor to zero and solve.

**Worked example** — `x² − 5x + 6 = 0`
Need two numbers that multiply to `6` and add to `−5` → `−2` and `−3`.
`x² − 2x − 3x + 6 = 0 → x(x−2) − 3(x−2) = 0 → (x−2)(x−3) = 0`
**Roots: x = 2, x = 3**

Fastest method when it works. Fails silently when the roots aren't rational —
a student can burn a lot of time hunting for integer factors that don't exist.

### Method 2 — Completing the square

Rewrite the equation so one side is a perfect square.

**Steps**
1. Divide through by `a` so the leading coefficient is 1.
2. Move the constant to the other side.
3. Add `(b/2)²` to both sides.
4. Write the left side as a square and take the square root of both sides.

**Worked example** — `x² − 4x + 1 = 0`
`x² − 4x = −1 → x² − 4x + 4 = −1 + 4 → (x−2)² = 3 → x − 2 = ±√3`
**Roots: x = 2 + √3, x = 2 − √3**

The method the quadratic formula is actually derived from — useful to know
because a student's *wrong* intermediate step often reveals which one they
were really attempting.

### Method 3 — The quadratic formula

For `ax² + bx + c = 0`:

```
x = ( −b ± √(b² − 4ac) ) / (2a)
```

Always works. Also where almost every sign mistake in this whole project
actually happens — see §3 below.

**Worked example** — `x² − 3x − 10 = 0`
`a=1, b=−3, c=−10 → b² − 4ac = 9 − 4(1)(−10) = 9 + 40 = 49 → √49 = 7`
`x = (3 ± 7) / 2` → **Roots: x = 5, x = −2**

**A rule worth stating explicitly in a prompt:** the correct-roots check
(`evaluate`) never depends on *which* method the student used — only whether
the final numbers are right. Method is something `classify` infers from the
mistake pattern, not something the student is asked to declare.

---

## 2. The question bank

16 questions (inside the 15–20 range the spec calls for), ordered easy → hard,
deliberately mixing methods so no single strategy covers the whole set.
`Q3` is kept identical to the worked walkthrough already in the AgentSpec, so
the demo and the written spec stay in sync.

| # | Equation | Standard form (a, b, c) | Correct roots | Natural method | What this question tends to trap |
|---|---|---|---|---|---|
| Q1 | x² − 5x + 6 = 0 | 1, −5, 6 | 2, 3 | Factorisation | Baseline — should be clean |
| Q2 | x² + 7x + 12 = 0 | 1, 7, 12 | −3, −4 | Factorisation | Both roots negative — sign of the pair |
| Q3 | x² − 3x − 10 = 0 | 1, −3, −10 | 5, −2 | Formula | Negative `c` → `−4ac` becomes positive; classic sign trap (this is the spec's own walkthrough question) |
| Q4 | 2x² − 5x + 3 = 0 | 2, −5, 3 | 3/2, 1 | Factorisation | Leading coefficient ≠ 1 |
| Q5 | x² − 4x + 4 = 0 | 1, −4, 4 | 2 (repeated) | Completing the square | Discriminant = 0 — one root, not two |
| Q6 | x² + 2x − 8 = 0 | 1, 2, −8 | −4, 2 | Factorisation | Mixed-sign roots |
| Q7 | 3x² − 2x − 1 = 0 | 3, −2, −1 | 1, −1/3 | Formula | Negative `c` with `a ≠ 1` |
| Q8 | x² − 2x − 3 = 0 | 1, −2, −3 | 3, −1 | Factorisation | — |
| Q9 | 2x² + 3x − 2 = 0 | 2, 3, −2 | 1/2, −2 | Formula | Fractional root easy to mis-simplify |
| Q10 | x² − 7x + 10 = 0 | 1, −7, 10 | 5, 2 | Factorisation | — |
| Q11 | x² − 4x + 1 = 0 | 1, −4, 1 | 2 + √3, 2 − √3 | Formula | Not factorable — forces the formula |
| Q12 | x² − 6x + 2 = 0 | 1, −6, 2 | 3 + √7, 3 − √7 | Formula | Same as Q11, harder radical |
| Q13 | 2x² − 4x − 3 = 0 | 2, −4, −3 | (2+√10)/2, (2−√10)/2 | Formula | Negative `c` **and** `a ≠ 1` — double sign risk |
| Q14 | x² − 3x + 1 = 0 | 1, −3, 1 | (3+√5)/2, (3−√5)/2 | Formula | Small discriminant, easy arithmetic slip |
| Q15 | x² = 5x − 6 | *(must rearrange to x² − 5x + 6 = 0 first)* | 2, 3 | Factorisation | Not given in standard form — sign flips when moving terms across `=` |
| Q16 | 3x² + 5 = 8x | *(must rearrange to 3x² − 8x + 5 = 0 first)* | 5/3, 1 | Formula | Same rearrangement trap, with a fractional root |

**Why Q15/Q16 exist deliberately:** every other question is already in
`ax² + bx + c = 0` form. These two aren't — the student has to move a term
across the equals sign first, and that's exactly where a sign gets dropped.
It's the same underlying mistake as the formula's sign trap, just earlier in
the process.

---

