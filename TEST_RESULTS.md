# Test Results

Date: 2026-10-31. Branch: `main`. Model: `claude-haiku-4-5-20251001`.
Constraints: the organisers' challenge constraints workbook (see the Constraints page).

Summary: every test passed. No safety violations. The live Claude pipeline worked on every event with no fallbacks.

## How to repeat these tests

```powershell
.venv\Scripts\Activate.ps1
python -m pytest -m "slow or not slow"   # 1. unit tests, offline, about 1.5 minutes
python evaluate.py                       # 2. 40 events, no API cost, about 1 minute
python evaluate.py --agents 6            # 3. also runs live Claude on 6 events (uses the API)
```

Raw numbers are in `events/evaluation_results.json`. The app shows them on the Evaluation page.

## 1. Unit tests

68 of 68 passed, including the slow test that solves all 40 events.

## 2. Decision quality on 40 events

The flight CSV data gives 40 real disrupted arrival banks at 9 US airports, 272 flights in total.
Every event contains a row that meets the rules of play (WEATHER_DELAY > 15 or LATE_AIRCRAFT_DELAY > 20).
Each event is solved four ways under the same safety rules, all taken from the constraints workbook:

- **Baseline:** first come, first served. Every flight waits for its planned gate. Nobody re-plans.
- **Min delay:** an optimiser that only cuts total delay.
- **Mediated:** the demo's default process, with code stand-ins for the three agents.
- **Vote:** the alternative process: two ranking rounds, Borda count and vetoes.

This run makes no API calls. A second run gives the same numbers, so the results reproduce.

Average per event:

| Metric | Baseline | Min delay | Mediated | Vote |
|---|---|---|---|---|
| Total delay (min) | 106.3 | 44.9 | 46.3 | 47.0 |
| Missed passenger connections | 75.5 | 43.5 | **39.8** | 45.2 |
| Lowest fuel margin (min, higher is safer) | 7.0 | 6.5 | 6.3 | 7.1 |
| Delay to later flights (min) | 116.7 | 66.4 | 65.3 | 65.5 |
| Crew overtime (min) | 31.4 | 0.0 | 0.0 | 0.0 |
| Gate changes | 1.0 | 2.6 | 2.4 | 2.4 |
| Events with a diversion | 7 | 5 | 5 | 5 |

Mediated compared with baseline, per event (40 events):

| Metric | Better | Same | Worse |
|---|---|---|---|
| Diversions | 2 | 38 | 0 |
| Total delay | 36 | 4 | 0 |
| Missed connections | 36 | 4 | 0 |
| Lowest fuel margin | 11 | 16 | 13 |
| Gate changes | 0 | 6 | 34 |
| Delay to later flights | 29 | 6 | 5 |
| Crew overtime | 26 | 14 | 0 |

Other results:

- **Safety:** 0 rule violations across every generated plan.
- **Agreement:** with code stand-ins, all three agents accepted the mediator's option in 39 of 40 events
  (0 mediator fallback, 1 vote fallback). Stand-ins accept whatever the mediator
  recommends, so this says nothing about live Claude agents.
- **Minimum fuel:** 12 events had a flight at minimum fuel. In 5 events even the best safe plan diverts one
  flight (baseline: 7).

What this means:

- Mediated cuts total delay by about 56% and missed connections by about 47% compared with baseline.
  It is never worse on delay.
- It is within about 1.4 minutes of the delay-only optimiser and misses fewer connections.
- The cost is more gate changes. This is a deliberate trade-off to reduce delay. Say so if judges ask.
- Its lowest fuel margin is a little lower than baseline's (6.3 vs 7.0 min; lower in 13 events): it
  re-orders landings to cut delay, so some flights hold longer. Every flight still lands at or above
  final reserve. Margins of 0-1 min happen in 7 events, 6 of them under a Minimum Fuel emergency.
- In 1 event (DFW-2015-03-29) every option breaks a hard rule (fuel buffer or crew overtime), so the
  mediator sets hard rules aside and the vote picks a plan 1 min above reserve to avoid overtime.
  Whether fuel buffer should outrank overtime is an open design question.

## 3. Live Claude pipeline on 6 events

Real API calls. This runs the vote process, not the mediated default.

| Event | Time (s) | API calls | Input tokens | Output tokens | Intake correct | Fallbacks | Claude pick | Code pick |
|---|---|---|---|---|---|---|---|---|
| ATL-2015-08-28-1243 | 20.2 | 8 | 25,197 | 2,685 | yes | 0 | D | A |
| MSP-2015-01-02-1200 | 19.9 | 8 | 27,059 | 2,663 | yes | 0 | B | A |
| ORD-2015-06-11-1301 | 16.8 | 8 | 28,332 | 2,280 | yes | 0 | A | E |
| ATL-2015-03-03-1724 | 15.7 | 8 | 22,000 | 2,304 | yes | 0 | A | C |
| ATL-2015-03-20-1700 | 15.6 | 8 | 23,429 | 2,083 | yes | 0 | A | B |
| ATL-2015-07-18-1442 | 16.0 | 8 | 24,406 | 2,342 | yes | 0 | A | A |
| **Average** | **17.4** | **8** | **25,070** | **2,393** | **6 of 6** | **0** | | |

- Claude read the runway closure, closed gates and weather correctly in 6 of 6 events.
- No step fell back to the code answer.
- Claude agents picked a different option than the code stand-ins in 5 of 6 events. Every pick was safe.
- Separate live runs of the mediated default on the 3 demo events: 2 of 3 reached consensus; the third
  (Minimum Fuel, one unavoidable diversion) was decided by the deterministic fallback. 0 safety violations.

## Open points

- The live evaluation table covers the vote process only. Live numbers for the mediated default need a small change to `evaluate.py`.
- 6 live events is a small sample. Run more before quoting the live numbers as final.
