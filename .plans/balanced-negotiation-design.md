# Balanced negotiation — design (parallel proposals + mediator)

Branch: `balanced-proposals`. Runs next to the original sequential pipeline (`orchestrator.py`);
nothing in the original pipeline changes. Second page in the Streamlit sidebar.

## Why
The sequential pipeline passes one proposal ATC → Pilot → Ground. Whoever speaks first anchors the
result, nobody compares trade-offs, and soft preferences don't affect the outcome. In a real run the
agents settled on 10:41 while 10:48 was better for everyone.

## Core idea
**Hard rules define the space of allowed options. Soft preferences pick a point inside it.**
The AI agents negotiate; code checks safety and does the balancing.

| Tier | Examples | Who checks | Negotiable? |
|---|---|---|---|
| Red line (safety / physics) | wake separation ≥ 3 min, fuel reserve ≥ 30 min, no earlier than possible, gate must exist and be free | code, with the real data | never |
| Hard rule (policy) | crew overtime ≤ 30 min | code, with the real data | only in a declared emergency, logged |
| Soft preference | little holding, connections, crew ready, preferred gate | mediator (code), from what each agent **declares** | yes - this is the negotiation |

## Information: each agent only sees its own domain
The brief asks for negotiation "across organisational boundaries" and "independent domains", and for
agents that "exchange structured data". So data is not shared by default.

| Information | Who sees it |
|---|---|
| The DOT flight row (delay, times, airports) | all three (brief requirement) |
| Public rulebook (separation, fuel, overtime limits), list of real gates | all three |
| Other scheduled arrivals | ATC only |
| Fuel reserve, connecting passengers, connection cut-off times | Pilot only |
| Gate busy windows, when the crew is free, shift end, gate nearest the crew base | Ground only |

Agents learn each other's limits only through **published declarations** (below).

## Each round (max 3)
1. **Propose (parallel).** Each agent sends a proposal plus a structured declaration:
   - `blocked_slots` – times/gates it cannot accept (its red lines / hard rules), e.g. `10:34-10:38 any; 10:28-11:03 D10`
   - `latest_acceptable_arrival` – e.g. `11:03`
   - `preferred_window` + importance (high / low), `preferred_gate` + importance
   - `i_can_give`, `i_need`, rationale

   From round 2, every agent sees all agents' published declarations and last round's objections.
2. **Check (code).** Each proposal is checked against red lines / hard rules with the real data.
3. **Mediate (code, no AI).**
   - Options: every minute from the earliest possible arrival to +60 min × every real gate.
   - Remove options that fail the real safety check, that any agent declared blocked, or that were
     rejected in an earlier round.
   - Score each option per agent **from that agent's declared preferences** (0–10):
     outside the preferred window: −0.5 per minute × weight; not the preferred gate: −2 × weight;
     weight high = 2, low = 1. No time window declared → the agent's own proposed time ± 2 min is
     its ideal. A gate preference only counts if declared (ATC, for example, doesn't care about gates).
   - Pick the option where the **worst-off agent scores highest**; ties → higher total → earlier.
   - Emergency: the aircraft in trouble comes first (pilot score, then the rule above).
4. **Review (parallel).** Each agent ACCEPTs or OBJECTs. An objection must say why
   (`red_line` / `hard_rule` / `preference`) and may update the agent's declaration.
5. **Consensus** = all ACCEPT + the option passes every real check (existing `mediator.evaluate_turn`).
   Accepting below one's own minimum (6) is allowed and logged as a concession.
   No consensus → the rejected option is excluded next round; updated declarations are used.

**Fallback (no AI)** after 3 rounds: mediate again over everything allowed (rejected options allowed
again). If nothing is safe at all, the existing `fallback.resolve()` escalates to human review.

## Emergency (automatic, no human)
If the fuel reserve at the earliest possible arrival is ≤ 40 min, the pilot side declares
**Minimum Fuel** automatically. Effects: crew-overtime limit 30 → 45 min (logged), ATC and Ground
minimum score → 0, mediator puts the pilot first. Red lines never change.
The page has a "simulate low fuel" checkbox that only lowers the reserve; the system decides the rest.

## Files
- `balancer.py` – mediator: parse declarations, real safety filter, declared scoring, balance rule
- `agents_balanced.py` – propose / review calls (parallel), private data views
- `orchestrator_balanced.py` – the round loop, emergency detection, consensus log
- `rules.py` – tiers, emergency trigger and relaxations
- `scoring.py` – "true" satisfaction from the hidden data; **only used to evaluate outcomes**
  (`compare.py`), not by the mediator
- `views/balanced_negotiation.py`, `compare.py`, tests
