"""
The wrong-answer ladder's pure-code parts: everything that can be settled by
the numbers, kept out of the model on purpose (README.md section 8 - "kept out
of the model means correctness is never a matter of the model's opinion").

    control_word        "skip" / "show me" - the global escape hatches
    read_reply          what kind of working did the student type, and what
                        does it claim (a discriminant value, or a factor pair)?
    check_discriminant  compare that claim with the right and known-wrong values
    check_factor_pair   ... or with a valid / sign-flipped / wrong pair
    pick_scaffold       which fixed, hand-checked warm-up question comes next
    solution_text       the full worked solution shown by "show me"
    leaks_answer        did a scaffold explanation give the real question away?

The one thing that is NOT here is reading free-form working with no label on it
("I got minus sixteen"): that is a narrow model *extraction* call in flow.py,
and even then the number it returns is only ever compared by code from here.
"""
from __future__ import annotations

import re
from fractions import Fraction

from .plan import method_effort
from .schema import (OPERATOR_METHOD, REVEAL_WORDS, SCAFFOLDS, SKIP_STEP, SKIP_WORDS,
                      _discriminant, predict_operator)

TOL = 0.05


def _close(x: float, y: float, tol: float = TOL) -> bool:
    return abs(x - y) <= tol


# ---------------------------------------------------------------- control words

def _words(raw: str) -> str:
    t = (raw or "").lower().replace("’", "'").strip()
    t = re.sub(r"[.!?,]+$", "", t)
    return re.sub(r"\s+", " ", t)


def control_word(raw: str) -> str | None:
    """"skip" or "reveal" if the whole reply is one of the escape words, else
    None. An exact match, never a substring: working like "I did not skip a
    step" must not abandon the question."""
    t = _words(raw)
    if t in SKIP_WORDS:
        return "skip"
    if t in REVEAL_WORDS:
        return "reveal"
    return None


def skips_step(raw: str) -> bool:
    """The intermediate-step prompt's own "skip this step" (or no answer at all)."""
    return _words(raw) in ("", SKIP_STEP, "skip step")


# ------------------------------------------------------------- reading working

def normalize(raw: str) -> str:
    """Make typed maths comparable. Unicode minus MUST become an ASCII hyphen
    first: left alone, "−16" would not parse as negative, and a sign error
    would be introduced by the very code that exists to detect sign errors."""
    t = (raw or "").lower()
    for ch in "−–—‒﹣－":
        t = t.replace(ch, "-")
    for ch in "×·⋅✕":
        t = t.replace(ch, "*")
    t = t.replace("Δ".lower(), "delta").replace("∆", "delta")     # Greek delta, both code points
    t = t.replace("²", "^2").replace("√", "sqrt")
    return t


_NUM = r"-?\d+(?:\.\d+)?"
_ANCHOR = re.compile(r"delta|discriminant|(?<![a-z])d\s*=")


def _anchored_discriminant(t: str) -> float | None:
    """The claimed FINAL value of the discriminant: on the last line that names
    it (delta / discriminant / D=), the number after its last '='. Anchoring on
    a labelled line matters because typed working is full of other numbers -
    the coefficients, the arithmetic on the way - and scanning for "a number"
    would pick one of those."""
    for line in reversed([ln for ln in re.split(r"[\n;]+", t) if _ANCHOR.search(ln)]):
        if "=" in line:
            m = re.fullmatch(rf"\s*({_NUM})\s*", line.rsplit("=", 1)[1])
            if m:
                return float(m.group(1))
        m = re.search(rf"(?:delta|discriminant)\D*?({_NUM})\s*$", line)
        if m:
            return float(m.group(1))
    return None


def _numbers(t: str) -> list[float]:
    return [float(x) for x in re.findall(rf"(?<![\d.]){_NUM}", t)]


def read_reply(raw: str) -> dict:
    """{"kind": "discriminant", "value": float}    a labelled discriminant
       {"kind": "factor_pair", "pair": (p, q)}     exactly two numbers and no
                                                   discriminant label - "the two
                                                   numbers I multiplied"
       {"kind": "unlabeled"}                       nothing code can anchor on -
                                                   the caller may try the model
                                                   extraction call, or give up."""
    t = normalize(raw)
    value = _anchored_discriminant(t)
    if value is not None:
        return {"kind": "discriminant", "value": value}
    if not _ANCHOR.search(t):
        nums = _numbers(t)
        if len(nums) == 2:
            return {"kind": "factor_pair", "pair": (nums[0], nums[1])}
    return {"kind": "unlabeled"}


def check_discriminant(a: float, b: float, c: float, value: float) -> dict:
    """The student's claimed discriminant against the right one and the one the
    known bug produces. Only the known-wrong value resolves to a bug; the right
    value means this step was fine (so it cannot name the mistake), and any
    other value is an arithmetic slip no operator predicts."""
    if _close(value, _discriminant(a, b, c)):
        return {"verdict": "correct", "operator": None}
    if _close(value, b * b + 4 * a * c):
        return {"verdict": "known_wrong", "operator": "formula_discriminant_sign"}
    return {"verdict": "other", "operator": None}


def check_factor_pair(a: float, b: float, c: float, pair: tuple[float, float]) -> dict:
    """The two numbers a student multiplied, against what factoring needs:
    product a*c and sum b. Right numbers with the signs flipped is the
    factoring sign bug; anything else is a wrong pair."""
    p, q = pair
    if _close(p * q, a * c) and _close(p + q, b):
        return {"verdict": "valid", "operator": None}
    if _close(p * q, a * c) and _close(p + q, -b):
        return {"verdict": "sign_flipped", "operator": "factor_sign_flip"}
    return {"verdict": "wrong_pair", "operator": "factor_wrong_pair"}


# --------------------------------------------------------------------- scaffolds

def pick_scaffold(root: dict, error_type: str, used: set[str]) -> tuple[dict | None, str]:
    """The next warm-up question and why, chosen by code from the fixed bank.
    Same method as the mistake (so they practise the step that broke), and one
    where that mistake would visibly give a different answer. With no method
    known (`unclassified`), the method that is quicker for the real question."""
    method = OPERATOR_METHOD.get(error_type)
    known = method is not None
    if method is None:
        method = "factorization" if method_effort(root, "factorization") < method_effort(root, "formula") else "formula"
    unused = [sq for sq in SCAFFOLDS if sq["id"] not in used]

    def shows_bug(sq: dict) -> bool:
        pred = predict_operator(error_type, sq["a"], sq["b"], sq["c"]) if known else None
        return pred is not None and pred != set(sq["roots"])

    for pool in ([sq for sq in unused if sq["method"] == method and shows_bug(sq)],
                 [sq for sq in unused if sq["method"] == method], unused):
        if pool:
            sq = pool[0]
            how = "the method you used" if known else "a method that is quick for this equation"
            return sq, f"A small warm-up using {how} ({sq['method']}), before coming back to this question."
    return None, "No warm-up questions are left for this method."


# ---------------------------------------------------------------- worked answer

def fmt(x: float) -> str:
    f = Fraction(x).limit_denominator(1000)
    text = str(f.numerator) if f.denominator == 1 else f"{f.numerator}/{f.denominator}"
    if abs(float(f) - x) > 1e-9:
        text = f"{x:g}"
    return text.replace("-", "−")


def solution_text(spec: dict) -> str:
    """The full worked solution, written by code from the question's own a/b/c
    and roots - never by a model, so "show me" can never show a wrong answer."""
    a, b, c = spec["a"], spec["b"], spec["c"]
    D = _discriminant(a, b, c)
    head = (f"{spec['text']}: a = {fmt(a)}, b = {fmt(b)}, c = {fmt(c)}. "
            f"Discriminant b² − 4ac = {fmt(D)}.")
    if D < 0:
        return head + " It is negative, so there is no real solution."
    roots = sorted(set(spec["roots"]), reverse=True)
    if len(roots) == 1:
        return head + f" x = −b / 2a = {fmt(roots[0])} (a double root)."
    return head + f" x = (−b ± √Δ) / 2a, so x = {fmt(roots[0])} or x = {fmt(roots[1])}."


def _stated_values(text: str) -> set[float]:
    """Numbers an explanation asserts as answers: after "x =", or in a list
    introduced by "the roots are" / "the solutions are" / "the answers are"."""
    t = normalize(text)
    out: set[float] = set()

    def add(token: str) -> None:
        token = token.replace(" ", "")
        if "/" in token:
            num, den = token.split("/")
            if float(den):
                out.add(round(float(num) / float(den), 4))
        else:
            out.add(round(float(token), 4))

    num = rf"{_NUM}(?:\s*/\s*\d+)?"
    for m in re.finditer(rf"x\s*=\s*({num})", t):
        add(m.group(1))
    for m in re.finditer(r"(?:roots?|solutions?|answers?)\s*(?:are|is|:)\s*([^.\n]*)", t):
        for token in re.findall(num, m.group(1)):
            add(token)
    return out


def leaks_answer(explanation: str, roots: list[float]) -> bool:
    """True if the text states every root of the real question. A warm-up
    explanation should teach the method, not hand over the answer."""
    stated = _stated_values(explanation)
    return bool(roots) and all(round(r, 4) in stated for r in set(roots))
