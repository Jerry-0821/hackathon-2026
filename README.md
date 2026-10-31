# Hackathon 2026 — Challenge #2: Multi-Agent ATC Negotiation

Python + Streamlit + Claude API.

"How might AI help air traffic controllers, pilots and ground operations staff make faster and better decisions?"

Three independent Claude agents — **ATC**, **Pilot**, and **Ground Ops** — negotiate a revised
arrival time and gate assignment for one real disrupted flight (AA 1057, DFW → MIA, from the
2015 DOT flight delays dataset). Each agent is a separate API call with its own objectives; a
deterministic (non-AI) constraint validator vetoes any proposal that breaks safety/ops limits;
negotiation is capped at 3 turns, after which a deterministic rule-based fallback picks a safe
outcome. Full design spec: [`.plans/atc-multiagent-negotiation-spec.md`](.plans/atc-multiagent-negotiation-spec.md).

## Files

| File | What it does |
|---|---|
| `app.py` | Web UI (Streamlit) — runs the negotiation live and renders it turn by turn |
| `scenario.py` | The fixed demo flight + derived fuel/gate/crew fields |
| `agents.py` | The 3 independent Claude calls (ATC / Pilot / Ground), one structured JSON response each |
| `constraints.py` | Deterministic, non-AI safety/ops validator (wake separation, fuel reserve, crew overtime, gate conflicts) |
| `mediator.py` | Deterministic, non-AI consensus gate (no LLM call) |
| `fallback.py` | Deterministic rule-based outcome if no consensus within 3 turns |
| `orchestrator.py` | The turn loop (ATC → Pilot → Ground, capped at 3 turns), writes `consensus_log.json` |
| `log_render.py` | Renders the JSON consensus log as a static HTML timeline |
| `test_api.py` | Checks that your API key works |
| `setup.ps1` | One-click first-time setup |
| `.env` | Your API key (**never uploaded to GitHub**, everyone creates their own) |
| `CLAUDE.md` | Team rules that Claude reads automatically |
| `PLAYBOOK.md` | Our hackathon plan, step by step |

## First-time setup (once per computer)

Install [Git](https://git-scm.com) and [Python 3.10+](https://python.org) first (tick "Add python.exe to PATH"). Then:

```powershell
git clone https://github.com/Jerry-0821/hackathon-2026.git
cd hackathon-2026
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` creates the virtual environment, installs packages and creates `.env`. Then open `.env` and paste the API key after `ANTHROPIC_API_KEY=`.

> If `Activate.ps1` says "running scripts is disabled", run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Every time you start work

```powershell
.venv\Scripts\Activate.ps1
python test_api.py      # check the key (optional)
python -m pytest        # offline unit tests, no API calls
streamlit run app.py    # open the web app
```

Click "Run negotiation" in the browser to watch the 3 agents negotiate live. Save your code, then click **Rerun** at the top right of the web page to see changes.

## Working together

- Before you start: `git pull`. When done: `git add .` → `git commit -m "what you did"` → `git push`
- See `CLAUDE.md` for the Ownership split once it's filled in.
- Send the API key by private message. **Never put it in code.**

## Changing the model

`MODEL` in `agents.py` is currently `claude-haiku-4-5-20251001` (fast/cheap, good for iterating on the negotiation logic). Swap to a Sonnet/Opus model there for higher-quality agent reasoning if time allows.
