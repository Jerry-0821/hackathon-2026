"""Deterministic, non-AI mediator. No LLM call.

Consensus is a strict gate: all 3 agents must have voted ACCEPT on the same
(unchanged) proposal within the turn, AND that proposal must pass the constraint
validator. There is no partial-credit / averaged path to consensus.
"""

from __future__ import annotations


def evaluate_turn(votes: list[dict], final_constraint_ok: bool) -> dict:
    all_accept = all(v["vote"] == "ACCEPT" for v in votes)
    consensus = all_accept and final_constraint_ok

    reasons = []
    if not all_accept:
        countering = [v["from"] for v in votes if v["vote"] != "ACCEPT"]
        reasons.append(f"not all agents accepted the same proposal (dissent: {countering})")
    if all_accept and not final_constraint_ok:
        reasons.append(
            "all agents voted ACCEPT, but the deterministic constraint validator vetoed the "
            "proposal - safety/ops limits override unanimous agent agreement"
        )

    return {
        "consensus": consensus,
        "reasons": reasons,
    }
