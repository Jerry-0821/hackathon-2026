# Cascade negotiation — merged design

Branch: `mediator-negotiation`. Merges the v2 cascade (multi-flight runway closure, safe options from
`solver.py`) with the negotiation design from `balanced-negotiation-design.md`. Agreed by both of us.

Rule for merging: for every step, use whichever side fits the challenge brief better.

| Brief requirement | Taken from |
|---|---|
| Cascading delays, runway closure / gate out / weather, flight plan adjustment, real DOT data | v2 (solver, events) |
| Explicit constraint checking, safety margins | v2 (solver + `verify`) plus hard-rule tier |
| Negotiate until a consensus is reached | mediator + all three must ACCEPT |
| Exchange structured data, cross-domain constraints, organisational boundaries | private views + published declarations |
| **Under 3 turns** | 2 agent turns, then a deterministic decision |
| Deterministic non-AI fallback, traceable log | mediator max-min, then Borda + veto; full JSON log |

## Rule tiers (`cascade_rules.py`)
| Tier | Rule | Owner | Checked by |
|---|---|---|---|
| Red line | runway rate + wake separation, no landing during full closure, land above final reserve fuel (else divert), gate open / free / size-compatible | all | `solver` builds only such plans; `solver.verify` re-checks |
| Hard rule | max airborne holding per flight ≤ 30 min | ATC | code, every option |
| Hard rule | lowest fuel margin ≥ 5 min above final reserve (company buffer) | Pilot | code, every option |
| Hard rule | ground crew overtime ≤ 30 min | Ground | code, every option |
| Soft | the role's metrics (delay, connections, fuel margin, knock-on, gate changes...) | each role | mediator, weighted by the role's **declared** priorities |

Hard rules are relaxed only (a) in a Minimum Fuel emergency, or (b) when no option satisfies a rule at
all. Every relaxation is logged with its reason. Red lines are never relaxed.
(Data check: in 13 of 40 events every option has crew overtime > 30, so (b) is needed.)

## Minimum Fuel emergency (automatic)
Declared by code when even the best safe plan (fewest diversions, then most fuel) leaves some flight
≤ 5 min above final reserve (4 of 40 events). Effects: company fuel buffer → 0 (final reserve stays),
crew-overtime limit → 45 min, the mediator ranks the pilot's score first. Logged.

## Information: each agent sees only its own domain
- ATC: ETAs, wake categories, runway closure; per option: landing times and holding.
- Flight Crew: fuel above final reserve, connections; per option: holding, fuel margin, missed pax.
- Ground Ops: gates, busy windows, crew shift end, next departures; per option: gate, gate wait, knock-on, crew overtime.
- Everyone: diversions per option (a shared safety outcome), the mediator's scores, and its own hard-rule
  check per option (computed by code - models misjudge limits, see the balanced-negotiation fix).

## Two turns
**Turn 1 (parallel)** — each agent publishes:
- priorities: high / low / none for each of its own metrics (feeds the mediator's weights)
- preferred option, acceptable options, vetoes with a reason
- optionally its own plan: a landing order + gate policy (keep / flex). Code simulates and verifies it;
  if safe and new, it becomes an extra option for everyone.
- messages to named agents (request / offer / concession / reject)

**Mediator (code)** — scores every option 0–10 per agent from the declared priorities, drops options
that break a (non-relaxed) hard rule, were vetoed, prefers options acceptable to all, and recommends the
option where the worst-off agent scores highest (emergency: pilot first).

**Turn 2 (parallel)** — each agent sees all published declarations, the added plans and the messages,
and votes ACCEPT or OBJECT on the recommendation (and may update what it can accept).

**Decision** — all three ACCEPT → consensus in turn 2. Otherwise no third turn: the mediator picks the
best max-min option among those all three listed as acceptable in turn 2 (excluding the objected one);
if there is none, `negotiate.decide()` (Borda + veto). Both are deterministic and logged.

## Files
- `cascade_rules.py` (new) – tiers, hard-rule checks and relaxations, Minimum Fuel detection
- `mediation.py` – scores from declared priorities, hard-rule filter, emergency ordering, stand-ins
- `ai_negotiation.py` – private per-role views, per-role tool with priorities and proposals
- `negotiate_mediated.py` – the 2-turn pipeline, proposals → simulate → verify → add, decision
- `views/cascade.py` – shows emergency, hard rules, declarations, added plans, 2 turns
- `tests/test_mediation.py` – offline tests
Unchanged: `solver.py`, `ai.py`, `negotiate.py` (the vote process), `evaluate.py`.
