# Hackathon 2026 — Challenge #2: Multi-Agent ATC Negotiation

Python + Streamlit + Claude API.

"How might AI help air traffic controllers, pilots and ground operations staff make faster and better decisions?"

A runway closes and a bank of 6-8 real arrivals has to be re-planned. The demo splits the work
between Claude and code:

| Step | Who | What |
|---|---|---|
| 1. Read the messages | Claude | Turns a raw NOTAM, METAR and free-text ops note into structured facts. Code then checks every fact against the airport data and converts UTC to local time. |
| 2. Generate options | Code (`solver.py`) | Simulates ~1,650 landing orders and gate plans. Keeps only safe ones (runway rate, wake separation, final reserve fuel, gate availability). Finds the Pareto front and picks 3-5 representative options. |
| 3. Rank and argue | Claude x3 | ATC, Flight Crew and Ground Ops each rank the options from their own side, then see each other's positions and reply in a second round. They can veto. |
| 4. Decide | Code (`negotiate.py`) | Fixed voting rule: Borda count, vetoes remove an option, ties go to the most balanced option. |
| 5. Brief | Claude | Writes a plain-English decision brief from code-computed comparisons. |

Every LLM step has a deterministic fallback, so the demo always reaches a safe decision.

**Data.** 40 real disrupted arrival banks at 9 US hubs from the 2015 DOT dataset (flights, times,
delays, tail numbers, each aircraft's next departure, onward connections). Fields the dataset lacks
(aircraft type, wake category, fuel, gate IDs, passenger counts, the closure and weather messages)
are synthetic, derived deterministically from reference tables in `reference_data.py`, and
labelled in the UI.

**Evaluation.** `evaluate.py` runs all 40 events (278 flights) and compares the negotiated outcome
with a no-coordination baseline and a single-objective optimiser. See the Evaluation page.

The original single-flight negotiation (v1) is still available as its own page.
Its design spec: [`.plans/atc-multiagent-negotiation-spec.md`](.plans/atc-multiagent-negotiation-spec.md).

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit entry point (page router) |
| `views/cascade.py` | Main demo page: runway closure cascade |
| `views/evaluation.py` | Evaluation results page |
| `views/single_flight.py` | v1 single-flight negotiation page |
| `views/balanced_negotiation.py`, `orchestrator_balanced.py`, `agents_balanced.py`, `balancer.py`, `rules.py`, `scoring.py`, `compare.py` | Balanced negotiation: parallel proposals + mediator (design: `.plans/balanced-negotiation-design.md`) |
| `ai.py` | All Claude calls for the cascade: intake, agent rankings, decision brief |
| `solver.py` | Deterministic plan generator, Pareto front, independent safety check |
| `negotiate.py` | Cascade pipeline and the voting rule |
| `charts.py` | Altair charts |
| `reference_data.py` | Aircraft, wake, airport and ops reference tables |
| `build_cascades.py` | Builds `events/cascades.json` from `data/flights.csv` (run once) |
| `evaluate.py` | Evaluation harness, writes `events/evaluation_results.json` |
| `events/` | Committed event and evaluation data, so the app runs without the raw dataset |
| `agents.py`, `orchestrator.py`, `constraints.py`, `mediator.py`, `fallback.py`, `scenario.py`, `log_render.py` | v1 single-flight negotiation |
| `benchmark.py` | v1 benchmark |
| `test_api.py` | Checks that your API key works |
| `tests/` | Offline unit tests (`python -m pytest`) |
| `setup.ps1` | One-click first-time setup |
| `.env` | Your API key (**never uploaded to GitHub**, everyone creates their own) |
| `CLAUDE.md` | Team rules that Claude reads automatically |
| `PLAYBOOK.md` | Our hackathon plan, step by step |

## First-time setup (once per computer)

Install [Git](https://git-scm.com) and [Python 3.12](https://python.org) (3.10+ works) first (tick "Add python.exe to PATH"). Then:

```powershell
git clone https://github.com/Jerry-0821/hackathon-2026.git
cd hackathon-2026
powershell -ExecutionPolicy Bypass -File setup.ps1
```

`setup.ps1` creates the virtual environment with Python 3.10+ (3.12 preferred), installs packages and creates `.env`. If you already have a `.venv` from an older Python, delete the `.venv` folder and run it again. Then open `.env` and paste the API key after `ANTHROPIC_API_KEY=`.

> If `Activate.ps1` says "running scripts is disabled", run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Every time you start work

```powershell
.venv\Scripts\Activate.ps1
python test_api.py      # check the key (optional)
python -m pytest        # offline unit tests, no API calls
streamlit run app.py    # open the web app
```

Pick a ★ event and click **Run**. Try editing the ops note first, e.g. close a second gate. Save your code, then click **Rerun** at the top right of the web page to see changes.

## Working together

- Before you start: `git pull`. When done: `git add .` → `git commit -m "what you did"` → `git push`
- See `CLAUDE.md` for the Ownership split once it's filled in.
- Send the API key by private message. **Never put it in code.**

## Rebuilding the data (optional)

```powershell
python build_cascades.py      # needs data/flights.csv (DOT 2015, not in git) - about 1 minute
python evaluate.py            # deterministic evaluation, no API cost - about 1 minute
python evaluate.py --agents 6 # also run the Claude pipeline on 6 events
```

## Changing the model

`MODEL` in `agents.py` is currently `claude-haiku-4-5-20251001` (fast/cheap). `ai.py` uses the same model. A stronger model should reason better about the numbers. Note: Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject the forced `tool_choice` these files use, so switching to them needs `tool_choice: auto` plus `strict: true` tools. Run `python -m pytest` and one live run after any switch.
