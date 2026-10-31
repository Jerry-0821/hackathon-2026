"""Mediator for the parallel-proposal pipeline. Deterministic, no AI.

1. Options: every minute from the earliest possible arrival to +60min x every real gate.
2. Remove options that fail the real safety check (red lines / hard rules, real data), that any
   agent DECLARED blocked, or that were rejected in an earlier round.
3. Score each option per agent from that agent's DECLARED soft preferences (0-10).
4. Pick the option where the worst-off agent scores highest (pick_balanced).

The mediator never reads an agent's private data for scoring - only what the agent published.
Safety is still checked with the real data, so a wrong declaration can't let an unsafe option through.
"""

from __future__ import annotations

import re

import constraints
import rules
from scenario import hhmm, minutes_since_midnight

SEARCH_WINDOW_MINUTES = 60
WEIGHT = {"high": 2, "low": 1}
MINUTE_PENALTY = 0.5  # per minute outside the preferred window, times weight
GATE_PENALTY = 2.0  # not the preferred gate, times weight
PROPOSAL_WINDOW_PAD = 2  # no window declared -> own proposed time +/- 2min is the ideal

_SLOT_RE = re.compile(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})(?:\s+([A-Za-z]\w*))?")


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


def _time(value) -> int | None:
    try:
        return minutes_since_midnight(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_declaration(decl: dict, state: dict) -> dict:
    """Turn an agent's published strings into numbers. Unparsable parts become warnings."""
    warnings = []
    blocked = []
    for m in _SLOT_RE.finditer(decl.get("blocked_slots") or ""):
        start, end, gate = _time(m.group(1)), _time(m.group(2)), m.group(3)
        if start is None or end is None or end < start:
            warnings.append(f"ignored blocked slot {m.group(0)!r}")
            continue
        blocked.append((start, end, None if not gate or gate.lower() == "any" else gate))

    latest = _time(decl["latest_acceptable_arrival"]) if decl.get("latest_acceptable_arrival") else None
    if decl.get("latest_acceptable_arrival") and latest is None:
        warnings.append(f"ignored latest_acceptable_arrival {decl['latest_acceptable_arrival']!r}")

    window = None
    m = _SLOT_RE.search(decl.get("preferred_window") or "")
    if m and _time(m.group(1)) is not None and _time(m.group(2)) is not None:
        window = (_time(m.group(1)), _time(m.group(2)))
    else:
        own = _time((decl.get("proposal") or {}).get("revised_arrival_time"))
        if own is not None:
            window = (own - PROPOSAL_WINDOW_PAD, own + PROPOSAL_WINDOW_PAD)
        if decl.get("preferred_window"):
            warnings.append(f"ignored preferred_window {decl['preferred_window']!r}")

    # A gate preference only counts if declared - many agents (e.g. ATC) don't care about gates.
    gate = decl.get("preferred_gate")
    if gate and gate not in state["gate_occupancy"]:
        warnings.append(f"ignored unknown preferred_gate {gate!r}")
        gate = None

    return {
        "blocked": blocked,
        "latest": latest,
        "window": window,
        "window_weight": WEIGHT.get(str(decl.get("preferred_window_importance", "low")).lower(), 1),
        "gate": gate,
        "gate_weight": WEIGHT.get(str(decl.get("preferred_gate_importance", "low")).lower(), 1),
        "warnings": warnings,
    }


def declared_block(option: dict, parsed: dict) -> str | None:
    t, g = minutes_since_midnight(option["revised_arrival_time"]), option["gate_assignment"]
    if parsed["latest"] is not None and t > parsed["latest"]:
        return f"after declared latest arrival {hhmm(parsed['latest'])}"
    for start, end, gate in parsed["blocked"]:
        if start <= t <= end and (gate is None or gate == g):
            return f"declared blocked {hhmm(start)}-{hhmm(end)} {gate or 'any gate'}"
    return None


def declared_score(option: dict, parsed: dict) -> dict:
    t, g = minutes_since_midnight(option["revised_arrival_time"]), option["gate_assignment"]
    penalties = {}
    if parsed["window"]:
        lo, hi = parsed["window"]
        off = lo - t if t < lo else t - hi if t > hi else 0
        if off:
            penalties[f"{off}min outside preferred {hhmm(lo)}-{hhmm(hi)}"] = off * MINUTE_PENALTY * parsed["window_weight"]
    if parsed["gate"] and g != parsed["gate"]:
        penalties[f"not preferred gate {parsed['gate']}"] = GATE_PENALTY * parsed["gate_weight"]
    score = round(max(0.0, 10.0 - sum(penalties.values())), 1)
    return {"score": score, "breakdown": {k: round(v, 1) for k, v in penalties.items()}}


def pick_balanced(scored: list[dict], emergency: bool = False) -> dict:
    """The balance rule, in one place so it is easy to change.
    Normal: highest lowest-score, then highest total, then earliest arrival.
    Emergency: the pilot (aircraft in trouble) first, then the normal rule."""
    def key(c):
        s = c["scores"]
        base = (min(s.values()), sum(s.values()), -minutes_since_midnight(c["proposal"]["revised_arrival_time"]))
        return ((s.get("pilot", 0),) + base) if emergency else base
    return max(scored, key=key)


def mediate(declarations: dict, state: dict, excluded: list[dict] = ()) -> dict:
    """declarations: {role: {"proposal": {...}, "blocked_slots": "...", ...}} as published by the agents."""
    parsed = {role: parse_declaration(d, state) for role, d in declarations.items()}
    naive = state["naive_arrival_minutes"]
    excluded_keys = {(e["revised_arrival_time"], e["gate_assignment"]) for e in excluded}

    counts = {"options": 0, "unsafe_real_check": 0, "declared_blocked": 0, "rejected_before": 0}
    scored = []
    for t in range(naive, naive + SEARCH_WINDOW_MINUTES + 1):
        for g in sorted(state["gate_occupancy"]):
            option = {"revised_arrival_time": hhmm(t), "gate_assignment": g}
            counts["options"] += 1
            if not check(option, state)[0]:
                counts["unsafe_real_check"] += 1
            elif any(declared_block(option, p) for p in parsed.values()):
                counts["declared_blocked"] += 1
            elif (option["revised_arrival_time"], g) in excluded_keys:
                counts["rejected_before"] += 1
            else:
                detail = {r: declared_score(option, p) for r, p in parsed.items()}
                scored.append({"proposal": option, "scores": {r: d["score"] for r, d in detail.items()}, "detail": detail})

    result = {"counts": counts, "allowed": len(scored), "parse_warnings": {r: p["warnings"] for r, p in parsed.items() if p["warnings"]}}
    if not scored:
        return {**result, "found": False}

    best = pick_balanced(scored, emergency=bool(state.get("emergency")))
    trade_offs = []
    for role, d in declarations.items():
        own = d.get("proposal") or {}
        own_ok = check(own, state)[0] if own else False
        own_score = declared_score(own, parsed[role])["score"] if own_ok else None
        trade_offs.append({
            "role": role,
            "own_proposal": own,
            "own_proposal_safe": own_ok,
            "score_on_own_proposal": own_score,
            "score_on_balanced": best["scores"][role],
            "gave_up": None if own_score is None else round(own_score - best["scores"][role], 1),
            "why": best["detail"][role]["breakdown"],
        })
    return {
        **result,
        "found": True,
        "proposal": best["proposal"],
        "scores": best["scores"],
        "min_score": min(best["scores"].values()),
        "total_score": round(sum(best["scores"].values()), 1),
        "below_minimum": [r for r, s in best["scores"].items() if s < rules.accept_min(r, state)],
        "trade_offs": trade_offs,
        "rule": "emergency: pilot first, then worst-off agent" if state.get("emergency") else "worst-off agent best off",
    }
