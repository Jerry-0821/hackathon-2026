"""Deterministic balancer (no AI) for the parallel-proposal pipeline.

Takes the three agents' own proposals, builds candidate options around them, removes every
candidate that breaks a red line or hard rule, scores the rest with scoring.py (soft
preferences), and picks the balanced one: the option where the worst-off agent is as well
off as possible.
"""

from __future__ import annotations

import constraints
import rules
import scoring
from scenario import hhmm, minutes_since_midnight

SEARCH_PADDING_MINUTES = 5  # also look a little before/after the earliest/latest proposed time
FALLBACK_WINDOW_MINUTES = 60  # fallback search: naive arrival .. naive + 60 on every known gate


def check(proposal: dict, state: dict) -> tuple[bool, list[str]]:
    """constraints.check plus two checks it doesn't do itself: a malformed proposal and
    arriving before it is physically possible (scheduled arrival + the real delay)."""
    try:
        arrival = minutes_since_midnight(proposal["revised_arrival_time"])
    except (KeyError, TypeError, ValueError) as e:
        return False, [f"bad_format: {e}"]
    if not proposal.get("gate_assignment"):
        return False, ["bad_format: no gate_assignment"]

    ok, violations = constraints.check(proposal, state)
    violations = list(violations)
    naive = state["naive_arrival_minutes"]
    if arrival < naive:
        violations.append(
            f"earliest_arrival: {proposal['revised_arrival_time']} is {naive - arrival}min before "
            f"the earliest possible arrival {state['naive_arrival']}"
        )
    return (len(violations) == 0, violations)


def candidates(proposals: list[dict], state: dict, window_minutes: int | None = None) -> list[dict]:
    """Every minute between the proposed times (plus padding) x every gate someone proposed
    (plus the current gate). With window_minutes, search naive..naive+window on all gates."""
    naive = state["naive_arrival_minutes"]
    known_gates = set(state["gate_occupancy"])

    if window_minutes is not None:
        lo, hi, gates = naive, naive + window_minutes, known_gates
    else:
        times, gates = [], {state["derived"]["current_gate"]}
        for p in proposals:
            try:
                times.append(minutes_since_midnight(p["revised_arrival_time"]))
            except (KeyError, TypeError, ValueError):
                continue
            if p.get("gate_assignment") in known_gates:
                gates.add(p["gate_assignment"])
        if times:
            lo, hi = max(naive, min(times) - SEARCH_PADDING_MINUTES), max(times) + SEARCH_PADDING_MINUTES
        else:
            lo, hi = naive, naive + SEARCH_PADDING_MINUTES

    return [
        {"revised_arrival_time": hhmm(t), "gate_assignment": g}
        for t in range(lo, max(lo, hi) + 1)
        for g in sorted(gates)
    ]


def pick_balanced(scored: list[dict]) -> dict:
    """The balance rule, in one place so it is easy to change.
    Protect the weakest: highest minimum score, then highest total, then earliest arrival."""
    return max(
        scored,
        key=lambda c: (
            min(c["scores"].values()),
            sum(c["scores"].values()),
            -minutes_since_midnight(c["proposal"]["revised_arrival_time"]),
        ),
    )


def _trade_offs(proposals_by_role: dict, balanced_scores: dict, state: dict) -> list[dict]:
    """For each agent: its own proposal, whether that was safe, and how much it gives up."""
    out = []
    for role, own in proposals_by_role.items():
        ok, violations = check(own, state)
        own_score = None
        if not any(v.startswith("bad_format") for v in violations):
            own_score = scoring.score(role, own, state)["score"]
        out.append({
            "role": role,
            "own_proposal": own,
            "own_proposal_safe": ok,
            "own_proposal_violations": rules.classify(violations),
            "score_on_own_proposal": own_score,
            "score_on_balanced": balanced_scores[role],
            "gave_up": None if own_score is None else round(own_score - balanced_scores[role], 1),
        })
    return out


def balance(
    proposals_by_role: dict,
    state: dict,
    window_minutes: int | None = None,
    extra_proposals: list[dict] = (),
) -> dict:
    """proposals_by_role: {"atc": {...}, "pilot": {...}, "ground": {...}}
    extra_proposals: more options to search around (e.g. last round's objections)."""
    cands = candidates(list(proposals_by_role.values()) + list(extra_proposals), state, window_minutes)
    safe = []
    for c in cands:
        if check(c, state)[0]:
            safe.append({"proposal": c, "scores": scoring.all_scores(c, state)})

    result = {"candidates_checked": len(cands), "safe_candidates": len(safe)}
    if not safe:
        return {**result, "found": False}

    best = pick_balanced(safe)
    others = sorted(
        (c for c in safe if c is not best),
        key=lambda c: (min(c["scores"].values()), sum(c["scores"].values())),
        reverse=True,
    )[:3]
    return {
        **result,
        "found": True,
        "proposal": best["proposal"],
        "scores": best["scores"],
        "min_score": min(best["scores"].values()),
        "total_score": round(sum(best["scores"].values()), 1),
        "below_minimum": [r for r, s in best["scores"].items() if s < rules.accept_min(r, state)],
        "trade_offs": _trade_offs(proposals_by_role, best["scores"], state),
        "runner_ups": others,
    }
