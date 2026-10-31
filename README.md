# Hackathon 2026 — Challenge #2: Multi-Agent ATC Negotiation

Python + Streamlit + Claude API.

"How might AI help air traffic controllers, pilots and ground operations staff make faster and better decisions?"

A runway closes and a bank of 6-8 real arrivals has to be re-planned. The demo splits the work
between Claude and code:

| Step | Who | What |
|---|---|---|
| 1. Read the messages | Claude | Turns a raw NOTAM, METAR and free-text ops note into structured facts. Code then checks every fact against the airport data and converts UTC to local time. |
| 2. Generate options | Code (`solver.py`) | Simulates ~1,650 landing orders and gate plans. Keeps only safe ones: runway capacity left after the closure (shared with the real traffic that hour), FAA wake separation, final reserve fuel, open and free gates, 20 min gate-change notice. Finds the Pareto front and picks 3-5 representative options. |
| 3. Negotiate | Claude x3 | ATC, Flight Crew and Ground Ops, each seeing only its own organisation's data. **Mediated (default):** turn 1 declares priorities, positions and optional own plans; a mediator (code) recommends the option where the worst-off agent does best; turn 2 is accept or object. **Vote:** two ranking rounds, with vetoes. |
| 4. Decide | Code (`negotiate_mediated.py`, `negotiate.py`) | Mediated: consensus if all three accept, else the best option all three called acceptable, else Borda + veto. Vote: Borda count, vetoes remove an option, ties go to the most balanced option. |
| 5. Brief | Claude | Writes a plain-English decision brief from code-computed comparisons. |

Every LLM step has a deterministic fallback, so the demo always reaches a safe decision.

**Data.** 40 real disrupted arrival banks at 9 US hubs from the 2015 DOT dataset (flights, times,
delays, tail numbers, each aircraft's next departure, onward connections, and the real traffic at the
airport that hour). Every event contains a row that meets the rules of play (WEATHER_DELAY > 15 or
LATE_AIRCRAFT_DELAY > 20). Aircraft type, gate IDs, passenger counts, the closure and the messages
are synthetic, derived deterministically, and labelled in the UI.

**Constraints.** All operational limits come from the challenge workbook,
`reference/challenge-2-operational-constraints.xlsx`, read by `workbook.py`: runway capacity,
gates, spare gates, turnaround, wake separation, fuel and crew rules. Every change or assumption
we made is listed in `workbook.STATED_CHANGES` and on the app's **Constraints** page, as the rules
of play require. Replace the workbook file and rebuild (`python build_cascades.py`) to use new limits.

**Evaluation.** `evaluate.py` runs all 40 events (278 flights) with deterministic stand-in agents and
compares both agent processes with a no-coordination baseline and a single-objective optimiser.
See the Evaluation page.

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
| `ai.py`, `ai_negotiation.py` | Claude calls for the cascade: intake, agent positions and rankings, decision brief |
| `negotiate_mediated.py`, `mediation.py`, `cascade_rules.py` | Mediated process: rule tiers, mediator recommendation, two agent turns (design: `.plans/cascade-negotiation-design.md`) |
| `solver.py` | Deterministic plan generator, Pareto front, independent safety check |
| `negotiate.py` | Vote process and shared pipeline pieces |
| `charts.py` | Altair charts |
| `workbook.py`, `reference/` | The challenge constraints workbook and the code that reads it; the stated changes |
| `views/operational_constraints.py` | Constraints page: every workbook limit used and every change we made |
| `reference_data.py` | Aircraft types, fleets, airport runway names and time zones (not limits) |
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
python -m pytest        # offline unit tests, no API calls (add -m slow for the 1-minute all-events test)
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
