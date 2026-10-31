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

    # 2. No proposal was ever constraint-valid: hardcoded safe default.
    free_gate = next(
        (g for g, windows in state["gate_occupancy"].items() if not windows),
        "D12",
    )
    safe_arrival_minutes = state["naive_arrival_minutes"] + 15  # fixed 15min safety buffer
    safe_proposal = {
        "revised_arrival_time": hhmm(safe_arrival_minutes),
        "gate_assignment": free_gate,
    }
    ok, violations = constraints.check(safe_proposal, state)
    return {
        "source": "hardcoded_safe_default",
        "proposal": safe_proposal,
        "reason": (
            "No constraint-valid proposal was produced in any of the 3 turns. Falling back to "
            "scheduled arrival + weather delay + 15min safety buffer, on the first known-free gate."
        ),
        "constraint_check": {"passed": ok, "violations": violations},
    }
