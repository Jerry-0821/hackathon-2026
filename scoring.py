"""Deterministic satisfaction scores (0-10) for each agent's SOFT preferences. No AI.

Red lines and hard rules are pass/fail in constraints.py. These scores only measure how
good a safe proposal is for each agent, so the agents can see how much they are giving up
and the log can show who conceded what. Same proposal -> same score, every run.

Optional per-scenario fields in scenario "derived" (defaults keep older scenarios working):
  crew_ready_offset           - minutes after naive arrival the ground crew is free (default 0)
  ground_preferred_gate       - gate nearest the crew base (default: current_gate)
  connection_cutoff_offsets   - {gate: minutes after naive arrival connecting passengers can
                                still make their flights, "default": ...}
"""

from __future__ import annotations

from scenario import minutes_since_midnight

ROLES = ("atc", "pilot", "ground")


def _clamp(x: float) -> float:
    return round(max(0.0, min(10.0, x)), 1)


def _facts(proposal: dict, state: dict) -> dict:
    arrival = minutes_since_midnight(proposal["revised_arrival_time"])
    gate = proposal["gate_assignment"]
    derived = state["derived"]
    naive = state["naive_arrival_minutes"]
    holding = max(arrival - naive, 0)

    gaps = [abs(arrival - o["arrival_minutes"]) for o in state["other_arrivals"]]
    sep_margin = (min(gaps) - state["constraints"]["min_wake_separation_minutes"]) if gaps else 99

    cutoffs = derived.get("connection_cutoff_offsets", {"default": 6, derived["requested_gate"]: 12})
    connection_cutoff = naive + cutoffs.get(gate, cutoffs["default"])

    crew_ready = naive + derived.get("crew_ready_offset", 0)
    return {
        "holding_minutes": holding,
        "wake_separation_margin_minutes": sep_margin,
        "fuel_margin_minutes": derived["fuel_reserve_minutes_at_naive_arrival"] - holding,
        "connections_made": arrival <= connection_cutoff,
        "crew_wait_minutes": max(crew_ready - arrival, 0),
        "overtime_minutes": max(arrival - derived["ground_crew_shift_end_minutes"], 0),
        "preferred_gate": gate == derived.get("ground_preferred_gate", derived["current_gate"]),
    }


def score(role: str, proposal: dict, state: dict) -> dict:
    """Returns {"score": 0-10, "breakdown": {...}} for one agent."""
    f = _facts(proposal, state)
    if role == "atc":
        squeeze = 2 if f["wake_separation_margin_minutes"] <= 0 else 1 if f["wake_separation_margin_minutes"] == 1 else 0
        penalties = {"holding (-1 per 4min)": f["holding_minutes"] / 4, "squeezed slot": squeeze}
    elif role == "pilot":
        penalties = {
            "missed connections": 0 if f["connections_made"] else 4,
            "fuel margin below 45min (-1 per 3min)": max(45 - f["fuel_margin_minutes"], 0) / 3,
        }
    elif role == "ground":
        penalties = {
            "crew not ready (-1 per 2min wait)": f["crew_wait_minutes"] / 2,
            "overtime (-1 per 5min)": f["overtime_minutes"] / 5,
            "not the preferred gate": 0 if f["preferred_gate"] else 2,
        }
    else:
        raise ValueError(f"unknown role {role!r}")
    total = _clamp(10 - sum(penalties.values()))
    return {"score": total, "breakdown": {k: round(v, 1) for k, v in penalties.items() if v}, "facts": f}


def all_scores(proposal: dict, state: dict) -> dict:
    return {role: score(role, proposal, state)["score"] for role in ROLES}
