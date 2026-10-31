"""Three independent LLM agents: ATC, Pilot, Ground.

Each agent is a separate Anthropic API call with only the structured messages
addressed to it - no shared prompt, no shared memory object. Output is forced into
a strict JSON shape via tool-use so it's always machine-parseable.
"""

import json
import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


RESPONSE_TOOL = {
    "name": "submit_response",
    "description": "Submit your structured vote/response on the current proposal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vote": {
                "type": "string",
                "enum": ["ACCEPT", "REJECT", "COUNTER"],
                "description": "ACCEPT the current proposal as-is, REJECT with no alternative, "
                "or COUNTER with a replacement proposal.",
            },
            "counter_proposal": {
                "type": "object",
                "description": "Required when vote is COUNTER. Omit otherwise.",
                "properties": {
                    "revised_arrival_time": {"type": "string", "description": "HH:MM, 24h"},
                    "gate_assignment": {"type": "string"},
                },
                "required": ["revised_arrival_time", "gate_assignment"],
            },
            "rationale": {
                "type": "string",
                "description": "One or two sentences explaining the vote from your role's objectives.",
            },
            "utility_fields": {
                "type": "object",
                "description": "Numeric fields relevant to your role's objectives, your own honest estimate.",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["vote", "rationale", "utility_fields"],
    },
}

SYSTEM_PROMPTS = {
    "atc": (
        "You are an Air Traffic Control AI agent. Your objectives, in priority order: "
        "(1) maintain safe runway wake separation from other scheduled traffic, "
        "(2) avoid taxiway/gate blockage, (3) maximize sector throughput. "
        "You do not control fuel or gate logistics - focus your utility_fields on "
        "sector_throughput_score (0-10, higher=better) and wake_separation_margin_minutes. "
        "You will be told if a proposal already fails a hard constraint check - never ACCEPT "
        "a proposal that failed that check; COUNTER instead with a safer alternative."
    ),
    "pilot": (
        "You are a Pilot AI agent for this specific flight. Your objectives, in priority order: "
        "(1) flight safety and minimum fuel reserve margins, (2) minimize turbulence/holding, "
        "(3) protect connecting passengers' onward itineraries. "
        "Focus your utility_fields on fuel_reserve_margin_minutes and connections_at_risk. "
        "You will be told if a proposal already fails a hard constraint check - never ACCEPT "
        "a proposal that failed that check; COUNTER instead with a safer alternative."
    ),
    "ground": (
        "You are a Ground Operations AI agent at the destination airport. Your objectives, "
        "in priority order: (1) avoid ground-crew overtime, (2) optimize gate utilization "
        "(avoid conflicts), (3) minimize turnaround delay. "
        "Focus your utility_fields on overtime_risk_minutes and gate_conflict_risk (0-10). "
        "You will be told if a proposal already fails a hard constraint check - never ACCEPT "
        "a proposal that failed that check; COUNTER instead with a safer alternative."
    ),
}


def _build_user_message(role: str, state: dict, current_proposal: dict, constraint_result: tuple, turn: int) -> str:
    ok, violations = constraint_result
    payload = {
        "turn": turn,
        "flight": state["flight"],
        "scenario_derived_fields": state["derived"],
        "current_proposal": current_proposal,
        "constraint_check_on_current_proposal": {
            "passed": ok,
            "violations": violations,
        },
        "instruction": (
            f"As the {role} agent, vote on current_proposal using the submit_response tool. "
            "If constraint_check_on_current_proposal.passed is false, you MUST NOT vote ACCEPT - "
            "vote COUNTER with a revised proposal, or REJECT."
        ),
    }
    return json.dumps(payload, indent=2)


class AgentCallError(Exception):
    """Raised when an agent's API call fails or returns something unusable.
    Carries a short, user-facing message - callers should stop the negotiation,
    not guess at a substitute response."""


def call_agent(role: str, state: dict, current_proposal: dict, constraint_result: tuple, turn: int) -> dict:
    """One independent API call. Returns the parsed structured response dict, plus 'from'/'turn'.
    Raises AgentCallError on any API failure or malformed response."""
    client = _get_client()
    user_message = _build_user_message(role, state, current_proposal, constraint_result, turn)

    start = time.monotonic()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPTS[role],
            tools=[RESPONSE_TOOL],
            tool_choice={"type": "tool", "name": "submit_response"},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.AuthenticationError as e:
        raise AgentCallError(f"{role}: invalid API key - check your .env file") from e
    except anthropic.RateLimitError as e:
        raise AgentCallError(f"{role}: rate limited or out of credits - wait a bit and try again") from e
    except anthropic.APIConnectionError as e:
        raise AgentCallError(f"{role}: can't reach Claude - check your internet connection") from e
    except anthropic.APIStatusError as e:
        raise AgentCallError(f"{role}: API error ({e.status_code}): {e.message}") from e

    try:
        tool_use_block = next(b for b in resp.content if b.type == "tool_use")
    except StopIteration as e:
        raise AgentCallError(f"{role}: model did not return a structured response") from e

    result = dict(tool_use_block.input)
    if "vote" not in result:
        raise AgentCallError(f"{role}: structured response is missing the required 'vote' field")

    result["from"] = role
    result["turn"] = turn
    result["proposal_under_review"] = current_proposal
    result["_perf"] = {
        "latency_seconds": round(time.monotonic() - start, 3),
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return result
