"""Agent calls for the parallel-proposal pipeline (orchestrator_balanced.py).

Each agent only sees its own domain's data (private_view) plus the shared flight row and rulebook.
It tells the others about its limits and preferences by PUBLISHING a structured declaration.

Two calls, each one independent Anthropic API call per agent, all three at the same time:
- propose(): proposal + declaration (blocked slots, latest arrival, preferred window/gate, needs)
- review():  ACCEPT or OBJECT to the mediator's balanced option, optionally updating the declaration

Reuses agents.py: client, MODEL, _role_view and AgentCallError. Tool fields are flat strings
(nested objects made Haiku leak arguments into the rationale text).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic

import agents
import rules
from scenario import hhmm

ROLES = ("atc", "pilot", "ground")

OBJECTIVES = {
    "atc": "You are the Air Traffic Control AI agent. Your objectives, in priority order: (1) runway wake "
    "separation from other traffic, (2) no taxiway blockage, (3) maximise sector throughput (little holding, "
    "no slot squeezed between other arrivals).",
    "pilot": "You are the Pilot AI agent for this flight. Your objectives, in priority order: (1) flight safety "
    "and fuel reserves, (2) minimise holding and turbulence, (3) protect connecting passengers.",
    "ground": "You are the Ground Operations AI agent at the destination airport. Your objectives, in priority "
    "order: (1) avoid ground-crew overtime, (2) gate utilisation without conflicts, (3) minimise turnaround delay "
    "(the crew should be free when the aircraft arrives).",
}

PROTOCOL = (
    "Protocol: the three agents work for different organisations and only see their own data. All three "
    "propose at the same time and PUBLISH their limits and preferences as structured fields - that is the only "
    "way the others learn about them. A deterministic (non-AI) mediator removes every option that breaks a red "
    "line or a declared blocked slot, scores each option for each agent from that agent's declared preferences, "
    "and picks the option where the worst-off agent is best off. Then all three ACCEPT or OBJECT. "
    "Be precise and honest: declare only real limits as blocked, and mark a preference 'high' only if it matters."
)

DECLARATION_FIELDS = {
    "blocked_slots": {"type": "string", "description": "Times you cannot accept because of YOUR red lines or hard "
                      "rules. Format 'HH:MM-HH:MM GATE' or 'HH:MM-HH:MM any', separated by ';'. "
                      "Example: '10:34-10:38 any; 10:28-11:03 D10'."},
    "latest_acceptable_arrival": {"type": "string", "description": "Latest arrival your red lines allow, HH:MM."},
    "preferred_window": {"type": "string", "description": "Arrival window you prefer, 'HH:MM-HH:MM'."},
    "preferred_window_importance": {"type": "string", "enum": ["high", "low"]},
    "preferred_gate": {"type": "string", "description": "Gate you prefer, only if you care about the gate."},
    "preferred_gate_importance": {"type": "string", "enum": ["high", "low"]},
    "i_can_give": {"type": "string", "description": "Soft preferences you are willing to trade away."},
    "i_need": {"type": "string", "description": "What you need from a named other agent."},
}
DECLARATION_KEYS = tuple(DECLARATION_FIELDS)

PROPOSE_TOOL = {
    "name": "submit_proposal",
    "description": "Submit your proposal and publish your limits and preferences.",
    "input_schema": {
        "type": "object",
        "properties": {
            "arrival_time": {"type": "string", "description": "24h HH:MM, e.g. 10:41"},
            "gate": {"type": "string", "description": "One of known_gates."},
            "rationale": {"type": "string", "description": "One or two plain sentences."},
            **DECLARATION_FIELDS,
        },
        "required": ["arrival_time", "gate", "rationale", "preferred_window", "preferred_window_importance"],
    },
}

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Accept or object to the mediator's balanced option.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vote": {"type": "string", "enum": ["ACCEPT", "OBJECT"]},
            "objection_reason": {"type": "string", "enum": ["red_line", "hard_rule", "preference"],
                                 "description": "Required for OBJECT: which kind of limit the option breaks for you."},
            "rationale": {"type": "string", "description": "One or two plain sentences."},
            "revised_arrival_time": {"type": "string", "description": "For OBJECT: 24h HH:MM you would accept."},
            "revised_gate": {"type": "string", "description": "For OBJECT: gate you would accept."},
            **{k: {**v, "description": v.get("description", "") + " Only fill in to UPDATE your declaration."}
               for k, v in DECLARATION_FIELDS.items()},
        },
        "required": ["vote", "rationale"],
    },
}


def private_view(role: str, state: dict) -> dict:
    """Only this role's own data. Extends agents._role_view with the soft-preference facts the
    role needs to argue for itself (connection cut-offs, when the crew is free, nearest gate)."""
    view = agents._role_view(role, state)
    d, naive = state["derived"], state["naive_arrival_minutes"]
    if role == "pilot" and d.get("connection_cutoff_offsets"):
        cut = d["connection_cutoff_offsets"]
        view["connections_still_made_if_arriving_by"] = {
            **{g: hhmm(naive + off) for g, off in cut.items() if g != "default"},
            "any other gate": hhmm(naive + cut["default"]),
        }
    if role == "ground":
        view["crew_free_from"] = hhmm(naive + d.get("crew_ready_offset", 0))
        view["gate_nearest_crew_base"] = d.get("ground_preferred_gate", d["current_gate"])
        view.pop("requested_gate", None)  # the pilot's / airline's wish, not ground's own data
    view["emergency"] = state.get("emergency")
    return view


def _system(role: str, state: dict) -> str:
    return "\n\n".join([OBJECTIVES[role], rules.role_brief(role, state), PROTOCOL])


def _call(role: str, state: dict, payload: dict, tool: dict) -> dict:
    """One independent API call forced to answer with `tool`. Error handling mirrors agents.call_agent."""
    start = time.monotonic()
    try:
        resp = agents._get_client().messages.create(
            model=agents.MODEL,
            max_tokens=1024,
            system=_system(role, state),
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        )
    except anthropic.AuthenticationError as e:
        raise agents.AgentCallError(f"{role}: invalid API key - check your .env file") from e
    except anthropic.RateLimitError as e:
        raise agents.AgentCallError(f"{role}: rate limited or out of credits - wait a bit and try again") from e
    except anthropic.APIConnectionError as e:
        raise agents.AgentCallError(f"{role}: can't reach Claude - check your internet connection") from e
    except anthropic.APIStatusError as e:
        raise agents.AgentCallError(f"{role}: API error ({e.status_code}): {e.message}") from e

    block = next((b for b in resp.content if b.type == "tool_use"), None)
    if block is None:
        raise agents.AgentCallError(f"{role}: model did not return a structured response")
    result = dict(block.input)
    result["from"] = role
    result["_perf"] = {
        "latency_seconds": round(time.monotonic() - start, 3),
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return result


def propose(role: str, state: dict, round_no: int, context: dict) -> dict:
    """context: {"published": {role: declaration}, "previous_round": {...} or None}"""
    payload = {
        "round": round_no,
        "flight": state["flight"],
        "your_private_data": private_view(role, state),
        "declarations_published_by_other_agents": {r: d for r, d in context["published"].items() if r != role},
        "previous_round": context.get("previous_round"),
        "instruction": (
            "Propose the arrival time and gate that is best for YOUR role and keeps every red line. "
            "Publish blocked_slots and latest_acceptable_arrival from YOUR data, and your preferred window/gate. "
            "From round 2, read the other agents' declarations and objections and move toward an option "
            "that fits their limits too."
        ),
    }
    result = _call(role, state, payload, PROPOSE_TOOL)
    if not result.get("arrival_time") or not result.get("gate"):
        raise agents.AgentCallError(f"{role}: proposal is missing arrival_time or gate")
    result["proposal"] = {
        "revised_arrival_time": str(result.pop("arrival_time")).strip(),
        "gate_assignment": str(result.pop("gate")).strip(),
    }
    return result


def review(role: str, state: dict, round_no: int, mediated: dict, published: dict) -> dict:
    mine = next(t for t in mediated["trade_offs"] if t["role"] == role)
    payload = {
        "round": round_no,
        "your_private_data": private_view(role, state),
        "balanced_option": mediated["proposal"],
        "balanced_option_passed_every_safety_check": True,
        "your_score_from_your_declared_preferences": mediated["scores"][role],
        "why_your_score_is_not_10": mine["why"],
        "your_accept_min_score": rules.accept_min(role, state),
        "all_scores": mediated["scores"],
        "all_published_declarations": published,
        "instruction": (
            "ACCEPT if the balanced option keeps your red lines and hard rules and your score is at least your "
            "minimum - nobody gets everything. OBJECT only if it breaks a red line or hard rule for you, or your "
            "score is below your minimum; then set objection_reason, give a revised arrival time and gate that "
            "also fits the other agents' published limits, and update your declaration if it was wrong or missing."
        ),
    }
    result = _call(role, state, payload, REVIEW_TOOL)
    if result.get("vote") not in ("ACCEPT", "OBJECT"):
        raise agents.AgentCallError(f"{role}: review vote must be ACCEPT or OBJECT")
    arrival, gate = result.pop("revised_arrival_time", None), result.pop("revised_gate", None)
    if result["vote"] == "OBJECT" and arrival and gate:
        result["revised_proposal"] = {"revised_arrival_time": str(arrival).strip(), "gate_assignment": str(gate).strip()}
    return result


def declaration_of(response: dict) -> dict:
    """The public part of a propose/review response: what the other agents and the mediator see."""
    return {k: response[k] for k in DECLARATION_KEYS if response.get(k)}


def run_parallel(fn_name: str, *args) -> dict:
    """Call propose/review for all three roles at the same time. Looked up by name so tests
    can monkeypatch agents_balanced.propose / agents_balanced.review."""
    fn = globals()[fn_name]
    with ThreadPoolExecutor(max_workers=len(ROLES)) as pool:
        futures = {role: pool.submit(fn, role, *args) for role in ROLES}
        return {role: f.result() for role, f in futures.items()}  # re-raises AgentCallError
