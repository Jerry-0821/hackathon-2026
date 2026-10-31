"""Runway-closure cascade with a mediated negotiation in two agent turns.
Design: .plans/cascade-negotiation-design.md

intake -> safe options -> rule tiers (hard rules, automatic Minimum Fuel) ->
turn 1 (all three agents at once: priorities, positions, own plans, messages) ->
agents' plans checked by code and added if safe -> mediator recommends ->
turn 2 (all three at once: ACCEPT or OBJECT) -> decision -> brief.

The brief asks for agreement "in under 3 turns", so there is no third agent turn. Decision:
all three ACCEPT -> consensus. Otherwise code decides: the best max-min option every agent listed as
acceptable in turn 2, or else negotiate.decide() (Borda + veto). Any failed LLM call falls back to a
labelled deterministic stand-in, so the demo always finishes. Intake, options and the brief are the
same as negotiate.py.
"""

from __future__ import annotations

import json

import ai
import ai_negotiation
import cascade_rules
import mediation
import negotiate
import solver
from agents import AgentCallError

ROLES = mediation.ROLES
AGENT_TURNS = 2
LOG_PATH = "cascade_log.json"


def add_agent_plans(problem: dict, options: list[dict], positions: dict) -> tuple[list[dict], list[dict]]:
    """Simulate and verify every plan the agents proposed; add the safe, new ones as options.
    Returns (options, report). Balanced costs are recomputed over the enlarged set."""
    flights = problem["flights"]
    index = {f["id"]: i for i, f in enumerate(flights)}
    by_sig = {o["signature"]: o["id"] for o in options}
    options, report = list(options), []
    for role in ROLES:
        prop = positions[role].get("proposal")
        if positions[role].get("proposal_rejected"):
            report.append({"by": role, "result": "rejected", "detail": positions[role]["proposal_rejected"]})
        if not prop:
            continue
        plan = solver.simulate(problem, [index[f] for f in prop["landing_order"]], prop["gate_policy"])
        violations = solver.verify(problem, plan)
        if violations:
            report.append({"by": role, "result": "unsafe", "detail": "; ".join(violations), "proposal": prop})
        elif plan["signature"] in by_sig:
            report.append({"by": role, "result": "same as existing option", "option": by_sig[plan["signature"]],
                           "proposal": prop})
        else:
            oid = chr(ord("A") + len(options))
            options.append({"id": oid, "name": f"Plan from {ai.ROLE_NAMES[role]}", "proposed_by": role, **plan})
            by_sig[plan["signature"]] = oid
            report.append({"by": role, "result": "added", "option": oid, "proposal": prop})
    for o, cost in zip(options, solver._balanced_costs(options)):
        o["balanced_cost"] = round(cost, 3)
    return options, report


def _extend_rankings(positions: dict, options: list[dict], report: list[dict]) -> None:
    """New options go to the end of everyone's ranking, except the proposer's (first, and acceptable)."""
    ids = [o["id"] for o in options]
    for r in report:
        if r["result"] in ("added", "same as existing option"):
            p = positions[r["by"]]
            p["ranking"] = [r["option"]] + [i for i in p["ranking"] if i != r["option"]]
            if r["option"] not in p["acceptable"]:
                p["acceptable"] = [r["option"]] + p["acceptable"]
    for p in positions.values():
        p["ranking"] += [i for i in ids if i not in p["ranking"]]


def run(event: dict, texts: dict | None = None, use_llm: bool = True):
    texts = texts or event["texts"]
    log = {"event_id": event["id"], "process": "mediated_negotiation", "texts": texts, "use_llm": use_llm,
           "steps": {}, "perf": []}

    # 1. Intake (same as negotiate.run)
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

    # 2. Safe options (same as negotiate.run) - the red lines
    problem = solver.build_problem(event, disruption)
    gen = solver.generate_options(problem)
    options = gen["options"]
    safety = {o["id"]: solver.verify(problem, o) for o in options}
    safety["baseline"] = solver.verify(problem, gen["baseline"])
    log["steps"]["options"] = {"explored": gen["explored"], "front_size": gen["front_size"],
                               "options": negotiate._strip(options), "baseline": negotiate._strip([gen["baseline"]])[0],
                               "safety_violations": safety}
    yield {"type": "options", "problem": problem, **gen, "safety_violations": safety}

    # 3. Rule tiers: automatic Minimum Fuel, hard rules
    emergency = cascade_rules.detect_minimum_fuel(options)
    hard = cascade_rules.evaluate(options, emergency)
    scores = mediation.score_options(options)  # default priorities until the agents declare theirs
    log["steps"]["rules"] = {"red_lines": cascade_rules.RED_LINES, "emergency": emergency, "hard_rules": hard}
    yield {"type": "rules", "red_lines": cascade_rules.RED_LINES, "emergency": emergency, "hard": hard}

    def ctx() -> dict:
        return {"event": event, "disruption": disruption, "problem": problem, "options": options,
                "scores": scores, "hard": hard, "emergency": emergency}

    def collect(results: dict, stand_in, label: str) -> tuple[dict, list[str]]:
        out, warns = {}, []
        for role in ROLES:
            res, err = results[role]
            if res:
                out[role], perf = res
                log["perf"].append({"step": f"{label}:{role}", **perf})
            else:
                out[role] = stand_in(role)
                if use_llm:
                    warns.append(f"{ai.ROLE_NAMES[role]}: {err}. Used the deterministic stand-in.")
        return out, warns

    # 4. Turn 1 - all three at once, nobody speaks first
    if use_llm:
        c = ctx()
        results = negotiate._parallel(lambda r: ai_negotiation.open_position(r, c), ROLES)
    else:
        results = {r: (None, "LLM disabled") for r in ROLES}
    positions, warns = collect(
        results, lambda r: mediation.proxy_position(r, options, scores, hard["violations"]), "turn1")
    thread = [dict(m, turn=1) for r in ROLES for m in positions[r].get("messages", [])]

    # Agents' own plans -> code checks them -> safe new ones become options for everyone.
    options, plan_report = add_agent_plans(problem, options, positions)
    _extend_rankings(positions, options, plan_report)
    hard = cascade_rules.evaluate(options, emergency)
    priorities = {r: positions[r].get("priorities") or mediation.default_priorities(r) for r in ROLES}
    scores = mediation.score_options(options, priorities)
    opening = {"positions": _snap(positions), "messages": list(thread), "warnings": warns,
               "plans": plan_report, "priorities": priorities, "hard": hard, "scores": scores}
    log["steps"]["turn1"] = opening
    yield {"type": "opening", **opening}

    # 5. Mediator recommends (code)
    rec = mediation.recommend(options, scores, positions, hard_violations=hard["violations"], emergency=emergency)
    log["steps"]["recommendation"] = rec
    yield {"type": "recommendation", **rec}

    # 6. Turn 2 - ACCEPT or OBJECT, all three at once
    published = _snap(positions)
    if use_llm:
        c = ctx()
        results = negotiate._parallel(lambda r: ai_negotiation.respond(r, c, rec, published, list(thread)), ROLES)
    else:
        results = {r: (None, "LLM disabled") for r in ROLES}
    votes, warns = collect(
        results, lambda r: mediation.proxy_vote(r, options, scores, rec, hard["violations"]), "turn2")
    new_messages = [dict(m, turn=2) for r in ROLES for m in votes[r].get("messages", [])]
    thread += new_messages
    for r in ROLES:  # keep turn-1 declarations if an agent did not repeat them
        votes[r].setdefault("priorities", positions[r].get("priorities"))
        votes[r]["ranking"] += [i for i in (o["id"] for o in options) if i not in votes[r]["ranking"]]
    agreed = mediation.consensus(votes)
    responses = {"votes": _snap(votes), "messages": new_messages, "consensus": agreed, "warnings": warns}
    log["steps"]["turn2"] = responses
    yield {"type": "responses", **responses}

    # 7. Decision: consensus, else code decides (no third turn)
    vote = negotiate.decide(options, votes)
    table = mediation.score_table(scores)
    if agreed:
        decision = dict(vote, winner=rec["option"], method="consensus", consensus_turn=AGENT_TURNS)
    else:
        fallback = mediation.resolve_without_consensus(options, scores, votes, rec, hard["violations"], emergency)
        if fallback:
            decision = dict(vote, winner=fallback["option"], method="mediator_fallback", consensus_turn=None,
                            fallback_pool=fallback["acceptable_to_all"])
        else:
            decision = dict(vote, method="vote_fallback", consensus_turn=None)
    decision["winner_rank_by_agent"] = {ai.ROLE_NAMES[r]: p["ranking"].index(decision["winner"]) + 1
                                        for r, p in votes.items()}
    decision["scores"] = table[decision["winner"]]
    decision["objected_by"] = [r for r in ROLES if votes[r].get("vote") == "OBJECT"]
    log["steps"]["decision"] = decision
    yield {"type": "decision", **decision}

    # 8. Brief (same as negotiate.run)
    brief, brief_source = None, "llm"
    if use_llm:
        try:
            brief, perf = ai.write_brief(event, disruption, decision, options, votes)
            log["perf"].append({"step": "brief", **perf})
        except AgentCallError as e:
            brief_source = f"template (brief call failed: {e})"
    if not brief:
        brief_source = brief_source if use_llm else "template"
        w = next(o for o in options if o["id"] == decision["winner"])["metrics"]
        how = {"consensus": "agreed by all three agents in turn 2",
               "mediator_fallback": "chosen by the mediator from the options all three agents could accept",
               "vote_fallback": "chosen by the Borda + veto fallback"}[decision["method"]]
        brief = (f"Option {decision['winner']} {how}. {w['diversions']} diversions, "
                 f"{w['total_delay_min']} min total delay, {w['pax_missed']} passengers miss connections, "
                 f"lowest fuel margin {w['min_fuel_margin_min']} min above final reserve.")
    log["steps"]["brief"] = {"text": brief, "source": brief_source}
    yield {"type": "brief", "text": brief, "source": brief_source}

    log["steps"]["thread"] = thread
    log["steps"]["final_options"] = negotiate._strip(options)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    yield {"type": "done", "log": log, "options": options, "baseline": gen["baseline"], "decision": decision}


def _snap(positions: dict) -> dict:
    return {r: dict(p) for r, p in positions.items()}
