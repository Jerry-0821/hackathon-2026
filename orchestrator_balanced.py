"""Parallel-proposal pipeline: an alternative to orchestrator.py, kept side by side to compare.

Each round (hard cap of 3):
  1. All three agents propose at the same time (agents_balanced.run_parallel).
  2. Every proposal is checked against red lines / hard rules (balancer.check, no AI).
  3. The balancer (no AI) searches around the proposals, drops unsafe options, scores the
     rest and picks the option where the worst-off agent is best off.
  4. All three agents ACCEPT or OBJECT to that balanced option, at the same time.
  5. Consensus = all ACCEPT + balanced option passes every rule (mediator.evaluate_turn).
     Otherwise the objections feed the next round.
After 3 rounds with no consensus, the balancer's full search is the deterministic fallback;
if even that finds nothing safe, fallback.resolve() escalates to human review.

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


def run(scenario_id: str = scenario.DEFAULT_SCENARIO, fuel_emergency: bool = False):
    """Generator yielding event dicts. The final event has type 'done' and carries the log."""
    state = scenario.initial_state(scenario_id)
    if fuel_emergency:
        rules.apply_fuel_emergency(state)

    log = {
        "pipeline": "parallel_proposals_balanced",
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

    previous_round = None
    revisions = []  # objectors' revised proposals from the last round
    turn_history = []
    outcome = None

    for rnd in range(1, MAX_ROUNDS + 1):
        turn_log = {"turn": rnd, "events": [], "votes": []}
        yield {"type": "turn_start", "turn": rnd, "proposal": None}

        # 1 + 2. Parallel proposals, each checked by code.
        try:
            proposals = agents_balanced.run_parallel("propose", state, rnd, previous_round)
        except agents.AgentCallError as e:
            yield from _stop_with_error(log, rnd, str(e))
            return

        for role in ROLES:
            p = proposals[role]
            ok, violations = balancer.check(p["proposal"], state)
            validation = {
                "type": "validation", "turn": rnd, "role": role, "proposal": p["proposal"],
                "passed": ok, "violations": violations, "violation_tiers": rules.classify(violations),
            }
            response = {
                **p, "turn": rnd, "vote": "PROPOSE",
                "proposal_check": {"passed": ok, "violations": rules.classify(violations)},
                "utility_fields": {"proposed": p["proposal"]},
            }
            for ev in (validation, {"type": "agent_response", "turn": rnd, "response": response}):
                turn_log["events"].append(ev)
                yield ev

        # 3. Balancer.
        by_role = {r: proposals[r]["proposal"] for r in ROLES}
        balanced = balancer.balance(by_role, state, extra_proposals=revisions)
        if not balanced["found"]:  # nothing safe near the proposals - widen the search
            balanced = balancer.balance(by_role, state, window_minutes=balancer.FALLBACK_WINDOW_MINUTES)
        if not balanced["found"]:
            turn_log["summary"] = {"type": "turn_summary", "turn": rnd, "final_proposal": None,
                                   "final_constraint_ok": False, "final_violations": [],
                                   "mediator": {"consensus": False, "reasons": ["no safe option exists"]}}
            log["turns"].append(turn_log)
            yield turn_log["summary"]
            break

        balance_event = {"type": "balance", "turn": rnd, "result": balanced}
        yield balance_event
        turn_log["events"].append({
            "type": "agent_response", "turn": rnd,
            "response": {
                "from": "balancer", "turn": rnd, "vote": "BALANCED",
                "rationale": (
                    f"Searched {balanced['candidates_checked']} options, {balanced['safe_candidates']} safe. "
                    f"Picked {balanced['proposal']['revised_arrival_time']} at "
                    f"{balanced['proposal']['gate_assignment']}: lowest agent score "
                    f"{balanced['min_score']}, total {balanced['total_score']}."
                ),
                "utility_fields": balanced["scores"],
            },
        })

        # 4. Parallel reviews.
        try:
            reviews = agents_balanced.run_parallel("review", state, rnd, balanced, proposals)
        except agents.AgentCallError as e:
            yield from _stop_with_error(log, rnd, str(e))
            return

        for role in ROLES:
            r = reviews[role]
            r["turn"] = rnd
            r["utility_fields"] = {"score": balanced["scores"][role]}
            r["concession"] = r["vote"] == "ACCEPT" and balanced["scores"][role] < rules.accept_min(role, state)
            ev = {"type": "agent_response", "turn": rnd, "response": r}
            turn_log["events"].append(ev)
            turn_log["votes"].append(r)
            yield ev

        # 5. Consensus gate (deterministic, the same mediator as the sequential pipeline).
        final_ok, final_violations = balancer.check(balanced["proposal"], state)
        verdict = mediator.evaluate_turn(turn_log["votes"], final_ok)
        summary = {
            "type": "turn_summary", "turn": rnd, "final_proposal": dict(balanced["proposal"]),
            "final_constraint_ok": final_ok, "final_violations": final_violations,
            "scores": balanced["scores"], "mediator": verdict,
        }
        turn_log["summary"] = summary
        log["turns"].append(turn_log)
        yield summary
        turn_history.append({"turn": rnd, "final_proposal": dict(balanced["proposal"]), "final_constraint_ok": final_ok})

        if verdict["consensus"]:
            outcome = {
                "status": "consensus", "turn_reached": rnd, "proposal": dict(balanced["proposal"]),
                "scores": balanced["scores"], "trade_offs": balanced["trade_offs"],
                "concessions": [r for r in ROLES if reviews[r]["concession"]],
            }
            break

        objections = [reviews[r] for r in ROLES if reviews[r]["vote"] == "OBJECT"]
        revisions = [o["revised_proposal"] for o in objections if o.get("revised_proposal")]
        previous_round = {
            "balanced_proposal": balanced["proposal"],
            "scores": balanced["scores"],
            "accepted_by": [r for r in ROLES if reviews[r]["vote"] == "ACCEPT"],
            "objections": [
                {k: o.get(k) for k in ("from", "rationale", "revised_proposal", "my_red_lines", "i_need")}
                for o in objections
            ],
        }

    if outcome is None:
        outcome = _fallback(state, turn_history)

    log["outcome"] = outcome
    _save(log)
    yield {"type": "done", "log": log}


def _fallback(state: dict, turn_history: list[dict]) -> dict:
    """No AI. Full balancer search first; if nothing is safe, the original escalation path."""
    best = balancer.balance({}, state, window_minutes=balancer.FALLBACK_WINDOW_MINUTES)
    if best["found"]:
        detail = {
            "source": "balancer_full_search",
            "proposal": best["proposal"],
            "scores": best["scores"],
            "reason": (
                f"No consensus within {MAX_ROUNDS} rounds. Searched every known gate from the earliest "
                f"possible arrival to +{balancer.FALLBACK_WINDOW_MINUTES}min and picked the safe option "
                f"where the worst-off agent is best off (lowest score {best['min_score']})."
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
