"""Agent calls for the parallel-proposal pipeline (orchestrator_balanced.py).

Two kinds of call, each one independent Anthropic API call per agent:
- propose(): the agent proposes the option it wants, with its red lines, what it can give
  and what it needs.
- review():  the agent ACCEPTs or OBJECTs to the balanced option the (non-AI) balancer picked.

The three agents are called at the same time (run_parallel), so nobody speaks first.
Reuses agents.py: client, MODEL, SYSTEM_PROMPTS, the per-role data view and AgentCallError.
Tool fields are flat (no nested objects) - nested objects made Haiku leak arguments into text.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import anthropic

import agents
import rules
import scoring

ROLES = ("atc", "pilot", "ground")

PROPOSE_TOOL = {
    "name": "submit_proposal",
    "description": "Submit the arrival time and gate you want for this flight.",
    "input_schema": {
        "type": "object",
        "properties": {
            "arrival_time": {"type": "string", "description": "24h HH:MM, e.g. 10:41"},
            "gate": {"type": "string", "description": "One of known_gates."},
            "rationale": {"type": "string", "description": "One or two plain sentences: why this option."},
            "my_red_lines": {"type": "string", "description": "Limits the others must respect, in concrete numbers."},
            "i_can_give": {"type": "string", "description": "Soft preferences you are willing to trade away."},
            "i_need": {"type": "string", "description": "What you need from a named other agent."},
        },
        "required": ["arrival_time", "gate", "rationale", "my_red_lines"],
    },
}

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Accept or object to the balanced proposal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vote": {"type": "string", "enum": ["ACCEPT", "OBJECT"]},
            "rationale": {"type": "string", "description": "One or two plain sentences explaining the vote."},
            "revised_arrival_time": {"type": "string", "description": "Required for OBJECT: 24h HH:MM."},
            "revised_gate": {"type": "string", "description": "Required for OBJECT: one of known_gates."},
            "my_red_lines": {"type": "string", "description": "Limits the others must respect, in concrete numbers."},
            "i_need": {"type": "string", "description": "What you need from a named other agent."},
        },
        "required": ["vote", "rationale"],
    },
}

PROTOCOL_NOTE = (
    "Protocol for this negotiation: all three agents propose at the same time, a deterministic "
    "(non-AI) balancer removes every option that breaks a red line or hard rule and picks the "
    "option where the worst-off agent is best off, then all three agents ACCEPT or OBJECT to it. "
    "There is no COUNTER vote here - to disagree, OBJECT with a revised arrival time and gate."
)


def _system(role: str, state: dict) -> str:
    return "\n\n".join([agents.SYSTEM_PROMPTS[role], rules.role_brief(role, state), PROTOCOL_NOTE])


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


def propose(role: str, state: dict, round_no: int, previous_round: dict | None) -> dict:
    payload = {
        "round": round_no,
        "flight": state["flight"],
        "your_operational_data": agents._role_view(role, state),
        "emergency": state.get("emergency"),
        "previous_round": previous_round,
        "instruction": (
            "Propose the arrival time and gate that is best for YOUR role while keeping every red line "
            "and hard rule. If previous_round is set, read the other agents' objections, red lines and "
            "needs, and move toward an option they can accept. State my_red_lines, i_can_give and "
            "i_need concretely - the others cannot see your data."
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


def review(role: str, state: dict, round_no: int, balanced: dict, proposals: dict) -> dict:
    payload = {
        "round": round_no,
        "your_operational_data": agents._role_view(role, state),
        "emergency": state.get("emergency"),
        "balanced_proposal": balanced["proposal"],
        "balanced_proposal_passed_all_red_lines_and_hard_rules": True,
        "your_score_for_balanced_proposal": scoring.score(role, balanced["proposal"], state),
        "your_accept_min_score": rules.accept_min(role, state),
        "what_each_agent_gives_up": balanced["trade_offs"],
        "this_rounds_proposals": {
            r: {k: p.get(k) for k in ("proposal", "rationale", "my_red_lines", "i_can_give", "i_need")}
            for r, p in proposals.items()
        },
        "instruction": (
            "ACCEPT the balanced proposal if it keeps your red lines and your score is at least your "
            "minimum - nobody gets everything they asked for. OBJECT only if it breaks one of your red "
            "lines or hard rules, or your score is below your minimum. If you OBJECT, give a revised "
            "arrival time and gate that also respects the other agents' red lines."
        ),
    }
    result = _call(role, state, payload, REVIEW_TOOL)
    if result.get("vote") not in ("ACCEPT", "OBJECT"):
        raise agents.AgentCallError(f"{role}: review vote must be ACCEPT or OBJECT")
    arrival, gate = result.pop("revised_arrival_time", None), result.pop("revised_gate", None)
    if result["vote"] == "OBJECT" and arrival and gate:
        result["revised_proposal"] = {
            "revised_arrival_time": str(arrival).strip(),
            "gate_assignment": str(gate).strip(),
        }
    return result


def run_parallel(fn_name: str, *args) -> dict:
    """Call propose/review for all three roles at the same time. Looked up by name so tests
    can monkeypatch agents_balanced.propose / agents_balanced.review."""
    fn = globals()[fn_name]
    with ThreadPoolExecutor(max_workers=len(ROLES)) as pool:
        futures = {role: pool.submit(fn, role, *args) for role in ROLES}
        return {role: f.result() for role, f in futures.items()}  # re-raises AgentCallError
