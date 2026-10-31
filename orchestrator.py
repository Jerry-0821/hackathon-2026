"""The turn loop. ATC -> Pilot -> Ground, fixed order, hard cap of 3 turns.

Implemented as a generator so a caller (Streamlit app, or a plain script) can render
each event as it happens rather than waiting for the whole run to finish. Also builds
the full JSON consensus log (every message, every constraint check, every mediator
verdict) and writes it to disk at the end.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import agents
import constraints
import fallback
import mediator
import scenario

ROLE_ORDER = ["atc", "pilot", "ground"]
MAX_TURNS = 3


def _counter_problem(counter) -> str | None:
    """Return why a counter_proposal is unusable, or None if it is well-formed."""
    if not isinstance(counter, dict):
        return "no counter_proposal supplied"
    if not counter.get("revised_arrival_time") or not counter.get("gate_assignment"):
        return "missing revised_arrival_time or gate_assignment"
    try:
        scenario.minutes_since_midnight(str(counter["revised_arrival_time"]))
    except ValueError as e:
        return str(e)
    return None


def run(scenario_id: str = scenario.DEFAULT_SCENARIO):
    """Generator yielding event dicts as the negotiation proceeds. The final event has
    type 'done' and carries the full consensus log (also written to consensus_log.json)."""

    state = scenario.initial_state(scenario_id)
    current_proposal = {
        "revised_arrival_time": state["naive_arrival"],
        "gate_assignment": state["derived"]["current_gate"],
    }

    log = {
        "scenario": {
            "flight": state["flight"],
            "derived": state["derived"],
            "naive_arrival": state["naive_arrival"],
        },
        "turns": [],
        "outcome": None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    yield {"type": "scenario", "state": log["scenario"], "initial_proposal": current_proposal}

    turn_history = []
    vote_history = []  # every vote so far, shown to later agents so they can respond to objections
    outcome = None

    for turn in range(1, MAX_TURNS + 1):
        turn_log = {"turn": turn, "events": [], "votes": []}
        yield {"type": "turn_start", "turn": turn, "proposal": dict(current_proposal)}

        for role in ROLE_ORDER:
            constraint_result = constraints.check(current_proposal, state)
            ok, violations = constraint_result
            validation_event = {
                "type": "validation",
                "turn": turn,
                "proposal": dict(current_proposal),
                "passed": ok,
                "violations": violations,
            }
            turn_log["events"].append(validation_event)
            yield validation_event

            try:
                response = agents.call_agent(
                    role, state, current_proposal, constraint_result, turn, history=list(vote_history)
                )
            except agents.AgentCallError as e:
                log["outcome"] = {"status": "error", "message": str(e), "turn": turn, "role": role}
                with open("consensus_log.json", "w") as f:
                    json.dump(log, f, indent=2)
                yield {"type": "error", "turn": turn, "role": role, "message": str(e)}
                yield {"type": "done", "log": log}
                return

            # Process a COUNTER before yielding, so the UI and log show the final vote.
            if response["vote"] == "COUNTER":
                counter = response.get("counter_proposal")
                problem = _counter_problem(counter)
                if problem:
                    # Unusable COUNTER - treat as REJECT, keep baseline.
                    response["vote"] = "REJECT"
                    response["rationale"] += f" [counter_proposal not usable ({problem}) - treated as REJECT]"
                else:
                    counter = {
                        "revised_arrival_time": str(counter["revised_arrival_time"]).strip(),
                        "gate_assignment": str(counter["gate_assignment"]).strip(),
                    }
                    response["counter_proposal"] = counter
                    counter_ok, counter_violations = constraints.check(counter, state)
                    response["counter_constraint_check"] = {
                        "passed": counter_ok,
                        "violations": counter_violations,
                    }
                    if counter_ok:
                        current_proposal = counter
                    else:
                        # Only constraint-valid counters become the new baseline.
                        response["rationale"] += " [counter_proposal failed the constraint check - not adopted]"

            vote_history.append(
                {
                    "turn": turn,
                    "from": role,
                    "vote": response["vote"],
                    "rationale": response.get("rationale", ""),
                    "counter_proposal": response.get("counter_proposal"),
                    "counter_constraint_check": response.get("counter_constraint_check"),
                }
            )

            agent_event = {"type": "agent_response", "turn": turn, "response": response}
            turn_log["events"].append(agent_event)
            turn_log["votes"].append(response)
            yield agent_event

        final_ok, final_violations = constraints.check(current_proposal, state)
        mediator_result = mediator.evaluate_turn(turn_log["votes"], final_ok)

        turn_summary = {
            "type": "turn_summary",
            "turn": turn,
            "final_proposal": dict(current_proposal),
            "final_constraint_ok": final_ok,
            "final_violations": final_violations,
            "mediator": mediator_result,
        }
        turn_log["summary"] = turn_summary
        log["turns"].append(turn_log)
        yield turn_summary

        turn_history.append(
            {
                "turn": turn,
                "final_proposal": dict(current_proposal),
                "final_constraint_ok": final_ok,
            }
        )

        if mediator_result["consensus"]:
            outcome = {
                "status": "consensus",
                "turn_reached": turn,
                "proposal": dict(current_proposal),
            }
            break

    if outcome is None:
        fallback_result = fallback.resolve(state, turn_history)
        outcome = {
            "status": "fallback",
            "turn_reached": MAX_TURNS,
            "proposal": fallback_result["proposal"],
            "fallback_detail": fallback_result,
        }

    log["outcome"] = outcome
    with open("consensus_log.json", "w") as f:
        json.dump(log, f, indent=2)

    yield {"type": "done", "log": log}
