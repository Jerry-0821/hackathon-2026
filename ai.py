"""Claude calls for the runway-closure cascade.

The split between LLM and code is deliberate:
- The LLM reads messy text (NOTAM, METAR, ops notes) and turns it into structured fields.
  Code then validates every field against the airport data and converts the times.
- The solver (solver.py, no LLM) generates the options and guarantees they are safe.
- The LLM agents rank the options from their own side and argue trade-offs.
  Code (negotiate.py) applies a fixed voting rule to pick the outcome.
- The LLM writes the plain-English decision brief.

Every call is one independent API request. Each agent sees only its own role prompt and
the structured messages addressed to it.
"""

from __future__ import annotations

import json
import re
import time

import anthropic

import agents
from agents import AgentCallError

MODEL = agents.MODEL


def _call(label: str, *, system: str, user: str, tool: dict | None = None, max_tokens: int = 1500) -> tuple[dict | str, dict]:
    """One API call. Returns (tool input dict or text, perf). Raises AgentCallError."""
    kwargs = {"model": MODEL, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]}
    if tool:
        kwargs["tools"] = [tool]
        kwargs["tool_choice"] = {"type": "tool", "name": tool["name"]}
    start = time.monotonic()
    try:
        resp = agents._get_client().messages.create(**kwargs)
    except anthropic.AuthenticationError as e:
        raise AgentCallError(f"{label}: invalid API key - check your .env file") from e
    except anthropic.RateLimitError as e:
        raise AgentCallError(f"{label}: rate limited or out of credits - wait a bit and try again") from e
    except anthropic.APIConnectionError as e:
        raise AgentCallError(f"{label}: can't reach Claude - check your internet connection") from e
    except anthropic.APIStatusError as e:
        raise AgentCallError(f"{label}: API error ({e.status_code}): {e.message}") from e
    perf = {"latency_seconds": round(time.monotonic() - start, 2),
            "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    if tool:
        block = next((b for b in resp.content if b.type == "tool_use"), None)
        if block is None:
            raise AgentCallError(f"{label}: model did not return a structured response")
        return dict(block.input), perf
    return "".join(b.text for b in resp.content if b.type == "text").strip(), perf


# ---------------------------------------------------------------------------
# 1. Intake: messy text -> structured disruption
# ---------------------------------------------------------------------------

INTAKE_TOOL = {
    "name": "submit_disruption",
    "description": "Submit the operational disruption extracted from the raw messages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "runway_closures": {
                "type": "array",
                "description": "Every runway closure mentioned. Empty if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "runway": {"type": "string", "description": "Runway as written, e.g. 08L/26R"},
                        "start": {"type": "string", "description": "Start time as HHMM, copied from the message"},
                        "end": {"type": "string", "description": "End time as HHMM, copied from the message"},
                        "time_basis": {"type": "string", "enum": ["UTC", "local"],
                                       "description": "NOTAM and METAR times are UTC. Ops notes usually use local time."},
                        "quote": {"type": "string", "description": "The exact text this came from"},
                    },
                    "required": ["runway", "start", "end", "time_basis", "quote"],
                },
            },
            "gates_out_of_service": {
                "type": "array",
                "description": "Every gate that must not be used. Empty if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "gate": {"type": "string"},
                        "reason": {"type": "string"},
                        "quote": {"type": "string", "description": "The exact text this came from"},
                    },
                    "required": ["gate", "reason", "quote"],
                },
            },
            "ceiling_ft": {"type": "integer",
                           "description": "Lowest BKN, OVC or VV layer in feet (BKN008 = 800). Use -1 if there is none."},
            "visibility_sm": {"type": "number", "description": "Visibility in statute miles (2 1/2SM = 2.5)"},
            "other_notes": {"type": "array", "items": {"type": "string"},
                            "description": "Other operational facts that do not fit the fields above, e.g. staff shortages"},
            "summary": {"type": "string", "description": "One plain sentence describing the situation"},
        },
        "required": ["runway_closures", "gates_out_of_service", "ceiling_ft", "visibility_sm", "other_notes", "summary"],
    },
}

INTAKE_SYSTEM = (
    "You are an airport operations intake assistant. You read raw NOTAMs, METARs and free-text "
    "ops notes and extract the facts into the submit_disruption tool. Copy times exactly as "
    "written and say whether they are UTC or local; do not convert them. Only report what the "
    "text says. Do not guess."
)


def parse_disruption(texts: dict, event: dict) -> tuple[dict, dict]:
    """Returns (raw extracted fields, perf). Validate with validate_disruption()."""
    user = json.dumps({
        "airport": event["airport"],
        "local_date": event["date"],
        "messages": {"NOTAM": texts.get("notam", ""), "METAR": texts.get("metar", ""),
                     "ops_note": texts.get("ops_note", "")},
    }, indent=1)
    return _call("intake", system=INTAKE_SYSTEM, user=user, tool=INTAKE_TOOL)


def _norm_runway(s: str) -> str:
    return s.upper().replace("RWY", "").replace("RUNWAY", "").replace(" ", "").strip()


def _match_runway(written: str, runways: list[str]) -> str | None:
    w = _norm_runway(written)
    for rwy in runways:
        if w == rwy or w in rwy.split("/") or set(w.split("/")) == set(rwy.split("/")):
            return rwy
    return None


def _parse_hhmm(s: str) -> int | None:
    digits = "".join(ch for ch in str(s) if ch.isdigit())[-4:]
    if len(digits) != 4:
        return None
    hh, mm = int(digits[:2]), int(digits[2:])
    if hh > 23 or mm > 59:
        return None
    return hh * 60 + mm


def validate_disruption(raw: dict, event: dict) -> tuple[dict, list[str]]:
    """Deterministic check of the LLM's extraction. Unknown runways or gates and bad times
    are dropped with a warning. UTC times are converted to local here, not by the LLM."""
    warnings: list[str] = []
    offset_min = event["utc_offset"] * 60
    closures = []
    for c in raw.get("runway_closures") or []:
        rwy = _match_runway(c.get("runway", ""), event["arrival_runways"])
        if rwy is None:
            warnings.append(f"Runway '{c.get('runway')}' is not one of the modelled arrival runways "
                            f"{event['arrival_runways']} - ignored.")
            continue
        start, end = _parse_hhmm(c.get("start", "")), _parse_hhmm(c.get("end", ""))
        if start is None or end is None:
            warnings.append(f"Could not read the closure times for {rwy} ('{c.get('start')}'-'{c.get('end')}') - ignored.")
            continue
        if c.get("time_basis") == "UTC":
            start, end = (start + offset_min) % 1440, (end + offset_min) % 1440
        if end <= start:
            end += 1440
        closures.append({"runway": rwy, "start_min": start, "end_min": end, "quote": c.get("quote", "")})

    gate_ids = {g["id"] for g in event["gates"]}
    gates_out = []
    for g in raw.get("gates_out_of_service") or []:
        gid = str(g.get("gate", "")).upper().replace("GATE", "").strip()
        if gid not in gate_ids:
            warnings.append(f"Gate '{g.get('gate')}' is not on this pier ({', '.join(sorted(gate_ids))}) - ignored.")
            continue
        if gid not in {x["gate"] for x in gates_out}:
            gates_out.append({"gate": gid, "reason": g.get("reason", ""), "quote": g.get("quote", "")})

    ceiling = raw.get("ceiling_ft")
    ceiling = None if ceiling is None or ceiling < 0 else int(ceiling)
    try:
        vis = float(raw.get("visibility_sm"))
    except (TypeError, ValueError):
        vis = 10.0
        warnings.append("Visibility missing - assumed 10 SM.")
    rules = "IMC" if (ceiling is not None and ceiling < 1000) or vis < 3 else "VMC"

    return {
        "runway_closures": closures,
        "gates_out_of_service": gates_out,
        "ceiling_ft": ceiling,
        "visibility_sm": vis,
        "flight_rules": rules,
        "other_notes": list(raw.get("other_notes") or []),
        "summary": raw.get("summary", ""),
    }, warnings


def compare_to_truth(parsed: dict, truth: dict) -> dict:
    """How well the intake matched the ground truth used to write the messages."""
    pc = {(c["runway"], c["start_min"], c["end_min"]) for c in parsed["runway_closures"]}
    tc = {(c["runway"], c["start_min"], c["end_min"]) for c in truth["runway_closures"]}
    return {
        "runway_closure": pc == tc,
        "gates_out": {g["gate"] for g in parsed["gates_out_of_service"]} == {g["gate"] for g in truth["gates_out_of_service"]},
        "flight_rules": parsed["flight_rules"] == truth["flight_rules"],
    }


# ---------------------------------------------------------------------------
# 2. Agents rank the options and argue trade-offs
# ---------------------------------------------------------------------------

RANK_TOOL = {
    "name": "submit_ranking",
    "description": "Submit your ranking of the options and your argument.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ranking": {"type": "array", "items": {"type": "string"},
                        "description": "Every option ID exactly once, best first for your role, e.g. [\"C\",\"A\",\"B\"]"},
            "vetoes": {"type": "array",
                       "description": "Options you cannot accept, with a reason that cites a number. Usually empty.",
                       "items": {"type": "object",
                                 "properties": {"option": {"type": "string"}, "reason": {"type": "string"}},
                                 "required": ["option", "reason"]}},
            "reply": {"type": "string",
                      "description": "Round 2 only: answer the strongest objection from another agent, by name. "
                                     "Say what you concede and what you hold. Empty in round 1."},
            "argument": {"type": "string",
                         "description": "Two or three plain sentences. Cite the numbers behind your top choice."},
        },
        "required": ["ranking", "vetoes", "reply", "argument"],
    },
}

_COMMON = (
    " Every option has already passed a deterministic safety check (runway separation, final "
    "reserve fuel, gate availability), so you are choosing between trade-offs, not between safe "
    "and unsafe. A fixed voting rule combines the three agents' rankings, so rank honestly from "
    "your side, and veto only an option you truly cannot accept. Use the submit_ranking tool."
)

ROLE_PROMPTS = {
    "atc": ("You are the Air Traffic Control agent for the arrival flow. Your priorities: keep "
            "traffic moving and minimise total delay and airborne holding, keep the sequence "
            "orderly, and clear the bank quickly." + _COMMON),
    "pilot": ("You are the Flight Crew agent, speaking for the pilots and the airline's flight "
              "operations on every inbound flight. Your priorities: keep fuel margin above final "
              "reserve as large as possible, avoid diversions, minimise holding, and protect "
              "passengers' onward connections." + _COMMON),
    "ground": ("You are the Ground Operations agent for the gate pier. Your priorities: avoid "
               "knock-on delay to the next departure of each aircraft, avoid ground crew overtime, "
               "and avoid gate changes and gate waits." + _COMMON),
}

ROLE_NAMES = {"atc": "ATC", "pilot": "Flight Crew", "ground": "Ground Ops"}

_SUMMARY_KEYS = ["diversions", "total_delay_min", "max_delay_min", "pax_missed", "min_fuel_margin_min",
                 "gate_changes", "gate_wait_min", "downstream_delay_min", "crew_overtime_min", "last_landing"]


# For each metric, which direction is better. Used to give the LLM code-computed comparisons,
# because small models misread number tables.
_BETTER = {"diversions": "lower", "total_delay_min": "lower", "max_delay_min": "lower", "pax_missed": "lower",
           "min_fuel_margin_min": "higher", "gate_changes": "lower", "gate_wait_min": "lower",
           "downstream_delay_min": "lower", "crew_overtime_min": "lower"}


def best_per_metric(options: list[dict]) -> dict:
    out = {}
    for k, direction in _BETTER.items():
        vals = {o["id"]: o["metrics"][k] for o in options}
        best = min(vals.values()) if direction == "lower" else max(vals.values())
        out[k] = {"best_options": [i for i, v in vals.items() if v == best], "best_value": best,
                  "better_is": direction}
    return out


def compare_options(winner: dict, other: dict) -> dict:
    """Plain statements of how `other` differs from `winner`, computed in code."""
    out = {}
    for k, direction in _BETTER.items():
        a, b = winner["metrics"][k], other["metrics"][k]
        if a == b:
            continue
        better = (b < a) if direction == "lower" else (b > a)
        out[k] = f"{other['id']} has {b} vs {winner['id']} {a} ({'better' if better else 'worse'} for {other['id']})"
    return out


def _option_view(role: str, opt: dict) -> dict:
    """Shared option summary plus per-flight details for this role only."""
    view = {"id": opt["id"], "name": opt["name"],
            "summary": {k: opt["metrics"][k] for k in _SUMMARY_KEYS}}
    flights = []
    for r in opt["rows"]:
        if r["diverted"]:
            flights.append({"flight": r["flight"], "outcome": "DIVERTS (fuel)"})
        elif role == "atc":
            flights.append({"flight": r["flight"], "lands": r["landing"], "holding_min": r["holding_min"]})
        elif role == "pilot":
            flights.append({"flight": r["flight"], "holding_min": r["holding_min"],
                            "fuel_margin_min": r["fuel_margin_min"], "fuel_margin_kg": r["fuel_margin_kg"],
                            "pax_missed": r["pax_missed"]})
        else:
            flights.append({"flight": r["flight"], "gate": r["gate"], "gate_changed": r["gate_changed"],
                            "gate_wait_min": r["gate_wait_min"], "knock_on_min": r["knock_on_min"]})
    view["per_flight"] = flights
    return view


ROLE_ORDER_SHIFT = {"atc": 0, "pilot": 1, "ground": 2}


def _rotate(items: list, shift: int) -> list:
    shift %= len(items)
    return items[shift:] + items[:shift]


def rank_options(role: str, round_no: int, event: dict, disruption: dict, options: list[dict],
                 others: dict | None = None) -> tuple[dict, dict]:
    """One agent's ranking. `others` maps role -> that agent's round-1 position (round 2 only).
    Returns (position, perf). The ranking is repaired if the model drops or repeats an ID."""
    payload = {
        "round": round_no,
        "airport": event["airport"],
        "situation": disruption.get("summary") or "One arrival runway closed; see options.",
        "flight_rules": disruption["flight_rules"],
        # Each role sees the options in a different order, so no option gains from being listed first.
        "options": [_option_view(role, o) for o in _rotate(options, ROLE_ORDER_SHIFT[role])],
        "best_option_per_metric": best_per_metric(options),
        "note": "best_option_per_metric is computed by code and is correct. Use it when you cite numbers.",
    }
    if others:
        payload["other_agents_round_1"] = {
            ROLE_NAMES[r]: {"ranking": p["ranking"], "vetoes": p["vetoes"], "argument": p["argument"]}
            for r, p in others.items()
        }
        payload["instruction"] = ("Round 2. Read the other agents' positions. You may change your ranking. "
                                  "Fill in 'reply' with what you concede and what you hold.")
    raw, perf = _call(ROLE_NAMES[role], system=ROLE_PROMPTS[role], user=json.dumps(payload, indent=1), tool=RANK_TOOL)
    return _clean_position(raw, [o["id"] for o in options]), perf


def option_id(value, ids: list[str]) -> str | None:
    """Read an option ID out of whatever the model wrote: "A", "a", "Option A", "A (Fastest flow)".
    Returns None if no known ID is found."""
    text = str(value).strip()
    if text.upper() in ids:
        return text.upper()
    for match in re.findall(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])", text):
        if match.upper() in ids:
            return match.upper()
    return None


def _clean_position(raw: dict, ids: list[str]) -> dict:
    ranking, repaired = [], False
    for x in raw.get("ranking") or []:
        oid = option_id(x, ids)
        if oid and oid not in ranking:
            ranking.append(oid)
        else:
            repaired = True
    missing = [i for i in ids if i not in ranking]
    if missing:
        repaired = True
        ranking += missing
    vetoes = [{"option": option_id(v.get("option", ""), ids), "reason": v.get("reason", "")}
              for v in raw.get("vetoes") or [] if isinstance(v, dict)]
    vetoes = [v for v in vetoes if v["option"]]
    return {"ranking": ranking, "vetoes": vetoes, "argument": raw.get("argument", ""),
            "reply": raw.get("reply", ""), "ranking_repaired": repaired, "source": "llm"}


# ---------------------------------------------------------------------------
# 3. Decision brief
# ---------------------------------------------------------------------------

BRIEF_SYSTEM = (
    "You write short decision briefs for an airport operations team. Plain English, no jargon "
    "beyond standard aviation terms, no headings. Four to six sentences. Say what was decided, "
    "why, who gives up what compared with the other options, and one thing to watch. Use the "
    "numbers given. Do not invent facts. Plain text only: no markdown, no bold, no title line."
)


def write_brief(event: dict, disruption: dict, decision: dict, options: list[dict], positions: dict) -> tuple[str, dict]:
    winner = next(o for o in options if o["id"] == decision["winner"])
    payload = {
        "airport": event["airport"],
        "situation": disruption.get("summary", ""),
        "decision": {"option": winner["id"], "name": winner["name"], "metrics": winner["metrics"]},
        "other_options": [{"option": o["id"], "name": o["name"], "metrics": o["metrics"],
                           "compared_with_decision": compare_options(winner, o)}
                          for o in options if o["id"] != winner["id"]],
        "note": "compared_with_decision is computed by code and is correct. Base every comparison on it.",
        "votes": decision["borda"],
        "vetoes": decision["vetoes"],
        "agent_arguments": {ROLE_NAMES[r]: p["argument"] for r, p in positions.items()},
    }
    return _call("brief", system=BRIEF_SYSTEM, user=json.dumps(payload, indent=1), max_tokens=600)
