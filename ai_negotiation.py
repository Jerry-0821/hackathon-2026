"""Claude calls for the mediated cascade negotiation (negotiate_mediated.py).
Design: .plans/cascade-negotiation-design.md

Each agent only sees its own organisation's data (private_view, my_option_view) plus the shared
safety outcome (diversions), the mediator's scores and its own hard-rule check per option, which code
computes. Reuses ai.py: the API call wrapper, role prompts, role names, ranking repair.

Two calls per agent, one independent API request each, all three agents at the same time:
- open_position(): turn 1 - declare priorities, preferred / acceptable options, vetoes, an optional
  own plan (landing order + gate policy), and messages to the other agents.
- respond():       turn 2 - ACCEPT or OBJECT to the mediator's recommendation, after seeing every
  agent's published declaration, the plans they added and the messages.
"""

from __future__ import annotations

import json

import ai
import cascade_rules
import mediation
import solver
import workbook

MESSAGE_TYPES = ["request", "offer", "concession", "reject"]

# Shared by every role: diversions are a safety outcome everyone must see.
OWN_SUMMARY = {
    "atc": ["diversions", "total_delay_min", "max_delay_min", "last_landing"],
    "pilot": ["diversions", "min_fuel_margin_min", "pax_missed"],
    "ground": ["diversions", "gate_changes", "gate_wait_min", "downstream_delay_min", "crew_overtime_min"],
}

PROTOCOL = (
    " You work for a different organisation from the other two agents and only see your own data. "
    "Every option already respects the red lines (separation, final reserve fuel, gate availability), "
    "checked by code. Hard rules (holding, company fuel buffer, crew overtime) are checked by code for "
    "you in breaks_my_hard_rules. A neutral mediator (code, not AI) scores every option 0-10 for each "
    "agent using the priorities each agent declares, and recommends the option where the worst-off "
    "agent does best. The result only stands if all three agents ACCEPT, and there are only two turns. "
    "Rules: (1) declare your priorities honestly - 'high' only for what really matters to you; "
    "(2) answer every request addressed to you; (3) never give up a hard rule, but trade preferences; "
    "(4) if you OBJECT, say which option you would accept instead. Use the submit_position tool."
)


def private_view(role: str, event: dict, disruption: dict, problem: dict) -> dict:
    """Only this role's own operational data, plus the challenge workbook limits for that role."""
    view = _own_data(role, event, disruption, problem)
    view["limits_from_challenge_workbook"] = _workbook_limits(role, event, problem)
    return view


def _workbook_limits(role: str, event: dict, problem: dict) -> dict:
    fc = workbook.load()["fuel_crew"]
    a = workbook.airport(event["airport"])
    if role == "atc":
        return {"active_runways": a["active_runways"], "max_runway_ops_per_hour": a["max_runway_ops_per_hour"],
                "real_traffic_ops_per_hour_now": event["demand_ops_per_hour"],
                "min_minutes_between_this_bank's_landings": {
                    f"{k} runway(s) closed": v for k, v in problem["landing_interval_min"].items()
                    if k < a["active_runways"]},
                "wake_separation_nm": {**{f"{lead} then {follow}": nm for (lead, follow), nm in
                                          workbook.load()["wake_nm"].items()},
                                       "any other pair": workbook.load()["wake_default_nm"]},
                "wake_note": f"converted at {workbook.APPROACH_SPEED_KT} kt and rounded up to whole minutes"}
    if role == "pilot":
        return {"final_reserve_min": fc["final_reserve_min"], "contingency_pct_of_trip": fc["contingency_pct"],
                "holding_burn_kg_per_hour": {"narrowbody": fc["burn_narrow_kg_h"], "widebody": fc["burn_wide_kg_h"]},
                "note": "fuel_above_final_reserve_min is the holding you can do before touching the final reserve."}
    return {"min_turnaround_min": problem["turnaround_min"], "spare_gates": a["spare_gates"],
            "gate_change_notice_min": problem["gate_change_notice_min"],
            "gate_change_deadline": f"flights landing before {solver.hhmm(problem['decision_min'] + problem['gate_change_notice_min'])} "
                                    "must keep their planned gate unless it is out of service",
            "ground_shift_h": fc["ground_shift_h"], "overtime_cost": f"{fc['overtime_rate']}x after the shift"}


def _own_data(role: str, event: dict, disruption: dict, problem: dict) -> dict:
    if role == "atc":
        return {"arrival_runways": event["arrival_runways"], "flight_rules": disruption["flight_rules"],
                "runway_closures": [{"runway": c["runway"], "from": solver.hhmm(c["start_min"]),
                                     "to": solver.hhmm(c["end_min"])} for c in disruption["runway_closures"]],
                "flights": [{"flight": f["id"], "eta": f["eta"], "wake": f.get("faa_wake", f["wake"])} for f in event["flights"]]}
    if role == "pilot":
        return {"flights": [{"flight": f["id"], "eta": f["eta"], "fuel_above_final_reserve_min": f["extra_fuel_min"],
                             "onward_connections": [{"flight": c["flight"], "departure": c["departure"], "pax": c["pax"]}
                                                    for c in f["connections"]]} for f in event["flights"]]}
    return {"crew_shift_end": solver.hhmm(event["crew_shift_end_min"]),
            "gates": [{"gate": g["id"], "widebody": g["widebody"],
                       "busy": [f"{solver.hhmm(s)}-{solver.hhmm(e)}" for s, e in problem["busy"].get(g["id"], [])]}
                      for g in problem["gates"]],
            "gates_out_of_service": problem["gates_out"],
            "flights": [{"flight": f["id"], "planned_gate": f["preferred_gate"], "widebody": f["widebody"],
                         "aircraft_next_departure": (f"{f['next_departure']['flight']} {f['next_departure']['departure']}"
                                                     if f["next_departure"] else None)} for f in event["flights"]]}


def my_option_view(role: str, opt: dict) -> dict:
    view = ai._option_view(role, opt)  # per-flight detail is already role-specific
    view["summary"] = {k: opt["metrics"][k] for k in OWN_SUMMARY[role]}
    if opt.get("proposed_by"):
        view["proposed_by"] = ai.ROLE_NAMES[opt["proposed_by"]]
    return view


def tool_for(role: str) -> dict:
    priority_fields = {
        f"priority_{m}": {"type": "string", "enum": list(mediation.PRIORITY_WEIGHT),
                          "description": f"How much {m} matters to you (feeds the mediator's weights)."}
        for m, _, _ in mediation.ROLE_METRICS[role]
    }
    return {
        "name": "submit_position",
        "description": "Submit your position in the negotiation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vote": {"type": "string", "enum": ["ACCEPT", "OBJECT"],
                         "description": "Turn 2 only: ACCEPT or OBJECT to the mediator's recommendation."},
                **priority_fields,
                "ranking": {"type": "array", "items": {"type": "string"},
                            "description": "Every option ID exactly once, best first for your role."},
                "acceptable": {"type": "array", "items": {"type": "string"},
                               "description": "Option IDs you can live with, even if not your favourite."},
                "vetoes": {"type": "array",
                           "description": "Options you truly cannot accept, with a reason that cites a number. Usually empty.",
                           "items": {"type": "object",
                                     "properties": {"option": {"type": "string"}, "reason": {"type": "string"}},
                                     "required": ["option", "reason"]}},
                "proposed_landing_order": {"type": "array", "items": {"type": "string"},
                                           "description": "Turn 1, optional: your own plan as a landing order of "
                                                          "flight IDs. Code checks it; if safe it becomes a new option."},
                "proposed_gate_policy": {"type": "string", "enum": ["keep", "flex"],
                                         "description": "For your plan: keep = wait up to 20 min for planned gates, "
                                                        "flex = take the first free gate."},
                "messages": {"type": "array",
                             "description": "What you say to the other agents. One to three short messages.",
                             "items": {"type": "object",
                                       "properties": {
                                           "to": {"type": "string", "enum": ["atc", "pilot", "ground", "all"]},
                                           "type": {"type": "string", "enum": MESSAGE_TYPES},
                                           "text": {"type": "string", "description": "One plain sentence with a number."},
                                       },
                                       "required": ["to", "type", "text"]}},
                "argument": {"type": "string", "description": "Two plain sentences. Cite the numbers behind your position."},
            },
            "required": ["ranking", "acceptable", "vetoes", "messages", "argument", *priority_fields],
        },
    }


def _system(role: str) -> str:
    return ai.ROLE_PROMPTS[role].replace(ai._COMMON, "") + PROTOCOL


def _payload(turn: int, role: str, ctx: dict) -> dict:
    options = ctx["options"]
    own_keys = OWN_SUMMARY[role]
    return {
        "turn": turn,
        "you_are": ai.ROLE_NAMES[role],
        "airport": ctx["event"]["airport"],
        "situation": ctx["disruption"].get("summary") or "One arrival runway closed; see options.",
        "your_private_data": private_view(role, ctx["event"], ctx["disruption"], ctx["problem"]),
        "emergency": ctx.get("emergency"),
        # Each role sees the options in a different order, so no option gains from being listed first.
        "options": [my_option_view(role, o) for o in ai._rotate(options, ai.ROLE_ORDER_SHIFT[role])],
        "best_option_per_metric": {k: v for k, v in ai.best_per_metric(options).items() if k in own_keys},
        "your_hard_rules": cascade_rules.checks_for(role, ctx["hard"]),
        "mediator_scores": mediation.score_table(ctx["scores"]),
        "scoring_rule": {
            "how": "10 = best of these options for that agent, 0 = worst, from that agent's declared priorities.",
            "priority_weights": mediation.PRIORITY_WEIGHT,
            "diversions_weight": mediation.DIVERSION_WEIGHT,
            "acceptable_if_at_least": mediation.ACCEPT_MIN_SCORE,
        },
        "note": "Numbers in best_option_per_metric, your_hard_rules and mediator_scores are computed by code and correct.",
    }


def _clean(raw: dict, ids: list[str], role: str, flight_ids: list[str]) -> dict:
    p = ai._clean_position(raw, ids)  # repairs ranking, keeps valid vetoes
    acceptable = [ai.option_id(x, ids) for x in raw.get("acceptable") or []]
    p["acceptable"] = [i for n, i in enumerate(acceptable) if i and i not in acceptable[:n]]
    p["priorities"] = {m: raw[f"priority_{m}"] for m, _, _ in mediation.ROLE_METRICS[role]
                       if raw.get(f"priority_{m}") in mediation.PRIORITY_WEIGHT}
    # Models write "DL1183" or "dl 1183" for "DL 1183": match flight IDs ignoring spaces and case.
    known = {f.replace(" ", "").upper(): f for f in flight_ids}
    order = [known.get(str(x).replace(" ", "").upper(), str(x).strip()) for x in raw.get("proposed_landing_order") or []]
    p["proposal"] = ({"landing_order": order,
                      "gate_policy": raw.get("proposed_gate_policy") if raw.get("proposed_gate_policy") in ("keep", "flex") else "keep"}
                     if order and sorted(order) == sorted(flight_ids) else None)
    if order and not p["proposal"]:
        p["proposal_rejected"] = "landing order must list every flight in this bank exactly once"
    p["messages"] = [
        {"from": role, "to": m.get("to") if m.get("to") in ("atc", "pilot", "ground", "all") else "all",
         "type": m.get("type") if m.get("type") in MESSAGE_TYPES else "request",
         "text": str(m.get("text", "")).strip()}
        for m in raw.get("messages") or [] if isinstance(m, dict) and str(m.get("text", "")).strip()
    ][:3]
    if raw.get("vote") in ("ACCEPT", "OBJECT"):
        p["vote"] = raw["vote"]
    return p


def open_position(role: str, ctx: dict) -> tuple[dict, dict]:
    payload = _payload(1, role, ctx)
    payload["instruction"] = ("Turn 1 of 2. Declare your priorities, your preferred option, every option you could "
                              "accept, and what you need from the other agents. If no option fits, propose your own "
                              "landing order - code will check it and add it if it is safe.")
    raw, perf = ai._call(ai.ROLE_NAMES[role], system=_system(role), user=json.dumps(payload, indent=1),
                         tool=tool_for(role))
    return _clean(raw, [o["id"] for o in ctx["options"]], role, [f["id"] for f in ctx["event"]["flights"]]), perf


def respond(role: str, ctx: dict, recommendation: dict, published: dict, thread: list[dict]) -> tuple[dict, dict]:
    payload = _payload(2, role, ctx)
    payload["mediator_recommendation"] = {
        "option": recommendation["option"],
        "scores": recommendation["scores"],
        "acceptable_to_all_agents": recommendation["acceptable_to_all"],
        "rule": recommendation["rule"],
    }
    payload["published_by_other_agents"] = {
        ai.ROLE_NAMES[r]: {"priorities": p.get("priorities"), "preferred": p["ranking"][0],
                           "acceptable": p.get("acceptable", []), "vetoes": p.get("vetoes", []),
                           "proposal": p.get("proposal"), "argument": p.get("argument", "")}
        for r, p in published.items() if r != role
    }
    payload["plans_added_by_agents"] = [o["id"] for o in ctx["options"] if o.get("proposed_by")]
    payload["messages_to_you"] = [m for m in thread if m["to"] in (role, "all") and m["from"] != role]
    payload["instruction"] = (f"Turn 2 of 2 - the last turn. Vote ACCEPT or OBJECT on option "
                              f"{recommendation['option']}. If nobody accepts, code decides from the options all three "
                              "list as acceptable. Reply to every message addressed to you.")
    raw, perf = ai._call(ai.ROLE_NAMES[role], system=_system(role), user=json.dumps(payload, indent=1),
                         tool=tool_for(role))
    p = _clean(raw, [o["id"] for o in ctx["options"]], role, [f["id"] for f in ctx["event"]["flights"]])
    if "vote" not in p:
        raise ai.AgentCallError(f"{ai.ROLE_NAMES[role]}: no ACCEPT/OBJECT vote on the recommendation")
    return p, perf
