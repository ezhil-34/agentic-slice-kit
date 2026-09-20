# Architecture: The Misconception Tracker

## 1. System Overview & Core Philosophy

The **Misconception Tracker** is an agentic intelligent tutoring system built on the `agentic-slice-kit` foundation. It is designed to diagnose, remediate, and track algebraic misconceptions in secondary school students solving quadratic equations.

### The Core Architectural Axiom:
> **Correctness and diagnosis must be deterministic math, not model opinion.**

1. **Pure-Code Ground Truth**: Root calculation, student root parsing, and misconception operator prediction are executed strictly in deterministic Python code. A language model is **never** asked whether a mathematical answer is right or wrong.
2. **Deterministic Operator Layer**: 5 classical quadratic error patterns are checked numerically against the question's coefficients ($a, b, c$) before any LLM is called.
3. **Targeted Agentic Role**: LLMs are used solely where natural language synthesis is essential (scaffold explanations and fallback diagnosis for unlabelled student working).
4. **Safety & Escape Hatches**: Escape words (`skip`, `show me`, `idk`) are checked first at every stage so students are never trapped in infinite remediation loops.

---

## 2. System Architecture & Component Diagram

```
                     ┌────────────────────────────────────────┐
                     │          Student Answer Input          │
                     │    (CLI or Web Browser / Fastify)      │
                     └───────────────────┬────────────────────┘
                                         │
                                         ▼
                               [ `DRAFTING` State ]
                ┌──────────────────────────────────────────────────┐
                │ 1. parse_roots(): extract numbers & fractions    │
                │ 2. roots_match(): tolerance & order-independent  │
                │ 3. If correct: append attempt -> Next Question   │
                │ 4. If wrong: match_operators(a, b, c, roots)     │
                └────────────────────────┬─────────────────────────┘
                                         │
                ┌────────────────────────┴─────────────────────────┐
                │                                                  │
          [ 1 Match ]                                         [ 0 Matches ]
                │                                                  │
                ▼                                                  ▼
     Ask "Looks like X, right?"                          Ask Intermediate Step
         (Phase: `confirm`)                           (Phase: `intermediate_step`)
                │                                                  │
                └────────────────────────┬─────────────────────────┘
                                         │
                                         ▼
                               [ `GATING` State ]
                ┌──────────────────────────────────────────────────┐
                │ 1. Read student response via callback.answer()   │
                │ 2. Check control words ('skip', 'show me')       │
                │ 3. Extract intermediate values (or LLM fallback) │
                │ 4. Record `classification` and `misconception`   │
                └────────────────────────┬─────────────────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         ▼                               ▼
                 [ ≥ 3 Repeats or ]               [ Standard Case ]
                 [ 2 Scaffolds    ]                      │
                         │                               ▼
                         ▼                       [ `PROBING` State ]
                 Present Choice Modal             1. plan_strategy()
                 (Worked Solution,                2. pick_scaffold()
                  Simpler Problem)                3. Complete ReExplanation
                         │                        4. leaks_answer() Guard
                         │                               │
                         ▼                               ▼
                 [ `AWAITING_EXPERT` ] ──▶ Present Scaffold Question
```

---

## 3. Mapping to Kit States (`slice/records.py`)

The Tracker maps directly onto the core state machine without modifying the core spine:

| Kit State | Tracker Role | Execution Behavior |
| :--- | :--- | :--- |
| **`DRAFTING`** | **Evaluate & Operator Match** | Deterministic root parsing, solution grading, and numerical error operator matching. |
| **`GATING`** | **Diagnosis & Decision** | Inspects student feedback from confirmations/working, logs classification, checks repeat counts. |
| **`PROBING`** | **Plan & Scaffold** | Selects remediation strategy (`plan.py`), picks unseen scaffold (`ladder.py`), generates guarded explanation. |
| **`AWAITING_EXPERT`** | **Human-in-the-Loop Pause** | Suspends execution waiting for the student to answer practice questions, confirm mistakes, or provide intermediate working. |
| **`COMPLETE`** | **Session Finished** | All questions completed; generates final progress summary and misconception matrix. |

---

## 4. The 5 Error Operators (`demo/tracker/schema.py`)

Every operator is an executable function calculating the exact wrong roots a student would obtain from equation coefficients:

| Operator ID | Method | Mathematical Rule / Bug Mechanics |
| :--- | :--- | :--- |
| `formula_sign_flip` | Formula | Uses $+b$ instead of $-b$ in $\frac{-b \pm \sqrt{b^2-4ac}}{2a}$. Both roots negated. |
| `factor_sign_flip` | Factoring | From $(x - p)(x - q) = 0$, writes roots as $-p, -q$. Negates both roots (collides with formula sign flip). |
| `formula_forgot_2a` | Formula | Divides by $a$ instead of $2a$ at final step. Both roots multiplied by 2. |
| `formula_discriminant_sign` | Formula | Evaluates discriminant as $b^2 + 4ac$ instead of $b^2 - 4ac$. |
| `factor_wrong_pair` | Factoring | Picks factor pairs whose sum equals $b$ but product does not equal $c$. |
| `unclassified` | Fallback | Used when no deterministic operator matches and student working is ambiguous. |

---

## 5. Architectural Tiers & Principles

### Tier 1: Durable State & Boundary Contracts
- **Immutable Event Log (`slice/store.py`)**: All state transitions, attempts, and classifications are stored in an append-only SQLite database with triggers preventing `UPDATE` and `DELETE`.
- **Typed Pydantic Schemas (`demo/tracker/schema.py`)**:
  - `ErrorClassification`: Structured model classification output (`error_type`, `confidence`, `reasoning`).
  - `IntermediateValue`: Extracted numbers from unstructured working steps (`discriminant`, `delta_line`, `p`, `q`).
  - `ReExplanation`: Bounded teaching explanations with verified pedagogical strategies.
- **Strict Attribution**: Every database record explicitly attributes its author (`code:evaluate`, `code:plan`, `agent:classify`, `agent:reexplain`, `student`).

### Tier 2: Anti-Hallucination & Anti-Leak Guards
- **Anti-Leak Filter (`demo/tracker/ladder.py:leaks_answer`)**: Before any generated explanation or scaffold is displayed to a student, code checks if the text contains the target equation's roots. If leaked, the explanation is suppressed.
- **Guaranteed Escape Hatches**: Control words (`skip`, `show me the answer`, `idk`) take top priority in all input handlers, immediately displaying verified step-by-step solutions without penalizing student flow.

### Tier 3: Adaptive Pedagogy & Longitudinal Memory
- **Remediation Strategy Planner (`demo/tracker/plan.py`)**: Selects among 4 distinct strategies (`worked_example`, `simpler_problem`, `alternate_method`, `real_world_example`). Avoids repeating ineffective strategies.
- **Repeat Limits & Escalation**:
  - `REPEATS_BEFORE_PAUSE = 3`: Maximum 3 identical errors before pausing for human choice.
  - `ESCALATION_ROUNDS = 2`: Maximum 2 scaffold rounds before offering the direct solution.
- **Student Profile & Memory (`demo/tracker/students.py`)**: Multi-session tracking using hashed PIN authentication. Re-folds historical misconception trends across past sessions.

---

## 6. Codebase File & Module Directory

```
agentic-slice-kit/
├── demo/
│   └── tracker/
│       ├── schema.py        # Question banks, error operators, Pydantic schemas
│       ├── flow.py          # State machine handlers (DRAFTING, GATING, PROBING)
│       ├── ladder.py        # Scaffolding tree, intermediate verification, leak check
│       ├── plan.py          # Remediation strategy planner (worked example, simpler problem)
│       ├── students.py      # Student persona simulators and multi-session auth
│       ├── custom.py        # Custom equation validator and consistency checker
│       ├── trail.py         # Per-turn learner trajectory & transition logger
│       ├── progress.py      # Mastery score calculator and error matrix aggregator
│       ├── stub.py          # Deterministic FakeCall mock for offline zero-cost runs
│       └── prompts/         # Jinja/Markdown prompt templates for LLM tasks
├── slice/
│   ├── store.py             # Append-only SQLite store with WAL mode & immutable triggers
│   ├── runner.py            # State machine execution loop with cycle detection
│   ├── callback.py          # Human-in-the-loop suspend/resume queue (ask/answer)
│   ├── budget.py            # Token budget fences and retry limiters
│   ├── llm.py               # OpenRouter / LLM wrapper with structured Pydantic parser
│   ├── config.py            # Centralized environment settings
│   └── records.py           # RunState enum and Version data contracts
├── web/
│   ├── student.py           # FastAPI student web interface with live step playback
│   ├── dashboard.py         # Real-time observer audit dashboard
│   └── ui.py                # Vanilla CSS styling, layout components, glassmorphism UI
├── scripts/
│   └── tracker.py           # CLI interactive runner for terminal sessions
└── tests/
    ├── test_smoke.py        # End-to-end agentic feedback loop & pause tests
    ├── test_tracker.py      # Full tracker flow and state machine transition tests
    ├── test_ladder.py       # Scaffolding logic, intermediate step parsing, leak detector tests
    ├── test_budget.py       # Spend limiter, token tracking, and restart survival tests
    ├── test_callback.py     # Suspend/resume callback queue and timeout sweep tests
    ├── test_runner.py       # State engine step-advance and error-boundary tests
    ├── test_store.py        # SQLite immutability, WAL mode, and sequence tests
    ├── test_web.py          # FastAPI web endpoints and authentication security tests
    └── test_integration.py  # Live LLM provider API tests (OpenRouter / Gemini / Claude)
```

---

## 7. Execution & Verification

### Running the Live Web Application:
```bash
export OPENROUTER_API_KEY=your-key
uvicorn web.student:app --port 8002
```
*Open [http://localhost:8002](http://localhost:8002) in your browser.*

### Running the Full Offline Test Suite:
```bash
python -m pytest --ignore=tests/test_integration.py -v
```
**Result**: 265 passed in ~21 seconds with 0 API tokens expended.
