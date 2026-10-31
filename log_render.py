"""Turns a consensus_log.json-shaped dict into a single self-contained static HTML page:
a timeline of every turn/agent/constraint-check, plus the final outcome banner."""

from __future__ import annotations

import html
import json

VOTE_COLOR = {"ACCEPT": "#1a7f37", "REJECT": "#b42318", "COUNTER": "#9a6700"}

CSS = """
body { font-family: -apple-system, Segoe UI, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fff; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: .25rem; }
.scenario { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 8px; padding: 1rem; margin-bottom: 1.5rem; }
.event { border: 1px solid #d0d7de; border-radius: 8px; padding: .75rem 1rem; margin: .5rem 0; }
.validation-ok { border-left: 4px solid #1a7f37; }
.validation-fail { border-left: 4px solid #b42318; background: #fff3f2; }
.agent { border-left: 4px solid #888; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 4px; color: white; font-size: .8rem; font-weight: 600; }
.outcome { border-radius: 8px; padding: 1rem; margin-top: 1.5rem; font-weight: 600; }
.outcome-consensus { background: #dafbe1; border: 1px solid #1a7f37; }
.outcome-fallback { background: #fff8c5; border: 1px solid #9a6700; }
pre { background: #f6f8fa; padding: .5rem; border-radius: 6px; overflow-x: auto; font-size: .85rem; }
"""


def _esc(s) -> str:
    return html.escape(str(s))


def render(log: dict) -> str:
    sc = log["scenario"]["flight"]
    derived = log["scenario"]["derived"]
    parts = [f"<html><head><meta charset='utf-8'><title>ATC Negotiation Log</title><style>{CSS}</style></head><body>"]
    parts.append("<h1>Multi-Agent ATC Negotiation — Consensus Log</h1>")
    parts.append(
        "<div class='scenario'>"
        f"<b>{_esc(sc['airline'])} {_esc(sc['flight_number'])}</b> "
        f"{_esc(sc['origin_airport'])} &rarr; {_esc(sc['destination_airport'])} on {_esc(sc['date'])}<br>"
        f"Scheduled arrival {_esc(sc['scheduled_arrival'])}, "
        f"{_esc(sc['delay_cause'].replace('_', ' '))} delay {_esc(sc['primary_delay_minutes'])} min, "
        f"naive (no-negotiation) arrival {_esc(log['scenario']['naive_arrival'])}<br>"
        f"Fuel reserve at naive arrival: {_esc(derived['fuel_reserve_minutes_at_naive_arrival'])} min | "
        f"current gate {_esc(derived['current_gate'])} | connecting passengers {_esc(derived['connecting_passengers'])}"
        "</div>"
    )

    for turn_log in log["turns"]:
        parts.append(f"<h2>Turn {turn_log['turn']}</h2>")
        for ev in turn_log["events"]:
            if ev["type"] == "validation":
                cls = "validation-ok" if ev["passed"] else "validation-fail"
                status = "PASSED" if ev["passed"] else "FAILED"
                viol = "".join(f"<li>{_esc(v)}</li>" for v in ev["violations"])
                parts.append(
                    f"<div class='event {cls}'><b>Constraint check: {status}</b> "
                    f"for proposal <code>{_esc(json.dumps(ev['proposal']))}</code>"
                    + (f"<ul>{viol}</ul>" if viol else "")
                    + "</div>"
                )
            elif ev["type"] == "agent_response":
                r = ev["response"]
                color = VOTE_COLOR.get(r["vote"], "#555")
                counter = (
                    f"<br>Counter-proposal: <code>{_esc(json.dumps(r.get('counter_proposal')))}</code>"
                    if r["vote"] == "COUNTER"
                    else ""
                )
                parts.append(
                    "<div class='event agent'>"
                    f"<b>{_esc(r['from'].upper())}</b> "
                    f"<span class='badge' style='background:{color}'>{_esc(r['vote'])}</span><br>"
                    f"{_esc(r['rationale'])}{counter}"
                    f"<pre>{_esc(json.dumps(r.get('utility_fields', {}), indent=2))}</pre>"
                    "</div>"
                )
        summary = turn_log["summary"]
        m = summary["mediator"]
        if m["consensus"]:
            parts.append(f"<div class='event validation-ok'><b>Mediator: CONSENSUS reached</b> on {_esc(json.dumps(summary['final_proposal']))}</div>")
        else:
            parts.append(f"<div class='event validation-fail'><b>Mediator: no consensus</b> — {_esc('; '.join(m['reasons']))}</div>")

    outcome = log["outcome"]
    if outcome["status"] == "consensus":
        parts.append(
            f"<div class='outcome outcome-consensus'>FINAL OUTCOME — Consensus reached at turn "
            f"{outcome['turn_reached']}: revised arrival {_esc(outcome['proposal']['revised_arrival_time'])}, "
            f"gate {_esc(outcome['proposal']['gate_assignment'])}</div>"
        )
    else:
        fb = outcome["fallback_detail"]
        parts.append(
            f"<div class='outcome outcome-fallback'>FINAL OUTCOME — Deterministic fallback fired "
            f"({_esc(fb['source'])}): {_esc(fb['reason'])}<br>"
            f"Fallback proposal: arrival {_esc(outcome['proposal']['revised_arrival_time'])}, "
            f"gate {_esc(outcome['proposal']['gate_assignment'])}</div>"
        )

    parts.append("</body></html>")
    return "\n".join(parts)


def render_to_file(log: dict, path: str = "negotiation_log.html") -> str:
    content = render(log)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
