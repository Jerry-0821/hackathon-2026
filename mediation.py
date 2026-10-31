"""Mediator for the runway-closure cascade. Deterministic, no LLM.

The solver's options are all safe. The mediator does not decide - it recommends:
1. Score every option 0-10 for each agent from that agent's objectives (code, from the metrics).
2. Drop options someone vetoed (with a reason) and options the agents already rejected.
3. Prefer options every agent said it can accept.
4. Recommend the option where the worst-off agent scores highest
   (ties: higher total score, then the solver's balanced cost).
The agents then ACCEPT or OBJECT. Consensus = all three ACCEPT. If there is no consensus after
the last round, negotiate.decide() (Borda + veto) is the deterministic fallback.
"""

from __future__ import annotations

ROLES = ["atc", "pilot", "ground"]

# (metric, weight, better). Diversions matter to everyone and weigh most.
ROLE_METRICS = {
    "atc": [("diversions", 3, "lower"), ("total_delay_min", 2, "lower"), ("max_delay_min", 1, "lower")],
    "pilot": [("diversions", 3, "lower"), ("min_fuel_margin_min", 2, "higher"), ("pax_missed", 2, "lower")],
    "ground": [("diversions", 3, "lower"), ("downstream_delay_min", 2, "lower"),
               ("crew_overtime_min", 1, "lower"), ("gate_changes", 1, "lower")],
}

# 10 = the best of these options for you, 0 = the worst. 5 = middle of the range.
ACCEPT_MIN_SCORE = 5


def score_options(options: list[dict]) -> dict:
    """{option_id: {role: {"score": 0-10, "penalties": {metric: points lost}}}}"""
    out = {o["id"]: {} for o in options}
    for role, metrics in ROLE_METRICS.items():
        total_w = sum(w for _, w, _ in metrics)
        for o in options:
            lost = {}
            for key, w, better in metrics:
                vals = [x["metrics"][key] for x in options]
                best, worst = (min(vals), max(vals)) if better == "lower" else (max(vals), min(vals))
                if best == worst:
                    continue
                share = abs(o["metrics"][key] - best) / abs(worst - best)
                if share:
                    lost[key] = round(10 * w * share / total_w, 1)
            out[o["id"]][role] = {"score": round(max(0.0, 10 - sum(lost.values())), 1), "penalties": lost}
    return out


def score_table(scores: dict) -> dict:
    """{option_id: {role: score}} - the plain numbers shown to agents and in the UI."""
    return {oid: {r: v["score"] for r, v in per_role.items()} for oid, per_role in scores.items()}


def recommend(options: list[dict], scores: dict, positions: dict, rejected: list[str] = ()) -> dict:
    ids = [o["id"] for o in options]
    cost = {o["id"]: o["balanced_cost"] for o in options}
    table = score_table(scores)

    vetoed: dict[str, list] = {}
    for role, p in positions.items():
        if len({v["option"] for v in p.get("vetoes", [])}) >= len(ids):
            continue  # an agent that vetoes everything is ignored, as in negotiate.decide
        for v in p.get("vetoes", []):
            vetoed.setdefault(v["option"], []).append({"by": role, "reason": v["reason"]})

    notes = []
    eligible = [i for i in ids if i not in vetoed and i not in rejected]
    if not eligible:
        eligible = [i for i in ids if i not in vetoed]
        notes.append("Every option not vetoed was already rejected, so rejected options are allowed again.")
    if not eligible:
        eligible = ids
        notes.append("Every option was vetoed by someone, so vetoes were set aside.")

    acceptable_to_all = [i for i in eligible
                         if all(i in positions[r].get("acceptable", []) for r in positions)]
    pool = acceptable_to_all or eligible
    best = max(pool, key=lambda i: (min(table[i].values()), sum(table[i].values()), -cost[i]))
    return {
        "option": best,
        "scores": table[best],
        "min_score": min(table[best].values()),
        "below_min": [r for r, s in table[best].items() if s < ACCEPT_MIN_SCORE],
        "acceptable_to_all": best in acceptable_to_all,
        "vetoed": vetoed,
        "rejected_before": list(rejected),
        "eligible": eligible,
        "notes": notes,
    }


def consensus(votes: dict) -> bool:
    return bool(votes) and all(v.get("vote") == "ACCEPT" for v in votes.values())


# ---------------------------------------------------------------------------
# Deterministic stand-in agents (no LLM): used when Claude is off or a call fails.
# ---------------------------------------------------------------------------

def proxy_position(role: str, options: list[dict], scores: dict) -> dict:
    table = score_table(scores)
    cost = {o["id"]: o["balanced_cost"] for o in options}
    ranking = sorted(table, key=lambda i: (-table[i][role], cost[i]))
    return {"ranking": ranking, "acceptable": [i for i in ranking if table[i][role] >= ACCEPT_MIN_SCORE],
            "vetoes": [], "messages": [], "argument": "", "reply": "", "ranking_repaired": False,
            "source": "deterministic"}


def proxy_vote(role: str, options: list[dict], scores: dict, recommendation: dict) -> dict:
    p = proxy_position(role, options, scores)
    p["vote"] = "ACCEPT" if recommendation["option"] in p["acceptable"] else "OBJECT"
    return p
