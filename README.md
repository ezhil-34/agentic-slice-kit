# The Misconception Tracker
### An Agentic Intelligent Tutoring System for Quadratic Equations

[![Tests](https://img.shields.io/badge/tests-265%20passed-brightgreen.svg)]()
[![Deterministic Math](https://img.shields.io/badge/math%20grading-100%25%20code-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)]()

> **"Correctness and diagnosis must be deterministic math, never model opinion."**

The **Misconception Tracker** is an agentic intelligent tutoring system built on the `agentic-slice-kit` framework. It diagnoses, tracks, and remediates algebraic misconceptions in high school students solving quadratic equations.

Instead of trusting non-deterministic language models to grade arithmetic or guess student bugs, this system combines **pure-code deterministic grading and operator matching** with **targeted agentic narration** for pedagogically sound explanations.

---

##  Key Features

-  **100% Deterministic Math Verification**: Student roots and intermediate working steps are parsed and verified using exact math. LLMs are **never** asked whether a solution is mathematically correct.
-  **5 Deterministic Error Operators**: Mathematically detects exact bug patterns from equation coefficients ($a, b, c$):
  - `formula_sign_flip` ($+b$ instead of $-b$)
  - `factor_sign_flip` (sign error during factoring extraction)
  - `formula_forgot_2a` (dividing by $a$ instead of $2a$)
  - `formula_discriminant_sign` ($b^2 + 4ac$ instead of $b^2 - 4ac$)
  - `factor_wrong_pair` (invalid factor pair)
-  **Pedagogical Scaffolding Ladder**: Delivers adaptive warm-up questions and step-by-step guidance tailored to the diagnosed mistake.
-  **Anti-Hallucination & Anti-Leak Guards**: Explanation texts are verified via `leaks_answer()` before presentation to ensure the final roots are never prematurely exposed.
-  **Infinite-Loop Prevention & Human Choice**:
  - `REPEATS_BEFORE_PAUSE = 3`: After 3 consecutive identical mistakes, the agent pauses and presents a human choice modal (Worked Solution, Simpler Practice, or Alternate Method).
  - `ESCALATION_ROUNDS = 2`: Caps remediation rounds per problem.
  - Universal escape words (`skip`, `show me`, `idk`) work at every stage.
- **Longitudinal Multi-Session Memory**: Authenticates students with hashed PINs, persisting misconception frequencies across sessions into an append-only SQLite store.
- **Multi-Surface Delivery**:
  - **Student Web App**: Interactive practice interface with animated turn-by-turn agent thought playback.
  - **Live Auditor Dashboard**: Real-time observability into the state machine and event stream.
  - **CLI Interactive Terminal**: Terminal-based tutor for fast debugging.

---

##  System Architecture

The Misconception Tracker runs on a 4-state loop mapped directly onto `slice/records.py`:

```
                 ┌────────────────────────────────────────┐
                 │          Student Submits Answer        │
                 └───────────────────┬────────────────────┘
                                     │
                                     ▼
                           [ `DRAFTING` State ]
               Deterministic Root Evaluation (parse_roots)
               Numerical Operator Matching (match_operators)
                                     │
             ┌───────────────────────┴───────────────────────┐
             │                                               │
       [ 1 Match ]                                      [ 0 Matches ]
             │                                               │
             ▼                                               ▼
  Ask Confirmation Question                       Ask Intermediate Step
      (Phase: `confirm`)                       (Phase: `intermediate_step`)
             │                                               │
             └───────────────────────┬───────────────────────┘
                                     │
                                     ▼
                            [ `GATING` State ]
                Extract Intermediate Values / Diagnostic Agent
                   Record `classification` & `misconception`
                                     │
                     ┌───────────────┴───────────────┐
                     ▼                               ▼
             [ ≥ 3 Repeats or ]               [ Standard Case ]
             [ 2 Scaffolds    ]                      │
                     │                               ▼
                     ▼                      [ `PROBING` State ]
             Human Choice Modal              1. plan_strategy()
             (Show Solution,                 2. pick_scaffold()
              Simpler Problem)               3. ReExplanation (guarded)
                     │                               │
                     ▼                               ▼
             [ `AWAITING_EXPERT` ] ◀─────────────────┘
```

---

##  Project Structure

```
agentic-slice-kit/
├── demo/
│   └── tracker/
│       ├── schema.py        # Question bank, error operators, Pydantic data schemas
│       ├── flow.py          # State machine handlers (DRAFTING, GATING, PROBING)
│       ├── ladder.py        # Scaffolding tree, intermediate verification, leak guards
│       ├── plan.py          # Remediation strategy planner
│       ├── students.py      # Simulated student personas & multi-session PIN auth
│       ├── custom.py        # Custom equation validator & generator
│       ├── trail.py         # Per-turn learner trajectory logging
│       ├── progress.py      # Mastery score calculator & misconception matrix
│       ├── stub.py          # Deterministic FakeCall mock for offline zero-cost testing
│       └── prompts/         # Structured markdown prompt templates
├── slice/                   # Agentic Slice Kit Core Engine
│   ├── store.py             # Append-only SQLite database with immutable triggers
│   ├── runner.py            # State machine runner with step cycle detection
│   ├── callback.py          # Human-in-the-loop suspend/resume queue (ask/answer)
│   ├── budget.py            # Spend limits, retry allowances, and token tracking
│   ├── llm.py               # LLM client with structured Pydantic schema validation
│   ├── config.py            # Environment configuration (.env loader)
│   └── records.py           # Core RunState enum and Version envelopes
├── web/
│   ├── student.py           # FastAPI student web app with step animation
│   ├── dashboard.py         # Observer dashboard with real-time audit trail
│   └── ui.py                # Vanilla CSS styling, responsive glassmorphism UI
├── scripts/
│   └── tracker.py           # Interactive CLI runner
├── tests/                   # Full test suite (265 offline unit & integration tests)
├── ARCHITECTURE.md          # Full architectural specification
└── RUN.md                   # Single demo launch command
```

---

## 🚦 Quickstart & Running the Demo

### 1. Prerequisites
Python 3.10+ installed.

```bash
pip install -r requirements.txt
```

### 2. Configure API Key (Optional for live LLM mode)
Create a `.env` file in the project root:
```env
OPENROUTER_API_KEY=your-openrouter-api-key
```

### 3. Launch the Web Application
```bash
uvicorn web.student:app --port 8002
```
Open **[http://localhost:8002](http://localhost:8002)** in your browser.

### 4. (Optional) Launch the Observer Dashboard
In a second terminal:
```bash
uvicorn web.dashboard:app --port 8001
```
Open **[http://localhost:8001](http://localhost:8001)** to watch agent state transitions and database records land in real time.

### 5. Launch CLI Tracker
```bash
# Live mode (uses OpenRouter LLM):
python scripts/tracker.py run

# Offline zero-token stub mode:
python scripts/tracker.py run --stub
```

---

##  Testing

The repository contains **265 offline tests** that run locally in ~20 seconds with **zero network calls and zero token expenditure**:

```bash
python -m pytest --ignore=tests/test_integration.py -v
```

To run the live LLM integration tests:
```bash
python -m pytest tests/test_integration.py -v
```

---

##  License
MIT License.
