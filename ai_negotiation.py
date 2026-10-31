"""Claude calls for the mediated negotiation (negotiate_mediated.py).

Reuses ai.py: the API call wrapper, each role's priorities, the per-role option view (each agent
only sees per-flight detail for its own domain), the code-computed best-per-metric table, and
ranking repair. Each call is one independent API request per agent.

Two calls per agent:
- open_position(): round 1 - preferred option, acceptable options, vetoes, messages to others.
- respond():       every round - ACCEPT or OBJECT to the mediator's recommendation, reply to the
                   messages addressed to you, update what you can accept.
"""

from __future__ import annotations

import json

import ai
import mediation

MESSAGE_TYPES = ["request", "offer", "concession", "reject"]

NEGOTIATE_TOOL = {
    "name": "submit_position",
    "description": "Submit your position in the negotiation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vote": {"type": "string", "enum": ["ACCEPT", "OBJECT"],
                     "description": "Only when a recommendation is given: ACCEPT or OBJECT to it."},
            "ranking": {"type": "array", "items": {"type": "string"},
                        "description": "Every option ID exactly once, best first for your role."},
            "acceptable": {"type": "array", "items": {"type": "string"},
                           "description": "Option IDs you can live with, even if not your favourite."},
            "vetoes": {"type": "array",
                       "description": "Options you truly cannot accept, with a reason that cites a number. Usually empty.",
                       "items": {"type": "object",
                                 "properties": {"option": {"type": "string"}, "reason": {"type": "string"}},
                                 "required": ["option", "reason"]}},
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
        "required": ["ranking", "acceptable", "vetoes", "messages", "argument"],
    },
}

PROTOCOL = (
    " Every option has already passed a deterministic safety check, so you are negotiating "
    "trade-offs, not safety. This is a negotiation, not a vote: a neutral mediator (code, not AI) "
    "scores every option 0-10 for each agent and recommends the option where the worst-off agent "
    "does best. The result only stands if all three agents ACCEPT. Rules: "
    "(1) answer every request addressed to you - accept it or say why not; "
    "(2) if you OBJECT, say in a message which option you would accept instead, or what would "
    "change your mind; (3) never give up a real limit, but trade your preferences; "
    "(4) move toward what the others need unless it breaks one of your limits. "
    "Message types: request (what you need from someone), offer (if you do X I accept Y), "
    "concession (a preference you give up), reject (you cannot accept something - cite a number). "
    "Use the submit_position tool."
)


def _system(role: str) -> str:
    return ai.ROLE_PROMPTS[role].replace(ai._COMMON, "") + PROTOCOL


def _payload(round_no: int, role: str, event: dict, disruption: dict, options: list[dict], scores: dict) -> dict:
    return {
        "round": round_no,
        "you_are": ai.ROLE_NAMES[role],
        "airport": event["airport"],
        "situation": disruption.get("summary") or "One arrival runway closed; see options.",
        "flight_rules": disruption["flight_rules"],
        # Each role sees the options in a different order, so no option gains from being listed first.
        "options": [ai._option_view(role, o) for o in ai._rotate(options, ai.ROLE_ORDER_SHIFT[role])],
        "best_option_per_metric": ai.best_per_metric(options),
        "mediator_scores": mediation.score_table(scores),
        "scoring_rule": {
            "how": "10 = best of these options for that agent, 0 = worst. Computed by code from the metrics.",
            "weights": {ai.ROLE_NAMES[r]: {k: w for k, w, _ in m} for r, m in mediation.ROLE_METRICS.items()},
            "acceptable_if_at_least": mediation.ACCEPT_MIN_SCORE,
        },
        "note": "best_option_per_metric and mediator_scores are computed by code and are correct. Cite them.",
    }


def _clean(raw: dict, ids: list[str], role: str) -> dict:
    p = ai._clean_position(raw, ids)  # repairs ranking, keeps valid vetoes
    p["acceptable"] = [i for i in (str(x).strip().upper()[:1] for x in raw.get("acceptable") or []) if i in ids]
    p["messages"] = [
        {"from": role, "to": m.get("to") if m.get("to") in ("atc", "pilot", "ground", "all") else "all",
         "type": m.get("type") if m.get("type") in MESSAGE_TYPES else "request",
         "text": str(m.get("text", "")).strip()}
        for m in raw.get("messages") or [] if isinstance(m, dict) and str(m.get("text", "")).strip()
    ][:3]
    if raw.get("vote") in ("ACCEPT", "OBJECT"):
        p["vote"] = raw["vote"]
    return p


def open_position(role: str, event: dict, disruption: dict, options: list[dict], scores: dict) -> tuple[dict, dict]:
    payload = _payload(1, role, event, disruption, options, scores)
    payload["instruction"] = ("Opening position. State your preferred option, every option you could accept, "
                              "and what you need from the other agents.")
    raw, perf = ai._call(ai.ROLE_NAMES[role], system=_system(role), user=json.dumps(payload, indent=1),
                         tool=NEGOTIATE_TOOL)
    return _clean(raw, [o["id"] for o in options], role), perf


def respond(role: str, round_no: int, event: dict, disruption: dict, options: list[dict], scores: dict,
            recommendation: dict, positions: dict, thread: list[dict]) -> tuple[dict, dict]:
    payload = _payload(round_no, role, event, disruption, options, scores)
    payload["mediator_recommendation"] = {
        "option": recommendation["option"],
        "scores": recommendation["scores"],
        "acceptable_to_all_agents": recommendation["acceptable_to_all"],
        "already_rejected": recommendation["rejected_before"],
    }
    payload["other_agents"] = {
        ai.ROLE_NAMES[r]: {"preferred": p["ranking"][0], "acceptable": p.get("acceptable", []),
                           "vetoes": p.get("vetoes", []), "argument": p.get("argument", "")}
        for r, p in positions.items() if r != role
    }
    payload["messages_to_you"] = [m for m in thread if m["to"] in (role, "all") and m["from"] != role]
    payload["full_thread"] = thread
    payload["instruction"] = (f"Vote ACCEPT or OBJECT on option {recommendation['option']}. Reply to every "
                              "message addressed to you. Update your acceptable options if you concede something.")
    raw, perf = ai._call(ai.ROLE_NAMES[role], system=_system(role), user=json.dumps(payload, indent=1),
                         tool=NEGOTIATE_TOOL)
    p = _clean(raw, [o["id"] for o in options], role)
    if "vote" not in p:
        raise ai.AgentCallError(f"{ai.ROLE_NAMES[role]}: no ACCEPT/OBJECT vote on the recommendation")
    return p, perf
