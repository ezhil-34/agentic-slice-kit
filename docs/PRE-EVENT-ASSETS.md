# Pre-Event Assets

## 1. Prior Code

- Forked starter repository:
  https://github.com/rsimhan/agentic-slice-kit

- Working repository:
  https://github.com/ezhil-34/agentic-slice-kit

- Existing starter framework:
  - State-machine orchestration
  - SQLite persistence
  - OpenRouter LLM integration
  - Budget and retry management
  - Automated tests

## 2. Prior Prompts / Agent Definitions

- Existing starter demo prompts and workflow
  are being used as a reference.
- No production agent was developed specifically
  for this Agent-a-thon before the event.

## 3. Evaluation Sets

- No project-specific evaluation dataset was
  prepared before the event.

## 4. Datasets and External Assets

- No new dataset was gathered before the event.

## 5. Libraries and Tools

 -pydantic>=2.7          # typed contracts at every boundary
 -httpx>=0.27            # model calls
 -sqlite-vec>=0.1.9      # vector search inside the run database
 -fastembed>=0.4         # local embeddings - no API key, no rate limit
 -fastapi>=0.115         # the expert callback form, and web/student.py
 -uvicorn>=0.30          # serves it
 -python-multipart>=0.0.9  # web/student.py's login/answer forms need this for FastAPI Form()



## 6. Event Work

  Today, we are building a Personalized Quadratic Equation Tutor Agent for Class 10 CBSE students. The agent takes the student's typed answer, analyzes the mistake, and maintains a persistent misconception history across sessions. Instead of identifying a misconception from a single wrong answer, it detects repeated error patterns, such as consistently using b² + 4ac instead of b² − 4ac. When a repeated mistake is detected, the agent adapts its teaching by re-explaining the specific concept using a different approach and giving another question to verify improvement.
 
