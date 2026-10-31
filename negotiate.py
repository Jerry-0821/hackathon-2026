"""Runway-closure cascade pipeline: intake -> options -> two agent rounds -> decision -> brief.

A generator, like orchestrator.py, so the UI can render each step as it happens.
If any LLM call fails, the step falls back to a clearly labelled deterministic stand-in,
so the demo always reaches a decision.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import ai
import solver
from agents import AgentCallError

ROLES = ["atc", "pilot", "ground"]
LOG_PATH = "cascade_log.json"

# Deterministic stand-in rankings (used for evaluation, and if an agent's call fails).
PROXY_KEYS = {
    "atc": lambda m: (m["diversions"], m["total_delay_min"], m["max_delay_min"]),
    "pilot": lambda m: (m["diversions"], -m["min_fuel_margin_min"], m["pax_missed"]),
    "ground": lambda m: (m["diversions"], m["downstream_delay_min"] + m["crew_overtime_min"], m["gate_changes"]),
}


def proxy_position(role: str, options: list[dict]) -> dict:
    ranked = sorted(options, key=lambda o: (PROXY_KEYS[role](o["metrics"]), o["balanced_cost"]))
    return {"ranking": [o["id"] for o in ranked], "vetoes": [], "argument": "", "reply": "",
            "ranking_repaired": False, "source": "deterministic"}


def decide(options: list[dict], positions: dict) -> dict:
    """Fixed voting rule. Borda count over the agents' rankings (top = K-1 points).
    A vetoed option is not eligible, unless the vetoes would rule out every option.
    Ties go to the option with the lower balanced cost across all objectives."""
    ids = [o["id"] for o in options]
    k = len(ids)
    borda = {i: 0 for i in ids}
    for p in positions.values():
        for rank, oid in enumerate(p["ranking"]):
            borda[oid] += k - 1 - rank

    vetoes: dict[str, list] = {}
    for role, p in positions.items():
        if len({v["option"] for v in p["vetoes"]}) >= k:
            continue  # an agent that vetoes everything is ignored
        for v in p["vetoes"]:
            vetoes.setdefault(v["option"], []).append({"by": ai.ROLE_NAMES[role], "reason": v["reason"]})

    eligible = [i for i in ids if i not in vetoes]
    note = ""
    if not eligible:
        eligible, note = ids, "Every option was vetoed by someone, so vetoes were set aside."
    cost = {o["id"]: o["balanced_cost"] for o in options}
    winner = max(eligible, key=lambda i: (borda[i], -cost[i]))
    rank_for = {ai.ROLE_NAMES[r]: p["ranking"].index(winner) + 1 for r, p in positions.items()}
    return {"winner": winner, "borda": borda, "vetoes": vetoes, "eligible": eligible,
            "winner_rank_by_agent": rank_for, "note": note}


def _parallel(fn, roles):
    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        futures = {r: pool.submit(fn, r) for r in roles}
        out = {}
        for r, fut in futures.items():
            try:
                out[r] = (fut.result(), None)
            except AgentCallError as e:
                out[r] = (None, str(e))
            except Exception as e:  # noqa: BLE001 - a bad model answer must not crash the demo
                out[r] = (None, f"{r}: unexpected {type(e).__name__}: {e}")
        return out


def run(event: dict, texts: dict | None = None, use_llm: bool = True):
    texts = texts or event["texts"]
    log = {"event_id": event["id"], "texts": texts, "use_llm": use_llm, "steps": {}, "perf": []}

    # 1. Intake
    warnings, raw, match = [], None, None
    if use_llm:
        try:
            raw, perf = ai.parse_disruption(texts, event)
            log["perf"].append({"step": "intake", **perf})
            disruption, warnings = ai.validate_disruption(raw, event)
            match = ai.compare_to_truth(disruption, event["disruption_truth"])
        except AgentCallError as e:
            disruption = dict(event["disruption_truth"], summary="", other_notes=[])
            warnings = [f"Intake call failed ({e}). Using the scenario's built-in structured disruption."]
    else:
        disruption = dict(event["disruption_truth"], summary="", other_notes=[])
    step = {"raw": raw, "disruption": disruption, "warnings": warnings, "matches_truth": match}
    log["steps"]["intake"] = step
    yield {"type": "intake", **step}

    # 2. Options (deterministic)
    problem = solver.build_problem(event, disruption)
    gen = solver.generate_options(problem)
    options = gen["options"]
    safety = {o["id"]: solver.verify(problem, o) for o in options}
    safety["baseline"] = solver.verify(problem, gen["baseline"])
    log["steps"]["options"] = {"explored": gen["explored"], "front_size": gen["front_size"],
                               "options": _strip(options), "baseline": _strip([gen["baseline"]])[0],
                               "safety_violations": safety}
    yield {"type": "options", "problem": problem, **gen, "safety_violations": safety}

    # 3. Two rounds of ranking and argument
    positions: dict = {}
    for round_no in (1, 2):
        prev = dict(positions)
        if use_llm:
            def call(role, round_no=round_no, prev=prev):
                others = {r: p for r, p in prev.items() if r != role} if round_no == 2 else None
                return ai.rank_options(role, round_no, event, disruption, options, others)
            results = _parallel(call, ROLES)
        else:
            results = {r: (None, "LLM disabled") for r in ROLES}
        round_warnings = []
        for role in ROLES:
            res, err = results[role]
            if res:
                positions[role], perf = res
                log["perf"].append({"step": f"round{round_no}:{role}", **perf})
            else:
                positions[role] = proxy_position(role, options)
                if use_llm:
                    round_warnings.append(f"{ai.ROLE_NAMES[role]}: {err}. Used the deterministic stand-in ranking.")
        snapshot = {r: dict(p) for r, p in positions.items()}
        log["steps"][f"round{round_no}"] = {"positions": snapshot, "warnings": round_warnings}
        yield {"type": "round", "round": round_no, "positions": snapshot, "warnings": round_warnings}

    # 4. Decision (deterministic)
    decision = decide(options, positions)
    log["steps"]["decision"] = decision
    yield {"type": "decision", **decision}

    # 5. Brief
    brief, brief_source = None, "llm"
    if use_llm:
        try:
            brief, perf = ai.write_brief(event, disruption, decision, options, positions)
            log["perf"].append({"step": "brief", **perf})
        except AgentCallError as e:
            brief_source = f"template (brief call failed: {e})"
    if not brief:
        brief_source = brief_source if use_llm else "template"
        w = next(o for o in options if o["id"] == decision["winner"])["metrics"]
        brief = (f"Option {decision['winner']} selected by Borda vote. {w['diversions']} diversions, "
                 f"{w['total_delay_min']} min total delay, {w['pax_missed']} passengers miss connections, "
                 f"lowest fuel margin {w['min_fuel_margin_min']} min above final reserve.")
    log["steps"]["brief"] = {"text": brief, "source": brief_source}
    yield {"type": "brief", "text": brief, "source": brief_source}

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    yield {"type": "done", "log": log, "options": options, "baseline": gen["baseline"], "decision": decision}


def _strip(plans: list[dict]) -> list[dict]:
    """Drop the internal signature tuple so plans serialise cleanly."""
    return [{k: v for k, v in p.items() if k != "signature"} for p in plans]
