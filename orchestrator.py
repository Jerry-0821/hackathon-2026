"""The turn loop. ATC -> Pilot -> Ground, fixed order, hard cap of 3 turns.

Implemented as a generator so a caller (Streamlit app, or a plain script) can render
each event as it happens rather than waiting for the whole run to finish. Also builds
the full JSON consensus log (every message, every constraint check, every mediator
verdict) and writes it to disk at the end.
"""

import json
from datetime import datetime, timezone

import agents
import constraints
import fallback
import mediator
import scenario

ROLE_ORDER = ["atc", "pilot", "ground"]
MAX_TURNS = 3


def run():
    """Generator yielding event dicts as the negotiation proceeds. The final event has
    type 'done' and carries the full consensus log (also written to consensus_log.json)."""

    state = scenario.initial_state()
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

            response = agents.call_agent(role, state, current_proposal, constraint_result, turn)
            agent_event = {"type": "agent_response", "turn": turn, "response": response}
            turn_log["events"].append(agent_event)
            turn_log["votes"].append(response)
            yield agent_event

            if response["vote"] == "COUNTER":
                counter = response.get("counter_proposal")
                if counter and "revised_arrival_time" in counter and "gate_assignment" in counter:
                    current_proposal = counter
                else:
                    # malformed COUNTER with no usable proposal - treat as REJECT, keep baseline
                    response["vote"] = "REJECT"
                    response["rationale"] += " [no usable counter_proposal supplied - treated as REJECT]"

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
