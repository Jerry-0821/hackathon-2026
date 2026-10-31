# Test Results

Date: 2026-10-31. Branch: `main`. Model: `claude-haiku-4-5-20251001`.

Summary: every test passed. No safety violations. The live Claude pipeline worked on every event with no fallbacks.

## How to repeat these tests

```powershell
.venv\Scripts\Activate.ps1
python -m pytest -m "slow or not slow"   # 1. unit tests, offline, about 1 minute
python evaluate.py                       # 2. 40 events, no API cost, about 1 minute
python evaluate.py --agents 6            # 3. also runs live Claude on 6 events (uses the API)
```

Raw numbers are in `events/evaluation_results.json`. The app shows them on the Evaluation page.

## 1. Unit tests

60 of 60 passed, including the slow test that solves all 40 events.

## 2. Decision quality on 40 events

The flight CSV data gives 40 real disrupted arrival banks at 9 US airports, 278 flights in total.
Each event is solved four ways under the same safety rules:

- **Baseline:** first come, first served. Every flight waits for its planned gate. Nobody re-plans.
- **Min delay:** an optimiser that only cuts total delay.
- **Mediated:** the demo's default process, with code stand-ins for the three agents.
- **Vote:** the alternative process: two ranking rounds, Borda count and vetoes.

This run makes no API calls. A second run gave the same numbers, so the results reproduce.

Average per event:

| Metric | Baseline | Min delay | Mediated | Vote |
|---|---|---|---|---|
| Total delay (min) | 296.5 | 200.0 | 200.9 | 200.5 |
| Missed passenger connections | 151.3 | 141.1 | 141.8 | 141.4 |
| Lowest fuel margin (min, higher is safer) | 5.8 | 6.8 | **8.5** | 7.7 |
| Delay to later flights (min) | 226.8 | 139.1 | 140.0 | 135.6 |
| Crew overtime (min) | 67.1 | 22.2 | 22.2 | 22.2 |
| Gate changes | 0.1 | 2.7 | 3.3 | 3.0 |
| Events with a diversion | 1 | 0 | 0 | 0 |

Mediated compared with baseline, per event (40 events):

| Metric | Better | Same | Worse |
|---|---|---|---|
| Diversions | 1 | 39 | 0 |
| Total delay | 36 | 4 | 0 |
| Missed connections | 21 | 11 | 8 |
| Lowest fuel margin | 27 | 11 | 2 |
| Gate changes | 0 | 3 | 37 |
| Delay to later flights | 33 | 4 | 3 |
| Crew overtime | 34 | 6 | 0 |

Other results:

- **Safety:** 0 rule violations across every generated plan.
- **Agreement:** all three agents accepted the mediator's option in 40 of 40 events. No fallback was needed.
- **Minimum fuel:** 4 events had a flight at minimum fuel. The rules handled it in all 4.

What this means:

- Mediated cuts total delay by about 32% compared with baseline. It is never worse on delay.
- It matches the delay-only optimiser within about 1 minute, and keeps the most fuel margin.
- The cost is more gate changes. This is a deliberate trade-off to reduce delay. Say so if judges ask.

## 3. Live Claude pipeline on 6 events

Real API calls. This runs the vote process, not the mediated default.

| Event | Time (s) | API calls | Input tokens | Output tokens | Intake correct | Fallbacks | Claude pick | Code pick |
|---|---|---|---|---|---|---|---|---|
| CLT-2015-03-01-1503 | 17.3 | 8 | 23,391 | 2,489 | yes | 0 | A | C |
| DFW-2015-08-01-1740 | 17.9 | 8 | 23,241 | 2,496 | yes | 0 | A | A |
| ORD-2015-03-01-1705 | 17.6 | 8 | 30,199 | 2,355 | yes | 0 | E | E |
| ATL-2015-01-03-0900 | 17.5 | 8 | 23,184 | 2,465 | yes | 0 | A | A |
| ATL-2015-01-03-1600 | 16.6 | 8 | 26,577 | 2,175 | yes | 0 | C | A |
| ATL-2015-02-26-1005 | 15.0 | 8 | 28,480 | 2,185 | yes | 0 | A | C |
| **Average** | **17.0** | **8** | **25,845** | **2,361** | **6 of 6** | **0** | | |

- Claude read the runway closure, closed gates and weather correctly in all 6 events.
- No step fell back to the code answer.
- Claude agents picked a different option than the code stand-ins in 3 of 6 events. Every pick was safe and none caused a diversion.

## Open points

- The live test covers the vote process only. Live numbers for the mediated default need a small change to `evaluate.py`.
- 6 live events is a small sample. Run more before quoting the live numbers as final.
