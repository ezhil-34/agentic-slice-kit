"""
The look of the student app: one stylesheet, the page shell, and the few
components more than one page uses. No framework, no build step, no external
requests (fonts, icons and the favicon are all inline) - it works offline and
nothing can fail to load during a demo.

Colour means the same thing on every page (see demo/tracker/progress.py):
green = right first time / fully corrected, orange = needed retries or kept
coming back, red = still unresolved, blue = where you are now, purple = the
tutor (AI), teal = the planner.
"""
from __future__ import annotations

import html
import math

_FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E"
            "%3Crect width='32' height='32' rx='8' fill='%232563eb'/%3E"
            "%3Cpath d='M9 22l7-12 7 12z' fill='none' stroke='white' stroke-width='2.4' "
            "stroke-linejoin='round'/%3E%3C/svg%3E")

CSS = """
:root{color-scheme:light dark;
 --bg:#f4f6fb;--card:#fff;--ink:#0f172a;--muted:#5b6b82;--line:#e3e8f0;--soft:#f1f5f9;
 --green:#15803d;--green-bg:#dcfce7;--orange:#b45309;--orange-bg:#ffedd5;
 --red:#b91c1c;--red-bg:#fee2e2;--blue:#1d4ed8;--blue-bg:#dbeafe;
 --purple:#6d28d9;--purple-bg:#ede9fe;--teal:#0f766e;--teal-bg:#ccfbf1;--gray:#64748b;
 --btn:#2563eb;--btn-ink:#fff;
 --shadow:0 1px 2px rgba(15,23,42,.06),0 10px 28px rgba(15,23,42,.07);--r:14px}
@media (prefers-color-scheme:dark){:root{
 --bg:#0a0f1e;--card:#121a30;--ink:#e8edf8;--muted:#9aa8c2;--line:#24304a;--soft:#18213a;
 --green:#4ade80;--green-bg:#0f2e1c;--orange:#fbbf24;--orange-bg:#33230a;
 --red:#f87171;--red-bg:#3a1414;--blue:#8fb0ff;--blue-bg:#15254d;
 --purple:#b9a3ff;--purple-bg:#241a4a;--teal:#5eead4;--teal-bg:#0d2f2c;--gray:#94a3b8;
 --btn:#3b6cf0;
 --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 28px rgba(0,0,0,.35)}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
 font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--blue);text-underline-offset:2px}
h1{font-size:1.45rem;line-height:1.25;margin:0 0 .35rem;letter-spacing:-.01em}
h2{font-size:1.02rem;margin:0 0 .6rem;letter-spacing:-.005em}
p{margin:0 0 .8rem}
code{background:var(--soft);padding:.1rem .4rem;border-radius:6px;font-size:.9em;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
:focus-visible{outline:3px solid var(--blue);outline-offset:2px;border-radius:6px}
[hidden]{display:none!important}
.muted{color:var(--muted)}
.small{font-size:.85rem}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}

/* ---------- header */
.top{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--card) 88%,transparent);
 backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
.top-in{max-width:58rem;margin:0 auto;padding:.6rem 1rem;display:flex;align-items:center;gap:1rem;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:.55rem;font-weight:700;color:var(--ink);text-decoration:none;letter-spacing:-.01em}
.brand-mark{width:1.7rem;height:1.7rem;border-radius:8px;background:var(--btn);display:grid;place-items:center}
.nav{display:flex;gap:.2rem;margin-left:.4rem;flex-wrap:wrap}
.nav a{padding:.4rem .75rem;border-radius:9px;text-decoration:none;color:var(--muted);font-weight:600;font-size:.92rem}
.nav a:hover{background:var(--soft);color:var(--ink)}
.nav a.on{background:var(--blue-bg);color:var(--blue)}
.who{margin-left:auto;display:flex;align-items:center;gap:.6rem;font-size:.9rem;color:var(--muted)}
.who form{margin:0}
.link-btn{background:none;border:1px solid var(--line);color:var(--muted);padding:.3rem .7rem;border-radius:9px;
 font:600 .82rem system-ui,sans-serif;cursor:pointer}
.link-btn:hover{color:var(--ink);border-color:var(--muted)}

/* ---------- layout */
.wrap{max-width:46rem;margin:0 auto;padding:1.4rem 1rem 4rem}
.wrap.wide{max-width:58rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);
 padding:1.4rem 1.5rem;margin:0 0 1rem}
.card.tight{padding:1rem 1.2rem}
.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.between{justify-content:space-between}

/* ---------- controls */
input[type=text],input[type=password]{width:100%;font:inherit;padding:.8rem 1rem;border-radius:11px;
 border:1.5px solid var(--line);background:var(--card);color:var(--ink)}
input::placeholder{color:var(--muted);opacity:.8}
input:focus{border-color:var(--blue);outline:none;box-shadow:0 0 0 4px var(--blue-bg)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.5rem;font:600 .95rem system-ui,sans-serif;
 padding:.8rem 1.3rem;border-radius:11px;border:0;background:var(--btn);color:var(--btn-ink);cursor:pointer;
 text-decoration:none;transition:transform .08s,filter .15s}
.btn:hover{filter:brightness(1.08)}
.btn:active{transform:translateY(1px)}
.btn[disabled]{opacity:.55;cursor:not-allowed}
.btn.ghost{background:transparent;color:var(--blue);border:1.5px solid var(--line)}
.btn.ghost:hover{background:var(--soft)}
.answer{display:flex;gap:.6rem;margin-top:1rem}
.answer input{flex:1}
.field-error{color:var(--red);font-size:.88rem;margin:.5rem 0 0;min-height:1.2em}

/* ---------- banners */
.banner{border-radius:11px;padding:.65rem .9rem;font-size:.92rem;margin:0 0 1rem;display:flex;gap:.55rem;align-items:flex-start}
.banner.ok{background:var(--green-bg);color:var(--green)}
.banner.info{background:var(--blue-bg);color:var(--blue)}
.banner.warn{background:var(--orange-bg);color:var(--orange)}
.banner.err{background:var(--red-bg);color:var(--red)}

/* ---------- question tracker */
.stepper{margin:0 0 1rem}
.stepper ol{list-style:none;display:flex;gap:.35rem;padding:0;margin:0}
.tile{flex:1;min-width:0;height:2.1rem;border-radius:10px;display:grid;place-items:center;font:700 .82rem system-ui,sans-serif;
 background:var(--soft);color:var(--muted);border:1.5px solid transparent}
.tile.green{background:var(--green-bg);color:var(--green);border-color:var(--green)}
.tile.orange{background:var(--orange-bg);color:var(--orange);border-color:var(--orange)}
.tile.current{background:var(--blue-bg);color:var(--blue);border-color:var(--blue);box-shadow:0 0 0 3px var(--blue-bg)}
.tile.current.retry{border-color:var(--orange);color:var(--orange);background:var(--orange-bg);box-shadow:0 0 0 3px var(--orange-bg)}
.legend{display:flex;gap:1rem;flex-wrap:wrap;margin:.55rem 0 0;font-size:.78rem;color:var(--muted)}
.legend i{display:inline-block;width:.7rem;height:.7rem;border-radius:4px;margin-right:.35rem;vertical-align:-1px;border:1.5px solid}
.legend .green{background:var(--green-bg);border-color:var(--green)}
.legend .orange{background:var(--orange-bg);border-color:var(--orange)}
.legend .blue{background:var(--blue-bg);border-color:var(--blue)}
.legend .todo{background:var(--soft);border-color:var(--line)}

/* ---------- the question card */
.q-meta{display:flex;justify-content:space-between;align-items:center;margin:0 0 .8rem;gap:.6rem;flex-wrap:wrap}
.q-num{font:700 .8rem system-ui,sans-serif;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.pill{display:inline-block;padding:.18rem .65rem;border-radius:99px;font:700 .75rem system-ui,sans-serif;background:var(--soft);color:var(--muted)}
.pill.orange{background:var(--orange-bg);color:var(--orange)}
.pill.green{background:var(--green-bg);color:var(--green)}
.pill.red{background:var(--red-bg);color:var(--red)}
.pill.blue{background:var(--blue-bg);color:var(--blue)}
.equation{font:600 clamp(1.6rem,5.5vw,2.3rem)/1.3 "Cambria Math","STIX Two Math",Georgia,serif;text-align:center;
 padding:1.1rem .6rem;background:var(--soft);border-radius:12px;letter-spacing:.02em;margin:0 0 .7rem;overflow-wrap:anywhere}
.ask{font-size:1.15rem;font-weight:600;margin:.3rem 0 .8rem}
.hint{color:var(--muted);font-size:.88rem;text-align:center;margin:0 0 1rem}

.attempts{list-style:none;padding:0;margin:0 0 1rem;display:grid;gap:.45rem}
.attempt{display:flex;flex-wrap:wrap;gap:.35rem .7rem;align-items:center;padding:.6rem .85rem;border-radius:11px;font-size:.92rem;
 background:var(--red-bg);color:var(--ink);border:1px solid color-mix(in srgb,var(--red) 30%,transparent)}
.attempt.ok{background:var(--green-bg);border-color:color-mix(in srgb,var(--green) 30%,transparent)}
.attempt .a-num{font:700 .72rem system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--red)}
.attempt.ok .a-num{color:var(--green)}
.attempt .a-why{color:var(--muted);margin-left:auto}
.hist-title{font:700 .78rem system-ui,sans-serif;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin:1rem 0 .5rem}

.tutor{border-left:4px solid var(--purple);background:var(--purple-bg);padding:.9rem 1.1rem;border-radius:0 12px 12px 0;margin:0 0 1rem}
.tutor .who-t{display:flex;gap:.5rem;align-items:center;margin:0 0 .35rem;font:700 .75rem system-ui,sans-serif;
 letter-spacing:.06em;text-transform:uppercase;color:var(--purple)}
.tutor p{margin:0 0 .4rem}
.tutor .why{color:var(--muted);font-size:.85rem;font-style:italic;margin:.3rem 0 0}

.choices{display:grid;grid-template-columns:1fr 1fr;gap:.7rem;margin-top:.9rem}
.choice{text-align:left;padding:1rem;border-radius:12px;border:1.5px solid var(--line);background:var(--card);color:var(--ink);
 cursor:pointer;font:inherit;transition:border-color .15s,background .15s,transform .08s}
.choice:hover{border-color:var(--blue);background:var(--blue-bg)}
.choice:active{transform:translateY(1px)}
.choice b{display:block;margin-bottom:.15rem}
.choice span{color:var(--muted);font-size:.86rem}
.choices.three{grid-template-columns:repeat(3,1fr)}

.said{display:flex;gap:.6rem;align-items:center;padding:.7rem .9rem;border-radius:11px;background:var(--blue-bg);color:var(--blue);margin-top:1rem}

/* ---------- agent panel */
.agents{padding:1.1rem 1.3rem}
.agents-head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;flex-wrap:wrap;margin:0 0 .3rem}
.agents-head h2{margin:0}
.agent-key{display:flex;gap:.9rem;flex-wrap:wrap;font-size:.75rem;color:var(--muted)}
.agent-key b{font-weight:700}
.trail{list-style:none;margin:.5rem 0 0;padding:0}
.trail-row{display:flex;gap:.8rem;align-items:baseline;padding:.55rem 0;border-top:1px solid var(--line);animation:rise .35s ease-out}
.trail.playing .trail-row{opacity:.55}
.trail.playing .trail-row.now{opacity:1}
.trail-agent{flex:0 0 6.6rem;font:700 .7rem system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase}
.trail-agent small{display:block;font:600 .68rem system-ui,sans-serif;letter-spacing:0;text-transform:none;color:var(--muted)}
.a-Evaluator{color:var(--gray)}.a-Diagnoser{color:var(--purple)}.a-Planner{color:var(--teal)}.a-Tutor{color:var(--blue)}.a-Runner{color:var(--red)}
.trail-doing{font-size:.93rem}
.trail-row.live .trail-doing::after{content:"";display:inline-block;width:1.3em;animation:dots 1.2s steps(4,end) infinite}
.spin{width:1rem;height:1rem;border-radius:50%;border:2.5px solid var(--line);border-top-color:var(--purple);
 animation:spin .8s linear infinite;display:inline-block;vertical-align:-3px;margin-right:.4rem}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes dots{0%{content:""}25%{content:"."}50%{content:".."}75%{content:"..."}}
@keyframes rise{from{opacity:0;transform:translateY(6px)}}

/* ---------- progress page */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.8rem;margin:0 0 1rem}
.stat{background:var(--card);border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--shadow);padding:1rem 1.1rem;display:flex;gap:.9rem;align-items:center}
.stat .n{font:800 2rem/1 system-ui,sans-serif;letter-spacing:-.02em}
.stat .l{font-size:.82rem;color:var(--muted);line-height:1.3}
.stat .n.green{color:var(--green)}.stat .n.orange{color:var(--orange)}.stat .n.red{color:var(--red)}.stat .n.blue{color:var(--blue)}
.ring text{font:800 22px system-ui,sans-serif;fill:var(--ink)}
.ring .track{stroke:var(--line)}
.grid10{display:grid;grid-template-columns:repeat(10,1fr);gap:.4rem}
.grid10 .tile{height:2.6rem}
.bug{border-left:5px solid var(--gray);padding-left:1.1rem}
.bug.green{border-left-color:var(--green)}.bug.orange{border-left-color:var(--orange)}.bug.red{border-left-color:var(--red)}
.bug h3{margin:0;font-size:1rem}
.bug .what{margin:.4rem 0 .6rem;color:var(--ink)}
.bug .tip{background:var(--soft);border-radius:10px;padding:.55rem .8rem;font-size:.88rem;margin:.6rem 0 0}
.meter{height:.6rem;background:var(--soft);border-radius:99px;overflow:hidden;margin:.55rem 0 .3rem;display:flex}
.meter i{display:block;height:100%}
.meter .ok{background:var(--green)}.meter .open{background:var(--red)}
.facts{display:flex;gap:.5rem 1.1rem;flex-wrap:wrap;font-size:.86rem;color:var(--muted)}
.facts b{color:var(--ink)}
.sess{display:flex;gap:.8rem;align-items:center;justify-content:space-between;flex-wrap:wrap;padding:.65rem 0;border-top:1px solid var(--line);font-size:.92rem}
.sess:first-of-type{border-top:0}
.empty{text-align:center;padding:1.6rem 1rem;color:var(--muted)}
.empty b{display:block;color:var(--ink);font-size:1.05rem;margin-bottom:.25rem}

/* ---------- login / custom / misc */
.center{max-width:26rem;margin:6vh auto 0}
.login-hero{text-align:center;margin:0 0 1.4rem}
.login-hero .brand-mark{width:3rem;height:3rem;margin:0 auto .8rem;border-radius:14px}
.stack{display:grid;gap:.7rem}
.chips{display:flex;gap:.45rem;flex-wrap:wrap;margin:.3rem 0 .9rem}
.chip{border:1px solid var(--line);background:var(--soft);color:var(--ink);border-radius:99px;padding:.3rem .8rem;cursor:pointer;
 font:600 .82rem system-ui,sans-serif}
.chip:hover{border-color:var(--blue);color:var(--blue)}
.verdict{border-radius:12px;padding:1rem 1.1rem;margin:0 0 1rem;border:1px solid}
.verdict.ok{background:var(--green-bg);border-color:var(--green)}
.verdict.bad{background:var(--red-bg);border-color:var(--red)}
.verdict h3{margin:0 0 .3rem;font-size:1.05rem}
.big-check{font-size:2.6rem;line-height:1;margin:0 0 .4rem}
.foot{text-align:center;color:var(--muted);font-size:.8rem;margin-top:2rem}

@media (max-width:34rem){
 .wrap{padding:1rem .75rem 3rem}.card{padding:1.1rem 1rem}
 .answer{flex-direction:column}.choices,.choices.three{grid-template-columns:1fr}
 .grid10{grid-template-columns:repeat(5,1fr)}.trail-agent{flex-basis:5.4rem}
 .attempt .a-why{margin-left:0;flex-basis:100%}.who{margin-left:0;width:100%;justify-content:space-between}
 .tile{height:1.8rem;font-size:.72rem;border-radius:8px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# What each agent is, shown as a small badge so it is obvious which parts are
# plain code and which are a model.
AGENTS = {
    "Evaluator": "code",
    "Diagnoser": "code + AI",
    "Planner": "code",
    "Tutor": "AI",
    "Runner": "system",
}


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def shell(title: str, body: str, *, student: str | None = None, active: str = "",
          script: str = "", main_attrs: str = "", wide: bool = False) -> str:
    nav = ""
    if student:
        def link(href, key, text):
            return f'<a href="{href}"{" class=on aria-current=page" if active == key else ""}>{text}</a>'
        nav = f"""
        <nav class="nav" aria-label="Main">
          {link("/resume", "practice", "Practice")}
          {link("/profile", "progress", "My progress")}
          {link("/custom", "custom", "Your own equation")}
        </nav>
        <div class="who"><span>{esc(student)}</span>
          <form method="post" action="/logout"><button class="link-btn" type="submit">Log out</button></form>
        </div>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Misconception Tracker</title>
<link rel="icon" href="{_FAVICON}">
<style>{CSS}</style></head>
<body>
<header class="top"><div class="top-in">
  <a class="brand" href="/"><span class="brand-mark"><svg width="16" height="16" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 22l7-12 7 12z" fill="none" stroke="#fff" stroke-width="3" stroke-linejoin="round"/></svg></span>Misconception Tracker</a>
  {nav}
</div></header>
<main class="wrap{" wide" if wide else ""}" id="app" {main_attrs}>
{body}
</main>
{script}
</body></html>"""


def stepper(tiles: list[str], current: int | None, retry: bool = False) -> str:
    """tiles: one of green / orange / current / todo per question."""
    items = []
    for i, t in enumerate(tiles):
        label = {"green": f"Question {i+1}: solved first try",
                 "orange": f"Question {i+1}: solved after retries",
                 "current": f"Question {i+1}: you are here",
                 "todo": f"Question {i+1}: not yet"}[t]
        mark = "✓" if t == "green" else str(i + 1)
        cls = t + (" retry" if t == "current" and retry else "")
        items.append(f'<li class="tile {cls}" title="{esc(label)}"><span aria-hidden="true">{mark}</span>'
                     f'<span class="sr-only">{esc(label)}</span></li>')
    return f"""<nav class="stepper" aria-label="Question progress"><ol>{"".join(items)}</ol>
      <p class="legend"><span><i class="green"></i>First try</span><span><i class="orange"></i>Needed retries</span>
      <span><i class="blue"></i>You are here</span><span><i class="todo"></i>To do</span></p></nav>"""


def tone(pct: int | None) -> str:
    if pct is None:
        return "blue"
    return "green" if pct >= 70 else "orange" if pct >= 40 else "red"


def ring(pct: int | None, size: int = 84) -> str:
    """A progress ring. `None` draws an empty one with a dash."""
    r, c = 40, 2 * math.pi * 40
    filled = c * (pct or 0) / 100
    colour = {"green": "var(--green)", "orange": "var(--orange)", "red": "var(--red)",
              "blue": "var(--muted)"}[tone(pct)]
    label = f"{pct}%" if pct is not None else "–"
    return (f'<svg class="ring" width="{size}" height="{size}" viewBox="0 0 100 100" role="img" '
            f'aria-label="mastery {label}"><circle class="track" cx="50" cy="50" r="{r}" fill="none" stroke-width="10"/>'
            f'<circle cx="50" cy="50" r="{r}" fill="none" stroke="{colour}" stroke-width="10" stroke-linecap="round" '
            f'stroke-dasharray="{filled:.1f} {c:.1f}" transform="rotate(-90 50 50)"/>'
            f'<text x="50" y="58" text-anchor="middle">{label}</text></svg>')


def agent_key() -> str:
    return '<div class="agent-key">' + "".join(
        f'<span><b class="a-{n}">{n}</b> · {k}</span>' for n, k in AGENTS.items() if n != "Runner") + "</div>"


def trail_rows(steps) -> str:
    return "".join(
        f'<li class="trail-row"><span class="trail-agent a-{esc(s.agent)}">{esc(s.agent)}'
        f'<small>{esc(AGENTS.get(s.agent, ""))}</small></span><span class="trail-doing">{esc(s.doing)}</span></li>'
        for s in steps)


# One script for the practice page. It does two jobs with the same code:
#  - after an answer is submitted (fetch), and
#  - when the page is loaded mid-turn (?play=1, or a run still in flight),
# it polls /status and plays the agents' steps one at a time, each held for at
# least STEP_MS even if the work behind it took a millisecond, then loads the
# next state of the page. Without JavaScript the form still posts normally.
SESSION_JS = """
<script>
(function () {
  const app = document.getElementById('app');
  const run = app.dataset.run;
  if (!run) return;
  const STEP_MS = +app.dataset.stepMs || 1400;
  const phase = app.dataset.phase || '';
  const panel = document.getElementById('agents');
  const list = document.getElementById('trail');
  const title = document.getElementById('agents-title');
  const form = document.getElementById('answer-form');
  const said = document.getElementById('said');
  const err = document.getElementById('field-error');
  const AGENTS = JSON.parse(app.dataset.agents || '{}');
  const NEXT = '/session/' + encodeURIComponent(run);
  let received = 0, queue = [], working = true, active = null, busy = false, live = null, started = false;

  function makeRow(agent, doing) {
    const li = document.createElement('li'); li.className = 'trail-row';
    const a = document.createElement('span'); a.className = 'trail-agent a-' + agent; a.textContent = agent;
    const k = document.createElement('small'); k.textContent = AGENTS[agent] || ''; a.appendChild(k);
    const d = document.createElement('span'); d.className = 'trail-doing'; d.textContent = doing;
    li.append(a, d); return li;
  }
  function dropLive() { if (live) { live.remove(); live = null; } }
  function pump() {
    if (busy) return;
    if (queue.length) {
      dropLive();
      list.querySelectorAll('.now').forEach(e => e.classList.remove('now'));
      const s = queue.shift(), li = makeRow(s.agent, s.doing);
      li.classList.add('now'); list.appendChild(li);
      busy = true; setTimeout(() => { busy = false; pump(); }, STEP_MS);
      return;
    }
    if (!working) { location.replace(NEXT); return; }
    if (active) {
      dropLive(); live = makeRow(active.agent, active.doing);
      live.classList.add('now', 'live'); list.appendChild(live);
    }
  }
  async function poll() {
    try {
      const r = await fetch(NEXT + '/status', {headers: {'Accept': 'application/json'}});
      if (r.status === 401 || r.status === 404) { location.replace('/'); return; }
      const d = await r.json();
      working = d.working; active = d.active;
      while (received < d.steps.length) queue.push(d.steps[received++]);
      pump();
    } catch (e) { /* server busy or restarting - try again */ }
    if (working) setTimeout(poll, 600);
  }
  function start() {
    if (started) return; started = true;
    panel.hidden = false; list.innerHTML = ''; list.classList.add('playing');
    title.innerHTML = '<span class="spin"></span>Agents at work';
    poll();
  }
  if (app.dataset.play === '1') start();

  if (form) {
    const input = form.querySelector('input[name=answer]');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const val = input ? input.value.trim() : (e.submitter ? e.submitter.value : '');
      if (input) {
        if (!val) { err.textContent = 'Type an answer first.'; input.focus(); return; }
        if (phase === 'practice' && !/\\d/.test(val)) {
          err.textContent = 'Enter your answers as numbers, e.g. 2 and 3 (fractions like 1/2 are fine).';
          input.focus(); return;
        }
      }
      err.textContent = '';
      const body = new FormData(form, e.submitter);      // read BEFORE disabling: disabled fields aren't sent
      form.querySelectorAll('input,button').forEach(el => el.disabled = true);
      try {
        const r = await fetch(form.action, {method: 'POST', body, headers: {'X-Requested-With': 'fetch'}});
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          form.querySelectorAll('input,button').forEach(el => el.disabled = false);
          err.textContent = d.error || 'Something went wrong - please try again.';
          return;
        }
      } catch (x) {
        form.querySelectorAll('input,button').forEach(el => el.disabled = false);
        err.textContent = 'Could not reach the server - check your connection and try again.';
        return;
      }
      if (said) {
        said.querySelector('code').textContent = input ? val : (e.submitter ? e.submitter.textContent.trim() : val);
        said.hidden = false;
      }
      form.hidden = true;
      start();
    });
  }
})();
</script>
"""
