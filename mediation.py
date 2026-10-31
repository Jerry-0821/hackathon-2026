"""Mediator for the runway-closure cascade. Deterministic, no LLM.
Design: .plans/cascade-negotiation-design.md

The solver's options are all safe (red lines). The mediator does not decide - it recommends:
1. Score every option 0-10 for each agent from that agent's metrics, weighted by the priorities the
   agent DECLARED in turn 1 (high = 2, low = 1, none = 0). Diversions always weigh 3 for everyone.
2. Drop options that break a hard rule (cascade_rules), that someone vetoed, or that were rejected.
3. Prefer options every agent said it can accept.
4. Recommend the option where the worst-off agent scores highest (ties: higher total, then the
   solver's balanced cost). In a Minimum Fuel emergency the pilot's score comes first.
The agents then ACCEPT or OBJECT. Consensus = all three ACCEPT.
"""

from __future__ import annotations

ROLES = ["atc", "pilot", "ground"]

# Each role's own soft metrics: (metric, default weight, better). Diversions are added for everyone.
ROLE_METRICS = {
    "atc": [("total_delay_min", 2, "lower"), ("max_delay_min", 1, "lower")],
    "pilot": [("min_fuel_margin_min", 2, "higher"), ("pax_missed", 2, "lower")],
    "ground": [("downstream_delay_min", 2, "lower"), ("crew_overtime_min", 1, "lower"), ("gate_changes", 1, "lower")],
}
DIVERSION_WEIGHT = 3
PRIORITY_WEIGHT = {"high": 2, "low": 1, "none": 0}

# 10 = the best of these options for you, 0 = the worst. 5 = middle of the range.
ACCEPT_MIN_SCORE = 5


def default_priorities(role: str) -> dict:
    return {m: ("high" if w >= 2 else "low") for m, w, _ in ROLE_METRICS[role]}


def weights(role: str, priorities: dict | None) -> list[tuple[str, int, str]]:
    """The role's metrics with weights from its declared priorities (defaults if not declared)."""
    declared = priorities or {}
    out = [("diversions", DIVERSION_WEIGHT, "lower")]
    for metric, default_w, better in ROLE_METRICS[role]:
        p = declared.get(metric)
        out.append((metric, PRIORITY_WEIGHT[p] if p in PRIORITY_WEIGHT else default_w, better))
    return out


def score_options(options: list[dict], priorities: dict | None = None) -> dict:
    """{option_id: {role: {"score": 0-10, "penalties": {metric: points lost}}}}
    priorities: {role: {metric: "high" | "low" | "none"}} as declared by the agents."""
    priorities = priorities or {}
    out = {o["id"]: {} for o in options}
    for role in ROLES:
        metrics = weights(role, priorities.get(role))
        total_w = sum(w for _, w, _ in metrics) or 1
        for o in options:
            lost = {}
            for key, w, better in metrics:
                vals = [x["metrics"][key] for x in options]
                best, worst = (min(vals), max(vals)) if better == "lower" else (max(vals), min(vals))
                if best == worst or not w:
                    continue
                share = abs(o["metrics"][key] - best) / abs(worst - best)
                if share:
                    lost[key] = round(10 * w * share / total_w, 1)
            out[o["id"]][role] = {"score": round(max(0.0, 10 - sum(lost.values())), 1), "penalties": lost}
    return out


def score_table(scores: dict) -> dict:
    """{option_id: {role: score}} - the plain numbers shown to agents and in the UI."""
    return {oid: {r: v["score"] for r, v in per_role.items()} for oid, per_role in scores.items()}


def recommend(options: list[dict], scores: dict, positions: dict, rejected: list[str] = (),
              hard_violations: dict | None = None, emergency: dict | None = None) -> dict:
    ids = [o["id"] for o in options]
    cost = {o["id"]: o["balanced_cost"] for o in options}
    table = score_table(scores)
    hard_violations = hard_violations or {}

    vetoed: dict[str, list] = {}
    for role, p in positions.items():
        if len({v["option"] for v in p.get("vetoes", [])}) >= len(ids):
            continue  # an agent that vetoes everything is ignored, as in negotiate.decide
        for v in p.get("vetoes", []):
            vetoed.setdefault(v["option"], []).append({"by": role, "reason": v["reason"]})

    notes = []
    hard_ok = [i for i in ids if not hard_violations.get(i)]
    if not hard_ok:
        hard_ok = ids
        notes.append("Every option breaks some hard rule, so hard rules were not used to filter.")
    eligible = [i for i in hard_ok if i not in vetoed and i not in rejected]
    if not eligible:
        eligible = [i for i in hard_ok if i not in vetoed]
        notes.append("Every option not vetoed was already rejected, so rejected options are allowed again.")
    if not eligible:
        eligible = hard_ok
        notes.append("Every option was vetoed by someone, so vetoes were set aside.")

    acceptable_to_all = [i for i in eligible
                         if all(i in positions[r].get("acceptable", []) for r in positions)]
    pool = acceptable_to_all or eligible
    best = max(pool, key=lambda i: _rank_key(table[i], cost[i], emergency))
    return {
        "option": best,
        "scores": table[best],
        "min_score": min(table[best].values()),
        "below_min": [r for r, s in table[best].items() if s < ACCEPT_MIN_SCORE],
        "acceptable_to_all": best in acceptable_to_all,
        "rule": "Minimum Fuel: pilot first, then worst-off agent" if emergency else "worst-off agent best off",
        "breaks_hard_rules": [i for i in ids if hard_violations.get(i)],
        "vetoed": vetoed,
        "rejected_before": list(rejected),
        "eligible": eligible,
        "notes": notes,
    }


def _rank_key(row: dict, cost: float, emergency: dict | None) -> tuple:
    base = (min(row.values()), sum(row.values()), -cost)
    return ((row["pilot"],) + base) if emergency else base


def consensus(votes: dict) -> bool:
    return bool(votes) and all(v.get("vote") == "ACCEPT" for v in votes.values())


def resolve_without_consensus(options: list[dict], scores: dict, votes: dict, recommendation: dict,
                              hard_violations: dict | None = None, emergency: dict | None = None) -> dict | None:
    """Deterministic decision after turn 2 when not everyone accepted: the best max-min option among
    those all three listed as acceptable in turn 2, excluding the one that was objected to.
    Returns None if there is no such option (then the caller uses Borda + veto)."""
    table = score_table(scores)
    cost = {o["id"]: o["balanced_cost"] for o in options}
    hard_violations = hard_violations or {}
    common = [o["id"] for o in options
              if o["id"] != recommendation["option"] and not hard_violations.get(o["id"])
              and all(o["id"] in v.get("acceptable", []) for v in votes.values())]
    if not common:
        return None
    best = max(common, key=lambda i: _rank_key(table[i], cost[i], emergency))
    return {"option": best, "scores": table[best], "acceptable_to_all": common}


# ---------------------------------------------------------------------------
# Deterministic stand-in agents (no LLM): used when Claude is off or a call fails.
# ---------------------------------------------------------------------------

def proxy_position(role: str, options: list[dict], scores: dict, hard_violations: dict | None = None) -> dict:
    table = score_table(scores)
    cost = {o["id"]: o["balanced_cost"] for o in options}
    mine = {oid: [v for v in vs if v["role"] == role] for oid, vs in (hard_violations or {}).items()}
    ranking = sorted(table, key=lambda i: (bool(mine.get(i)), -table[i][role], cost[i]))
    return {"ranking": ranking,
            "acceptable": [i for i in ranking if table[i][role] >= ACCEPT_MIN_SCORE and not mine.get(i)],
            "priorities": default_priorities(role), "proposal": None,
            "vetoes": [], "messages": [], "argument": "", "reply": "", "ranking_repaired": False,
            "source": "deterministic"}


def proxy_vote(role: str, options: list[dict], scores: dict, recommendation: dict,
               hard_violations: dict | None = None) -> dict:
    p = proxy_position(role, options, scores, hard_violations)
    p["vote"] = "ACCEPT" if recommendation["option"] in p["acceptable"] else "OBJECT"
    return p
