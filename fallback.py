"""Deterministic rule-based fallback. No LLM call.

Fires only when 3 turns have elapsed with no consensus. Picks a safe, conservative
outcome and records why, rather than leaving the negotiation unresolved.
"""

from __future__ import annotations

import constraints
from scenario import hhmm


def resolve(state: dict, turn_history: list[dict]) -> dict:
    """turn_history: list of per-turn records, each with 'final_proposal' and
    'final_constraint_ok' (see orchestrator). Returns the fallback outcome dict."""

    # 1. Prefer the most recent proposal across all turns that was constraint-valid,
    #    even if it lacked unanimous ACCEPT.
    for turn_record in reversed(turn_history):
        if turn_record["final_constraint_ok"]:
            return {
                "source": "last_constraint_valid_proposal",
                "proposal": turn_record["final_proposal"],
                "reason": (
                    f"No unanimous consensus within 3 turns. Falling back to the last "
                    f"constraint-valid proposal, from turn {turn_record['turn']}."
                ),
            }

    # 2. No proposal from the negotiation was ever constraint-valid: deterministically search
    #    for *some* safe outcome rather than blindly picking a fixed offset that might itself
    #    violate a constraint (e.g. a flat "+15min" buffer can still bust the fuel margin).
    search_window_minutes = 60
    candidate_gates = sorted(state["gate_occupancy"].keys())
    for extra in range(0, search_window_minutes + 1):
        candidate_arrival = state["naive_arrival_minutes"] + extra
        for gate in candidate_gates:
            candidate = {
                "revised_arrival_time": hhmm(candidate_arrival),
                "gate_assignment": gate,
            }
            ok, violations = constraints.check(candidate, state)
            if ok:
                return {
                    "source": "deterministic_safe_default_search",
                    "proposal": candidate,
                    "reason": (
                        "No constraint-valid proposal was produced in any of the 3 turns. "
                        f"Deterministically searched +0..{search_window_minutes}min from the naive "
                        f"arrival across known gates and found a constraint-valid outcome "
                        f"({extra}min added, gate {gate})."
                    ),
                    "constraint_check": {"passed": True, "violations": []},
                }

    # 3. Nothing in the search window was ever valid - escalate rather than silently pick
    #    something unsafe. Still returns a concrete best-effort proposal, but flags it clearly.
    free_gate = next((g for g, windows in state["gate_occupancy"].items() if not windows), candidate_gates[0])
    fallback_arrival = state["naive_arrival_minutes"] + 15
    fallback_proposal = {
        "revised_arrival_time": hhmm(fallback_arrival),
        "gate_assignment": free_gate,
    }
    ok, violations = constraints.check(fallback_proposal, state)
    return {
        "source": "needs_human_review",
        "proposal": fallback_proposal,
        "reason": (
            f"No constraint-valid outcome exists within +0..{search_window_minutes}min of the naive "
            "arrival on any known gate. This scenario needs human review - returning a best-effort "
            "proposal that is NOT guaranteed to be constraint-valid."
        ),
        "constraint_check": {"passed": ok, "violations": violations},
    }
