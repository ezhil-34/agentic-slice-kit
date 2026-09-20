"""
Canned responses for `classify` and `reexplain` - no key, no network, no
tokens. Build and prove the state machine against this FIRST (README.md
section 12, Phase 1), before spending anything on real model calls.

Unlike demo/smoke/stub.py, replies here can't be a fixed indexed script,
because how many times classify/reexplain get called depends on what the
"student" (a human at a terminal, or a test) actually types. Instead this
reads the prompt's own content to decide what to return - which is honest,
because it is reacting to the same information a real model would see.
"""
from __future__ import annotations

import re
from typing import Any, Type

from pydantic import BaseModel

from .schema import CustomCheck, ErrorClassification, IntermediateValue, ReExplanation


class FakeCall:
    """A drop-in for slice.llm.complete. Same keyword signature, no network."""

    def __init__(self, always_error_type: str = "formula_sign_flip") -> None:
        self.calls: list[str] = []
        self.always_error_type = always_error_type
        self.custom_roots = ["2", "3"]      # what a stubbed custom_check "solves" to

    def __call__(self, *, settings, budget, messages, schema: Type[BaseModel] | None = None,
                 model: str | None = None, step: str = "call", timeout: float = 120.0) -> Any:
        base = step.split(":")[0]
        self.calls.append(step)
        user = messages[-1]["content"]
        budget.record_tokens(len(user) // 4)

        if base == "classify":
            return ErrorClassification(
                error_type=self.always_error_type, confidence=0.9,
                reasoning="stub: no model called, always the same fixed answer for testing",
            )

        if base == "extract":
            # Canned: the first number in the student's reply, or null. Enough to
            # prove the wiring (unlabeled working -> extraction -> code compares).
            typed = user.split("Student's reply:", 1)[-1]
            m = re.search(r"-?\d+(?:\.\d+)?", typed.replace("−", "-"))
            return IntermediateValue(value=float(m.group(0)) if m else None)

        if base == "reexplain":
            m = re.search(r"explicitly asked for: (\w+)", user)
            if m:
                strategy = m.group(1)
            else:
                m = re.search(r"Pick one strategy from: \[(.*?)\]", user)
                first = m.group(1).split(",")[0].strip().strip("'\"") if m else "worked_example"
                strategy = first
            return ReExplanation(
                strategy=strategy,
                explanation=f"[stub explanation using '{strategy}' - no model called]",
            )

        if base == "custom_check":
            # Canned: pretends every custom equation has roots 2 and 3, and
            # judges the student's answer against that. Enough to prove the
            # wiring (route -> schema -> record -> page) with no key.
            typed = user.split("Student's answer:", 1)[-1]
            nums = set(re.findall(r"-?\d+(?:\.\d+)?", typed))
            return CustomCheck(
                computed_roots=self.custom_roots,
                student_correct=nums == {"2", "3"},
                feedback="[stub feedback - no model called]",
            )

        raise AssertionError(f"stub has no canned reply for step {step!r}")
