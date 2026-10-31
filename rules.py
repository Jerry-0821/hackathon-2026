"""Rule tiers for the negotiation: red lines, hard rules, and soft preferences.

- RED LINE  - safety or physics. Never relaxed, not even in an emergency.
- HARD RULE - regulation / company policy. Relaxed only in a declared emergency, and the
              relaxation is written to the log.
- SOFT      - preferences. Scored 0-10 per agent in scoring.py and open to trade-offs.

constraints.py does the actual checking (deterministic, no AI). This module only says
which tier each rule belongs to, who owns it, and what changes in an emergency.
"""

from __future__ import annotations

RED_LINE = "red_line"
HARD_RULE = "hard_rule"

# rule name (the text before ":" in a constraints.py violation) -> (tier, owning agent)
RULES = {
    "wake_separation": (RED_LINE, "atc"),
    "earliest_arrival": (RED_LINE, "atc"),
    "fuel_reserve": (RED_LINE, "pilot"),
    "gate_conflict": (RED_LINE, "ground"),
    "unknown_gate": (RED_LINE, "ground"),
    "bad_format": (RED_LINE, "unknown"),
    "crew_overtime": (HARD_RULE, "ground"),
}

# Minimum satisfaction score (scoring.py) an agent should hold out for before it ACCEPTs.
ACCEPT_MIN_SCORE = {"atc": 6, "pilot": 6, "ground": 6}

# Fuel emergency: extra holding burned fuel, so the flight lands with less reserve.
EMERGENCY_FUEL_RESERVE_MINUTES = 36
EMERGENCY_RELAXED_LIMITS = {"max_crew_overtime_minutes": 45}
# In an emergency ATC and Ground must yield their preferences to the aircraft in trouble.
EMERGENCY_ACCEPT_MIN_SCORE = {"atc": 0, "pilot": 6, "ground": 0}

SOFT_PREFERENCES = {
    "atc": "little holding for this flight, and a slot that is not squeezed between other arrivals (throughput)",
    "pilot": "fuel margin well above the minimum, and arriving in time for the 18 connecting passengers",
    "ground": "a crew that is ready when the aircraft arrives, little crew overtime, and the gate closest to the crew base",
}


def classify(violations: list[str]) -> list[dict]:
    """Turn constraints.py violation strings into {rule, tier, owner, detail} records."""
    out = []
    for v in violations:
        name = v.split(":", 1)[0]
        tier, owner = RULES.get(name, (RED_LINE, "unknown"))  # unknown rules are treated as red lines
        out.append({"rule": name, "tier": tier, "owner": owner, "detail": v})
    return out


def apply_fuel_emergency(state: dict) -> dict:
    """Mutate a fresh scenario state into a fuel emergency and record what was relaxed."""
    derived = state["derived"]
    derived["fuel_reserve_minutes_at_naive_arrival"] = min(
        derived["fuel_reserve_minutes_at_naive_arrival"], EMERGENCY_FUEL_RESERVE_MINUTES
    )
    relaxed = {}
    for key, value in EMERGENCY_RELAXED_LIMITS.items():
        relaxed[key] = {"from": state["constraints"].get(key), "to": value}
        state["constraints"][key] = value
    state["emergency"] = {
        "type": "fuel",
        "declared_by": "pilot",
        "reason": f"extra holding burned fuel - reserve at naive arrival is only "
        f"{derived['fuel_reserve_minutes_at_naive_arrival']}min",
        "relaxed_hard_rules": relaxed,
    }
    return state


def accept_min(role: str, state: dict) -> int:
    table = EMERGENCY_ACCEPT_MIN_SCORE if state.get("emergency") else ACCEPT_MIN_SCORE
    return table[role]


def role_brief(role: str, state: dict) -> str:
    """Extra system-prompt text telling one agent which of its limits are negotiable."""
    c = state["constraints"]
    mine = [name for name, (_, owner) in RULES.items() if owner == role]
    red = [n for n in mine if RULES[n][0] == RED_LINE]
    hard = [n for n in mine if RULES[n][0] == HARD_RULE]
    lines = [
        "Your limits come in three tiers:",
        f"- RED LINES (never give these up, not even in an emergency): {', '.join(red) or 'none'}.",
        f"- HARD RULES (only relaxed in a declared emergency): {', '.join(hard) or 'none'}.",
        f"- SOFT PREFERENCES (you may trade these away): {SOFT_PREFERENCES[role]}.",
        f"Current limits: wake separation >= {c['min_wake_separation_minutes']}min, "
        f"fuel reserve >= {c['min_fuel_reserve_minutes']}min, crew overtime <= "
        f"{c['max_crew_overtime_minutes']}min, an arriving aircraft holds its gate for 25min.",
        "Your satisfaction score 0-10 is computed by code from the numbers, not by you. "
        f"Your minimum acceptable score is {accept_min(role, state)}. "
        "Always state my_red_lines, i_can_give and i_need - the other agents cannot see your data.",
    ]
    if state.get("emergency"):
        e = state["emergency"]
        lines.append(
            f"EMERGENCY DECLARED by {e['declared_by']}: {e['reason']}. Red lines still hold. "
            "Give the aircraft in trouble priority over your soft preferences."
        )
    return "\n".join(lines)
