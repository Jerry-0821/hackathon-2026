"""Rule tiers for the runway-closure cascade. Deterministic, no LLM.

RED LINES are built into solver.py: every option already respects runway rate and wake separation,
never lands during a full closure, lands above final reserve fuel (or diverts), and uses only open,
free, size-compatible gates. solver.verify() re-checks them. They are never relaxed.

HARD RULES are policy limits, one per role. They are checked here on every option and relaxed only
(a) in a Minimum Fuel emergency, or (b) when no option satisfies the rule at all. Each relaxation is
recorded with its reason.

SOFT preferences are the roles' metrics, scored by mediation.py from each agent's declared priorities.
"""

from __future__ import annotations

RED_LINES = [
    "runway rate and wake separation between landings",
    "no landing while every arrival runway is closed",
    "every flight lands above final reserve fuel, or diverts",
    "gates must be open, free and size-compatible",
]

# (role, metric computed by option_value, "max"/"min", limit, description)
HARD_RULES = [
    ("atc", "max_holding_min", "max", 30, "airborne holding per flight"),
    ("pilot", "min_fuel_margin_min", "min", 5, "lowest fuel margin above final reserve (company buffer)"),
    ("ground", "crew_overtime_min", "max", 30, "ground crew overtime"),
]

MIN_FUEL_TRIGGER_MIN = 5  # Minimum Fuel if even the best plan leaves a flight at or below this margin
EMERGENCY_LIMITS = {"min_fuel_margin_min": 0, "crew_overtime_min": 45}


def option_value(option: dict, metric: str) -> int:
    if metric == "max_holding_min":
        return max((r["holding_min"] for r in option["rows"] if not r["diverted"]), default=0)
    return option["metrics"][metric]


def _breaks(value: int, kind: str, limit: int) -> bool:
    return value > limit if kind == "max" else value < limit


def detect_minimum_fuel(options: list[dict]) -> dict | None:
    """Minimum Fuel: even the best safe plan (fewest diversions, then most fuel) leaves a flight
    at or below MIN_FUEL_TRIGGER_MIN above final reserve."""
    fewest = min(o["metrics"]["diversions"] for o in options)
    best = max((o for o in options if o["metrics"]["diversions"] == fewest),
               key=lambda o: o["metrics"]["min_fuel_margin_min"])
    margin = best["metrics"]["min_fuel_margin_min"]
    if margin > MIN_FUEL_TRIGGER_MIN:
        return None
    flights = [r["flight"] for r in best["rows"] if not r["diverted"] and r["fuel_margin_min"] == margin]
    return {
        "type": "minimum_fuel",
        "declared_by": "pilot",
        "declared_automatically": True,
        "flights": flights,
        "reason": f"even the best safe plan lands {', '.join(flights)} only {margin} min above final reserve "
                  f"(trigger: {MIN_FUEL_TRIGGER_MIN} min)",
    }


def evaluate(options: list[dict], emergency: dict | None = None) -> dict:
    """Apply the hard rules to every option.
    Returns {"limits": [...], "relaxed": [...], "violations": {option_id: [...]}}."""
    limits, relaxed = [], []
    for role, metric, kind, limit, desc in HARD_RULES:
        rule = {"role": role, "metric": metric, "kind": kind, "limit": limit, "description": desc}
        if emergency and metric in EMERGENCY_LIMITS:
            relaxed.append({**rule, "to": EMERGENCY_LIMITS[metric], "reason": "Minimum Fuel emergency"})
            rule = {**rule, "limit": EMERGENCY_LIMITS[metric]}
        if all(_breaks(option_value(o, metric), kind, rule["limit"]) for o in options):
            best = (min if kind == "max" else max)(option_value(o, metric) for o in options)
            relaxed.append({**rule, "to": best, "reason": f"no safe option meets it; the best possible is {best}"})
            rule = {**rule, "limit": best}
        limits.append(rule)

    violations = {}
    for o in options:
        violations[o["id"]] = [
            {"role": r["role"], "rule": r["metric"],
             "detail": f"{r['description']}: {option_value(o, r['metric'])} "
                       f"({'max' if r['kind'] == 'max' else 'min'} {r['limit']})"}
            for r in limits if _breaks(option_value(o, r["metric"]), r["kind"], r["limit"])
        ]
    return {"limits": limits, "relaxed": relaxed, "violations": violations}


def checks_for(role: str, hard: dict) -> dict:
    """One role's own hard-rule check per option, as shown to that agent."""
    mine = [r for r in hard["limits"] if r["role"] == role]
    return {
        "my_hard_rules": [f"{r['description']} {'<=' if r['kind'] == 'max' else '>='} {r['limit']}" for r in mine],
        "relaxed": [r for r in hard["relaxed"] if r["role"] == role],
        "breaks_my_hard_rules": {oid: [v["detail"] for v in vs if v["role"] == role]
                                 for oid, vs in hard["violations"].items()},
    }
