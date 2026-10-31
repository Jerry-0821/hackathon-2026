# Hackathon 2026 — Challenge #2: Multi-Agent ATC Negotiation

Autonomous SG 2026 · AI & Aerospace track · Airbus problem statement #2.
Python + Streamlit + Claude API.

> "How might AI help air traffic controllers, pilots and ground operations staff make faster and better decisions?"

## The problem

A runway closes in a thunderstorm while a bank of 6-8 arrivals is 20 minutes out. Someone has to
decide the landing order and the gates. Today the tower, the airline and the ramp agree on this by
phone. Each side only sees its own data, so the plan is slow, and it often costs extra holding,
fuel, missed connections and crew overtime.

**Users:** the airline duty manager and the tower supervisor. The system recommends a plan. A person
approves it. Nothing is sent to an operational system.

## What it does

Three Claude agents (ATC, Flight Crew, Ground Ops) negotiate a revised landing time and gate for
every flight in the bank. Each agent is a separate API call and sees only its own organisation's
data. Code does the safety checks, the scoring and the decision. Claude reads text and writes text.

| Step | Who | What |
|---|---|---|
| 1. Read the messages | Claude | Turns a raw NOTAM, METAR and free-text ops note into structured facts. Code checks every fact against the airport data and converts UTC to local time. |
| 2. Build safe options | Code (`solver.py`) | Simulates ~1,650 landing orders and gate plans. Keeps only plans that pass the red lines: runway rate and wake separation, no landing while every runway is closed, every flight above final reserve fuel (or it diverts), gates open, free and size-compatible. Picks 3-5 options from the Pareto front. |
| 3. Check rules | Code (`cascade_rules.py`) | Hard rules per role on every option: ATC holding ≤ 30 min, Flight Crew fuel buffer ≥ 5 min above final reserve, Ground crew overtime ≤ 30 min. A rule is relaxed only in a Minimum Fuel emergency or when no option can meet it, and every relaxation is logged. Minimum Fuel is declared by code, not by the model. |
| 4. Turn 1 | Claude x3, in parallel | Each agent declares its priorities (high / low / none), its preferred and acceptable options, vetoes, an optional own plan, and messages to the other agents. Code simulates and verifies any proposed plan before it can become an option. |
| 5. Mediate | Code (`mediation.py`) | Scores each option 0-10 per agent from the priorities that agent declared. Drops options that break a hard rule or were vetoed. Recommends the option where the worst-off agent does best. |
| 6. Turn 2 | Claude x3, in parallel | Each agent sees the others' published positions and votes ACCEPT or OBJECT. |
| 7. Decide | Code (`negotiate_mediated.py`) | All three accept → consensus. Otherwise a deterministic fallback: the best max-min option all three listed as acceptable, else Borda count + veto. The log says which rule decided and why. |
| 8. Brief | Claude | Writes a short decision brief from numbers computed by code. |

Every Claude step has a deterministic stand-in. If a call fails or returns a bad answer, the
stand-in is used, the UI labels it, and the run still ends in a safe decision.

### How this maps to the brief

| Brief asks for | Where |
|---|---|
| ATC, Pilot and Ground agents, not one combined prompt | 3 separate calls per turn (`ai_negotiation.py`); the UI labels each bubble "separate Claude call" |
| Structured messages | Tool-use JSON per agent; every bubble has a "Structured message (JSON)" view |
| Cross-domain constraints checked explicitly | Red lines in `solver.py` + independent re-check `solver.verify`; hard rules in `cascade_rules.py`; side panel "Cross-domain checks" |
| Turn limit, no endless loops | 2 agent turns, then code decides. Limit of 3 shown in the UI |
| Deterministic non-AI fallback | `mediation.resolve_without_consensus`, then `negotiate.decide` (Borda + veto) |
| Traceable consensus log | Full JSON log per run (download button), including every message, check, score and the decision rule |
| Output: revised arrival time, gate, flight plan | Per-flight table: ETA → new landing time, holding, gate (changed or not), fuel margin |

## Demo

```powershell
streamlit run app.py
```

1. **Negotiation room** (default page). Pick a ★ event and click **Run**. The negotiation appears as
   a chat. The side panel shows the turn limit, who can accept which option, the checks and the
   decision. At the bottom: the recommended plan, an **Approve** button for the duty manager, and
   the log download.
2. Open **Incoming messages and flights** and edit the ops note (for example, close a second gate)
   to change the situation.
3. Switch **Claude agents** off to run with the deterministic stand-ins. No API cost.
4. **Results (40 events)** shows the evaluation. **Full detail** shows every step as tables.

URL options: `?autorun=1` starts the run on page load; `&agents=0` starts with the agents off.

A live run takes 8 API calls (1 intake, 3 + 3 agents, 1 brief), about 15-60 s, about $0.05 with
Claude Haiku 4.5.

## Evaluation

`evaluate.py` runs all 40 events (278 real flights, 9 US hubs) with the deterministic stand-in
agents, so the numbers are reproducible. Averages per event:

| Method | Total delay (min) | Knock-on departure delay (min) | Crew overtime (min) | Missed connections (pax) | Lowest fuel margin (min) | Gate changes |
|---|---|---|---|---|---|---|
| No coordination (land in ETA order, wait for planned gate) | 296 | 227 | 67 | 151 | 5.8 | 0.1 |
| Min-delay optimiser (one objective) | 200 | 139 | 22 | 141 | 6.8 | 2.7 |
| **Mediated negotiation (demo default)** | **201** | **140** | **22** | **142** | **8.5** | **3.3** |

- 0 safety violations across every option and baseline (independent re-check).
- Against no coordination, the mediated outcome has less total delay in 36 of 40 events and is
  never worse. It needs more gate changes in 37 of 40 events: that is the price of re-planning.
- Against the min-delay optimiser it gives up about 1 min of delay for 1.7 min more fuel margin.
- 4 of 40 events trigger an automatic Minimum Fuel emergency.
- Live Claude runs on 6 events: intake read all 6 message sets correctly; 8 calls and about
  17 s per run; the Claude agents chose the same option as the stand-ins in 3 of 6.

### Where it fails, and how the user knows

- **Agents misread numbers.** In one live CLT run, ATC called 51 min "max holding" when 51 min was
  the maximum delay. The decision does not depend on this: scores, checks and the decision are
  computed by code, and the UI says the tables are the source of truth.
- **No consensus.** In another live CLT run, Flight Crew and Ground Ops objected to the
  recommendation. Code fell back to the best option all three had called acceptable. The chat shows
  the objections and the fallback rule.
- **A failed or malformed call** is replaced by the deterministic stand-in and labelled
  "⚠ deterministic stand-in" in the chat.
- **No option meets a hard rule.** The rule is relaxed to the best possible value and the UI shows a
  "Hard rule relaxed and logged" warning (13 of 40 events for crew overtime).

### Limits and assumptions

- Real (DOT 2015): flights, times, delays, tail numbers, each aircraft's next departure, onward
  connections.
- Synthetic, derived deterministically and labelled in the UI: aircraft type, wake category, fuel,
  gate IDs, passenger counts, and the NOTAM / METAR / ops note texts. Built from the reference
  tables in `reference_data.py`.
- The constraints workbook from the organisers (`challenge2_operational_constraints.xlsx`) is not
  read yet. Our planning values are round numbers in `reference_data.py`, not certified values.
- Input is one of 40 pre-built arrival banks built from `flights.csv` rows, not any single row.
- Only the hub carrier's arrivals are modelled. Other traffic on the runway is not.
- The evaluation uses stand-in agents. Only 6 events were run with Claude.
- Not for operational use.

## Files

| File | What it does |
|---|---|
| `app.py` | Streamlit entry point (page router) |
| `views/demo.py` | **Main demo page: Negotiation room** (chat view of the mediated negotiation) |
| `views/evaluation.py` | Results page (40 events) |
| `views/cascade.py` | Full detail page: every step as tables, and the vote process |
| `views/single_flight.py`, `views/balanced_negotiation.py` | Earlier versions (single flight) |
| `ai.py`, `ai_negotiation.py` | All Claude calls for the cascade: intake, agent positions and votes, decision brief |
| `negotiate_mediated.py`, `mediation.py`, `cascade_rules.py` | Mediated process: rule tiers, mediator, two agent turns, decision (design: `.plans/cascade-negotiation-design.md`) |
| `solver.py` | Deterministic plan generator, Pareto front, independent safety check |
| `negotiate.py` | Vote process and shared pipeline pieces |
| `charts.py` | Altair charts |
| `reference_data.py` | Aircraft, wake, airport and ops reference tables |
| `build_cascades.py` | Builds `events/cascades.json` from `data/flights.csv` (run once) |
| `evaluate.py` | Evaluation harness, writes `events/evaluation_results.json` |
| `events/` | Committed event and evaluation data, so the app runs without the raw dataset |
| `mockups/` | Three HTML design drafts for the demo page (C was chosen) |
| `.streamlit/config.toml` | App theme |
| `orchestrator_balanced.py`, `agents_balanced.py`, `balancer.py`, `rules.py`, `scoring.py`, `compare.py` | Earlier version: balanced negotiation (design: `.plans/balanced-negotiation-design.md`) |
| `agents.py`, `orchestrator.py`, `constraints.py`, `mediator.py`, `fallback.py`, `scenario.py`, `log_render.py`, `benchmark.py` | Earlier version: v1 single-flight negotiation (design: `.plans/atc-multiagent-negotiation-spec.md`) |
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

Save your code, then click **Rerun** at the top right of the web page to see changes.

## Working together

- Before you start: `git pull`. When done: `git add .` → `git commit -m "what you did"` → `git push`
- See `CLAUDE.md` for the Ownership split once it's filled in.
- Send the API key by private message. **Never put it in code.**
- The challenge data files and PDF (`flights.csv`, `airlines.csv`, `airports.csv`,
  `challenge2_operational_constraints.xlsx`, `*.pdf`) are git-ignored. Keep them local.

## Rebuilding the data (optional)

```powershell
python build_cascades.py      # needs data/flights.csv (DOT 2015, not in git) - about 1 minute
python evaluate.py            # deterministic evaluation, no API cost - about 1 minute
python evaluate.py --agents 6 # also run the Claude pipeline on 6 events
```

## Changing the model

`MODEL` in `agents.py` is currently `claude-haiku-4-5-20251001` (fast and cheap). `ai.py` uses the same model. A stronger model should reason better about the numbers. Note: Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject the forced `tool_choice` these files use, so switching to them needs `tool_choice: auto` plus `strict: true` tools. Run `python -m pytest` and one live run after any switch.
