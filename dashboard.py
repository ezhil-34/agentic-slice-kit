"""
A window into the tracker while it runs.

The point of this page is the thing the judging criteria actually ask for:
showing *which agent did what, when*, not just the final answer. It reads
tracker.db - the same append-only store scripts/tracker.py writes to - and
never writes to it itself. Run a session in one terminal, open this in a
browser, and watch the records land as they're appended.

    uvicorn web.dashboard:app --reload --port 8001

Then run a session in another terminal against the SAME --db file:

    python scripts/tracker.py --db tracker.db run --stub

and open http://localhost:8001/run/<run_id> - the run_id is printed at the
top of the tracker.py output.

Server-rendered on first load; a small poll (fetch, every 1.2s) keeps it
live without a full-page reload. No framework, no build step - matches
web/expert.py's own approach, with just enough JS added because "watch it
think while it happens" is the actual ask, and that needs a moving picture.
"""
from __future__ import annotations

import html
import os
import time
from collections import defaultdict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from slice.store import Store

DB = os.environ.get("TRACKER_DB", "tracker.db")
app = FastAPI(title="Tracker dashboard")

# Which handler produced a record, and how to show it. Matches flow.py's
# produced_by tags exactly - see demo/tracker/README.md, "The four agents".
AGENT_LABELS = {
    "code:evaluate":        ("EVALUATE",   "code"),
    "agent:classify":       ("CLASSIFY",   "agent"),
    "code:log_and_decide":  ("LOG & DECIDE", "code"),
    "agent:reexplain":      ("REEXPLAIN",  "agent"),
    "system":               ("SYSTEM",     "sys"),
    "student":              ("STUDENT",    "human"),
    "expert":               ("STUDENT",    "human"),   # callback.answer()'s default `who`
    "system:timeout":       ("TIMEOUT",    "sys"),
    "runner":                ("RUNNER",     "sys"),
}

ERROR_LABELS = {
    "sign_error": "Sign errors",
    "factoring_error": "Factoring",
    "arithmetic_slip": "Arithmetic slips",
    "unclassified": "Unclassified",
}


def _store() -> Store:
    return Store(DB)


def _summarize(run_id: str) -> dict:
    """Everything the page needs, computed fresh from the store every call.
    No caching - the store is the only truth (see slice/store.py's own
    docstring), so re-deriving this each time can never go stale."""
    s = _store()
    records = s.replay(run_id)
    run_state = s.get_state(run_id).value
    tokens = int(s.counter(run_id, "tokens"))

    # Learner memory: current occurrence count per error type, from the
    # latest "misconception" record of each type - not a running total of
    # every classify call, since occurrences already resets per question in
    # flow.py's own counting logic (see handle_gating).
    memory: dict[str, int] = defaultdict(int)
    for v in records:
        if v.kind == "misconception":
            memory[v.payload["error_type"]] = max(
                memory[v.payload["error_type"]], v.payload["occurrences"])

    attempts = [v for v in records if v.kind == "attempt"]
    correct = sum(1 for a in attempts if a.payload["correct"])
    mastery = round(100 * correct / len(attempts)) if attempts else None

    latest_misconception = next(
        (v for v in reversed(records) if v.kind == "misconception"), None)
    recurring = None
    if latest_misconception and latest_misconception.payload["occurrences"] >= 2:
        recurring = {
            "error_type": latest_misconception.payload["error_type"],
            "previous": latest_misconception.payload["occurrences"] - 1,
            "current": latest_misconception.payload["occurrences"],
        }

    feed = []
    for v in records:
        label, kind = AGENT_LABELS.get(v.produced_by, (v.produced_by.upper(), "sys"))
        feed.append({
            "seq": v.seq, "kind": v.kind, "label": label, "css": kind,
            "age": round(time.time() - v.created_at, 1),
            "payload": v.payload,
        })

    return {
        "run_id": run_id, "state": run_state, "tokens": tokens,
        "memory": dict(memory), "mastery": mastery,
        "questions_attempted": len({a.payload["question_id"] for a in attempts}),
        "recurring": recurring, "feed": feed,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    s = _store()
    runs = s.list_runs(limit=20)
    if not runs:
        return _page("No runs yet",
                     "<h1>No runs yet</h1><p class='sub'>Start one with "
                     "<code>python scripts/tracker.py --db tracker.db run --stub</code></p>")
    rows = "".join(
        f"<div class='card'><a href='/run/{r['id']}'>{r['id']}</a>"
        f"<span class='tag {r['state']}'>{r['state']}</span></div>"
        for r in runs)
    return _page("Runs", f"<h1>{len(runs)} run(s)</h1>{rows}")


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str):
    return _page(f"Run {run_id}", _BODY_TEMPLATE.replace("__RUN_ID__", run_id))


@app.get("/api/run/{run_id}")
def run_state(run_id: str):
    return JSONResponse(_summarize(run_id))


# ------------------------------------------------------------------- markup

PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{color-scheme:light dark;
  --code:#6b7280; --agent:#7c3aed; --sys:#0d9488; --human:#2563eb;
  --warn:#d97706; --ok:#16a34a; --bad:#dc2626;}}
*{{box-sizing:border-box}}
body{{font:15px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  max-width:46rem;margin:0 auto;padding:2rem 1.2rem 4rem}}
h1{{font:600 1.25rem/1.3 system-ui,sans-serif;margin:0 0 .3rem}}
.sub{{color:#6b7280;font-size:.85rem;margin:0 0 1.5rem;font-family:system-ui,sans-serif}}
a{{color:var(--human)}}
.card{{border:1px solid #d4d4d8;border-radius:8px;padding:.8rem 1rem;margin:0 0 .6rem;
  display:flex;justify-content:space-between;align-items:center}}
.tag{{font-size:.7rem;padding:.15rem .5rem;border-radius:99px;font-family:system-ui,sans-serif;
  text-transform:uppercase;letter-spacing:.04em}}
.tag.complete{{background:rgba(22,163,74,.15);color:var(--ok)}}
.tag.awaiting_expert{{background:rgba(37,99,235,.15);color:var(--human)}}
.tag.failed{{background:rgba(220,38,38,.15);color:var(--bad)}}

.panel{{border:1px dashed #a1a1aa;border-radius:10px;padding:1rem 1.2rem;margin:0 0 1.2rem;
  background:rgba(127,127,127,.06)}}
.panel h2{{font:600 .78rem/1 system-ui,sans-serif;letter-spacing:.08em;text-transform:uppercase;
  color:#6b7280;margin:0 0 .8rem}}
.bar-row{{display:flex;align-items:center;gap:.6rem;margin:.3rem 0;font-size:.85rem}}
.bar-label{{width:9rem;flex-shrink:0;font-family:system-ui,sans-serif}}
.bar-track{{flex:1;background:rgba(127,127,127,.15);border-radius:4px;height:10px;overflow:hidden}}
.bar-fill{{height:100%;background:var(--warn);border-radius:4px}}
.bar-count{{width:1.4rem;text-align:right;color:#6b7280}}
.stats{{display:flex;gap:1.6rem;margin-top:.9rem;font-family:system-ui,sans-serif;font-size:.85rem}}
.stats b{{display:block;font-size:1.3rem;font-family:ui-monospace,monospace}}

.alert{{border:1px solid var(--warn);background:rgba(217,119,6,.09);border-radius:10px;
  padding:.9rem 1.1rem;margin:0 0 1.2rem;display:none}}
.alert.show{{display:block}}
.alert .head{{color:var(--warn);font-weight:700;font-family:system-ui,sans-serif;
  font-size:.8rem;letter-spacing:.04em;margin-bottom:.5rem}}
.alert .arrow{{color:var(--warn);margin-top:.5rem}}

.feed{{border-left:2px solid #d4d4d8;padding-left:1rem}}
.entry{{margin:0 0 1rem;position:relative}}
.entry::before{{content:'';position:absolute;left:-1.31rem;top:.4rem;width:8px;height:8px;
  border-radius:50%;background:currentColor}}
.entry .who{{font-weight:700;font-size:.72rem;letter-spacing:.05em}}
.entry.code .who{{color:var(--code)}}
.entry.agent .who{{color:var(--agent)}}
.entry.sys .who{{color:var(--sys)}}
.entry.human .who{{color:var(--human)}}
.entry .age{{color:#9ca3af;font-size:.7rem;margin-left:.5rem;font-family:system-ui,sans-serif}}
.entry pre{{margin:.3rem 0 0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:.78rem;
  background:rgba(127,127,127,.07);border-radius:6px;padding:.5rem .7rem}}
.empty{{color:#9ca3af;font-family:system-ui,sans-serif}}
</style>
{body}"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=html.escape(title), body=body))


_BODY_TEMPLATE = """
<h1>Run __RUN_ID__</h1>
<p class="sub" id="meta">loading…</p>

<div class="panel">
  <h2>Learner memory</h2>
  <div id="bars"><p class="empty">no misconceptions logged yet</p></div>
  <div class="stats">
    <div><b id="mastery">–</b>mastery</div>
    <div><b id="qcount">–</b>questions seen</div>
    <div><b id="tokens">–</b>tokens</div>
  </div>
</div>

<div class="alert" id="alert">
  <div class="head">&#9888; RECURRING MISCONCEPTION</div>
  <div id="alert-body"></div>
</div>

<div class="feed" id="feed"><p class="empty">waiting for the first record…</p></div>

<script>
const runId = "__RUN_ID__";
function esc(s){ return (s+"").replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

async function tick() {
  const r = await fetch(`/api/run/${runId}`);
  if (!r.ok) return;
  const d = await r.json();

  document.getElementById('meta').innerHTML =
    `state: <b>${esc(d.state)}</b>`;

  const bars = document.getElementById('bars');
  const labels = {sign_error:"Sign errors", factoring_error:"Factoring",
                  arithmetic_slip:"Arithmetic slips", unclassified:"Unclassified"};
  const entries = Object.entries(d.memory);
  bars.innerHTML = entries.length ? entries.map(([k,v]) => `
    <div class="bar-row">
      <span class="bar-label">${labels[k] || k}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.min(100,v*20)}%"></span></span>
      <span class="bar-count">${v}</span>
    </div>`).join('') : '<p class="empty">no misconceptions logged yet</p>';

  document.getElementById('mastery').textContent = d.mastery === null ? '–' : d.mastery + '%';
  document.getElementById('qcount').textContent = d.questions_attempted;
  document.getElementById('tokens').textContent = d.tokens;

  const alertBox = document.getElementById('alert');
  if (d.recurring) {
    alertBox.classList.add('show');
    document.getElementById('alert-body').innerHTML = `
      ${labels[d.recurring.error_type] || d.recurring.error_type} detected.<br>
      Previous occurrences: ${d.recurring.previous}<br>
      Current occurrence: ${d.recurring.current}
      <div class="arrow">&rarr; switching teaching strategy</div>`;
  } else {
    alertBox.classList.remove('show');
  }

  const feed = document.getElementById('feed');
  feed.innerHTML = d.feed.slice().reverse().map(f => `
    <div class="entry ${f.css}">
      <span class="who">${esc(f.label)}</span>
      <span class="age">${f.age}s ago · ${esc(f.kind)}</span>
      <pre>${esc(JSON.stringify(f.payload, null, 2))}</pre>
    </div>`).join('') || '<p class="empty">waiting for the first record…</p>';
}

tick();
setInterval(tick, 1200);
</script>
"""
