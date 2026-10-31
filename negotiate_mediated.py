"""Runway-closure cascade with a mediated negotiation instead of a single vote.

intake -> safe options -> opening positions -> up to 3 rounds of
(mediator recommends -> agents ACCEPT/OBJECT and reply to each other) -> decision -> brief.

Steps 1-2 (intake, options) and the brief are the same as negotiate.py. The decision is
consensus when all three agents ACCEPT the mediator's recommendation; if there is no consensus
after MAX_ROUNDS, negotiate.decide() (Borda + veto) is the deterministic fallback.
Any failed LLM call falls back to a labelled deterministic stand-in, so the demo always finishes.
"""

from __future__ import annotations

import json

import ai
import ai_negotiation
import mediation
import negotiate
import solver
from agents import AgentCallError

ROLES = mediation.ROLES
MAX_ROUNDS = 3
LOG_PATH = "cascade_log.json"


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

    # 2. Safe options (same as negotiate.run)
    problem = solver.build_problem(event, disruption)
    gen = solver.generate_options(problem)
    options = gen["options"]
    safety = {o["id"]: solver.verify(problem, o) for o in options}
    safety["baseline"] = solver.verify(problem, gen["baseline"])
    log["steps"]["options"] = {"explored": gen["explored"], "front_size": gen["front_size"],
                               "options": negotiate._strip(options), "baseline": negotiate._strip([gen["baseline"]])[0],
                               "safety_violations": safety}
    yield {"type": "options", "problem": problem, **gen, "safety_violations": safety}

    scores = mediation.score_options(options)
    log["steps"]["scores"] = scores

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

    # 3. Opening positions (parallel, nobody speaks first)
    if use_llm:
        results = negotiate._parallel(
            lambda r: ai_negotiation.open_position(r, event, disruption, options, scores), ROLES)
    else:
        results = {r: (None, "LLM disabled") for r in ROLES}
    positions, warns = collect(results, lambda r: mediation.proxy_position(r, options, scores), "open")
    thread = [dict(m, round=0) for r in ROLES for m in positions[r].get("messages", [])]
    log["steps"]["opening"] = {"positions": _snap(positions), "warnings": warns}
    yield {"type": "opening", "positions": _snap(positions), "messages": list(thread), "warnings": warns}

    # 4. Mediated rounds
    rejected, rounds, agreed = [], [], None
    for rnd in range(1, MAX_ROUNDS + 1):
        rec = mediation.recommend(options, scores, positions, rejected)
        if use_llm:
            results = negotiate._parallel(
                lambda r, rec=rec, prev=dict(positions), th=list(thread): ai_negotiation.respond(
                    r, rnd, event, disruption, options, scores, rec, prev, th), ROLES)
        else:
            results = {r: (None, "LLM disabled") for r in ROLES}
        votes, warns = collect(results, lambda r, rec=rec: mediation.proxy_vote(r, options, scores, rec), f"round{rnd}")
        new_messages = [dict(m, round=rnd) for r in ROLES for m in votes[r].get("messages", [])]
        thread += new_messages
        positions = votes
        done = mediation.consensus(votes)
        round_log = {"round": rnd, "recommendation": rec, "votes": _snap(votes), "messages": new_messages,
                     "consensus": done, "warnings": warns}
        rounds.append(round_log)
        log["steps"][f"round{rnd}"] = round_log
        yield {"type": "mediation_round", **round_log}
        if done:
            agreed = (rnd, rec["option"])
            break
        rejected.append(rec["option"])

    # 5. Decision: consensus, or Borda + veto over the final positions as the fallback
    vote = negotiate.decide(options, positions)
    if agreed:
        decision = dict(vote, winner=agreed[1], method="consensus", consensus_round=agreed[0],
                        winner_rank_by_agent={ai.ROLE_NAMES[r]: p["ranking"].index(agreed[1]) + 1
                                              for r, p in positions.items()})
    else:
        decision = dict(vote, method="vote_fallback", consensus_round=None)
    decision["scores"] = mediation.score_table(scores)[decision["winner"]]
    log["steps"]["decision"] = decision
    yield {"type": "decision", **decision}

    # 6. Brief (same as negotiate.run)
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
        how = (f"agreed by all three agents in round {decision['consensus_round']}" if agreed
               else f"no consensus in {MAX_ROUNDS} rounds, chosen by the Borda + veto fallback")
        brief = (f"Option {decision['winner']} {how}. {w['diversions']} diversions, "
                 f"{w['total_delay_min']} min total delay, {w['pax_missed']} passengers miss connections, "
                 f"lowest fuel margin {w['min_fuel_margin_min']} min above final reserve.")
    log["steps"]["brief"] = {"text": brief, "source": brief_source}
    yield {"type": "brief", "text": brief, "source": brief_source}

    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    yield {"type": "done", "log": log, "options": options, "baseline": gen["baseline"], "decision": decision}


def _snap(positions: dict) -> dict:
    return {r: dict(p) for r, p in positions.items()}
