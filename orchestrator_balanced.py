"""Parallel-proposal pipeline: an alternative to orchestrator.py, kept side by side to compare.
Design: .plans/balanced-negotiation-design.md

Before round 1: if the fuel reserve is at/below the trigger, a Minimum Fuel emergency is declared
automatically (rules.declare_fuel_emergency) - no human in the loop.

Each round (hard cap of 3):
  1. All three agents propose at the same time, each publishing its limits and preferences.
     From round 2 they see every agent's published declaration and last round's objections.
  2. Every proposal is checked against red lines / hard rules with the real data (no AI).
  3. The mediator (balancer.mediate, no AI) picks the balanced option from the declarations.
  4. All three agents ACCEPT or OBJECT at the same time; objectors may update their declaration.
  5. Consensus = all ACCEPT + the option passes every real check (mediator.evaluate_turn).
     Otherwise the rejected option is excluded and the next round starts.
Fallback after 3 rounds: mediate again with everything allowed; if nothing is safe,
fallback.resolve() escalates to human review.

Events and the log use the same shape as orchestrator.py, so log_render.py works unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import agents
import agents_balanced
import balancer
import fallback
import mediator
import rules
import scenario

ROLES = agents_balanced.ROLES
MAX_ROUNDS = 3


def _save(log: dict) -> None:
    with open("consensus_log.json", "w") as f:
        json.dump(log, f, indent=2)


def _p(p: dict) -> str:
    return f"{p['revised_arrival_time']} at {p['gate_assignment']}"


def run(scenario_id: str = scenario.DEFAULT_SCENARIO, simulate_low_fuel: bool = False):
    """Generator yielding event dicts. The final event has type 'done' and carries the log."""
    state = scenario.initial_state(scenario_id)
    if simulate_low_fuel:
        rules.simulate_low_fuel(state)  # scenario input only
    if rules.fuel_emergency_needed(state):
        rules.declare_fuel_emergency(state)  # decided by the system, not a human

    log = {
        "pipeline": "parallel_proposals_mediated",
        "scenario": {
            "flight": state["flight"],
            "derived": state["derived"],
            "naive_arrival": state["naive_arrival"],
            "emergency": state.get("emergency"),
        },
        "turns": [],
        "outcome": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    yield {"type": "scenario", "state": log["scenario"], "initial_proposal": None}
    if state.get("emergency"):
        yield {"type": "emergency", "emergency": state["emergency"]}

    published = {}  # role -> latest public declaration (incl. its proposal), what the mediator uses
    excluded = []  # options rejected in earlier rounds
    previous_round = None
    turn_history = []
    outcome = None

    for rnd in range(1, MAX_ROUNDS + 1):
        turn_log = {"turn": rnd, "events": [], "votes": []}
        yield {"type": "turn_start", "turn": rnd, "proposal": None}

        # 1. Parallel proposals + declarations. Agents see what was published before this round.
        context = {"published": dict(published), "previous_round": previous_round}
        try:
            proposals = agents_balanced.run_parallel("propose", state, rnd, context)
        except agents.AgentCallError as e:
            yield from _stop_with_error(log, rnd, str(e))
            return

        # 2. Real safety check of each proposal.
        for role in ROLES:
            p = proposals[role]
            published[role] = {"proposal": p["proposal"], **agents_balanced.declaration_of(p)}
            ok, violations = balancer.check(p["proposal"], state)
            validation = {
                "type": "validation", "turn": rnd, "role": role, "proposal": p["proposal"],
                "passed": ok, "violations": violations, "violation_tiers": rules.classify(violations),
            }
            response = {
                **p, "turn": rnd, "vote": "PROPOSE", "declaration": published[role],
                "rationale": f"Proposes {_p(p['proposal'])}. {p.get('rationale', '')}",
                "utility_fields": {},
            }
            for ev in (validation, {"type": "agent_response", "turn": rnd, "response": response}):
                turn_log["events"].append(ev)
                yield ev

        # 3. Mediator.
        mediated = balancer.mediate(published, state, excluded)
        if not mediated["found"]:
            summary = {"type": "turn_summary", "turn": rnd, "final_proposal": None, "final_constraint_ok": False,
                       "final_violations": [], "mediator": {"consensus": False, "reasons": ["no allowed option left"]},
                       "counts": mediated["counts"], "parse_warnings": mediated["parse_warnings"]}
            turn_log["summary"] = summary
            log["turns"].append(turn_log)
            yield summary
            # Give the agents another round to loosen their declarations, instead of stopping here.
            previous_round = {
                "no_option_left": True,
                "message": ("No arrival time and gate was allowed by every agent's declaration together. "
                            "Check your blocked_slots and latest_acceptable_arrival against the others' "
                            "declarations, and block only what your own data really rules out."),
                "option_counts": mediated["counts"],
                "declarations_that_clashed": {r: {k: d.get(k) for k in ("proposal", "blocked_slots",
                                                                         "latest_acceptable_arrival")}
                                              for r, d in published.items()},
            }
            continue

        yield {"type": "balance", "turn": rnd, "result": mediated}
        turn_log["events"].append({
            "type": "agent_response", "turn": rnd,
            "response": {
                "from": "mediator", "turn": rnd, "vote": "BALANCED",
                "rationale": (
                    f"{mediated['counts']['options']} options: {mediated['counts']['unsafe_real_check']} unsafe, "
                    f"{mediated['counts']['declared_blocked']} declared blocked, "
                    f"{mediated['counts']['rejected_before']} rejected before, {mediated['allowed']} allowed. "
                    f"Picked {_p(mediated['proposal'])} ({mediated['rule']}): lowest score "
                    f"{mediated['min_score']}, total {mediated['total_score']}."
                ),
                "utility_fields": mediated["scores"],
            },
        })

        # 4. Parallel reviews.
        try:
            reviews = agents_balanced.run_parallel("review", state, rnd, mediated, dict(published))
        except agents.AgentCallError as e:
            yield from _stop_with_error(log, rnd, str(e))
            return

        for role in ROLES:
            r = reviews[role]
            r["turn"] = rnd
            r["utility_fields"] = {"score": mediated["scores"][role]}
            r["concession"] = r["vote"] == "ACCEPT" and mediated["scores"][role] < rules.accept_min(role, state)
            update = agents_balanced.declaration_of(r)
            if update:  # objectors (or anyone) may correct their published declaration
                published[role] = {**published[role], **update}
                r["declaration_update"] = update
            ev = {"type": "agent_response", "turn": rnd, "response": r}
            turn_log["events"].append(ev)
            turn_log["votes"].append(r)
            yield ev

        # 5. Consensus gate (the same mediator.evaluate_turn as the sequential pipeline).
        final_ok, final_violations = balancer.check(mediated["proposal"], state)
        verdict = mediator.evaluate_turn(turn_log["votes"], final_ok)
        summary = {
            "type": "turn_summary", "turn": rnd, "final_proposal": dict(mediated["proposal"]),
            "final_constraint_ok": final_ok, "final_violations": final_violations,
            "scores": mediated["scores"], "mediator": verdict,
        }
        turn_log["summary"] = summary
        log["turns"].append(turn_log)
        yield summary
        turn_history.append({"turn": rnd, "final_proposal": dict(mediated["proposal"]), "final_constraint_ok": final_ok})

        if verdict["consensus"]:
            outcome = {
                "status": "consensus", "turn_reached": rnd, "proposal": dict(mediated["proposal"]),
                "scores": mediated["scores"], "trade_offs": mediated["trade_offs"],
                "concessions": [r for r in ROLES if reviews[r]["concession"]],
            }
            break

        excluded.append(dict(mediated["proposal"]))
        objections = [reviews[r] for r in ROLES if reviews[r]["vote"] == "OBJECT"]
        previous_round = {
            "rejected_option": mediated["proposal"],
            "scores": mediated["scores"],
            "accepted_by": [r for r in ROLES if reviews[r]["vote"] == "ACCEPT"],
            "objections": [
                {k: o.get(k) for k in ("from", "objection_reason", "rationale", "revised_proposal", "i_need")}
                for o in objections
            ],
        }

    if outcome is None:
        outcome = _fallback(published, state, turn_history)

    log["outcome"] = outcome
    log["published_declarations"] = published
    _save(log)
    yield {"type": "done", "log": log}


def _fallback(published: dict, state: dict, turn_history: list[dict]) -> dict:
    """No AI. Mediate once more with every option allowed again; if nothing is safe, escalate."""
    best = balancer.mediate(published, state) if published else {"found": False}
    if best["found"]:
        detail = {
            "source": "mediator_final_pick",
            "proposal": best["proposal"],
            "scores": best["scores"],
            "reason": (
                f"No consensus within {MAX_ROUNDS} rounds. The mediator picked the safe option that best "
                f"fits every agent's published declaration ({best['rule']}, lowest score {best['min_score']})."
            ),
            "constraint_check": {"passed": True, "violations": []},
        }
    else:
        detail = fallback.resolve(state, turn_history)
    return {"status": "fallback", "turn_reached": MAX_ROUNDS, "proposal": detail["proposal"], "fallback_detail": detail}


def _stop_with_error(log: dict, rnd: int, message: str):
    role = message.split(":", 1)[0]
    log["outcome"] = {"status": "error", "message": message, "turn": rnd, "role": role}
    _save(log)
    yield {"type": "error", "turn": rnd, "role": role, "message": message}
    yield {"type": "done", "log": log}
