"""Second page: parallel proposals + mediator (orchestrator_balanced.py).
Listed in the app.py page router.
Design: .plans/balanced-negotiation-design.md"""

from __future__ import annotations

import streamlit.components.v1 as components
import streamlit as st

import log_render
import orchestrator_balanced
import rules
import scenario

st.title("Multi-Agent ATC Negotiation — parallel proposals + mediator")
st.caption(
    "Each agent only sees its own organisation's data. All three propose at the same time and publish "
    "their limits and preferences. A deterministic (non-AI) mediator drops every unsafe or blocked option, "
    "scores the rest from each agent's declared preferences, and picks the option where the worst-off "
    "agent is best off. The agents then ACCEPT or OBJECT. Max 3 rounds, deterministic fallback."
)

scenario_options = scenario.list_scenarios()
scenario_id = st.selectbox(
    "Scenario (real disrupted flight from the 2015 DOT dataset)",
    options=[key for key, _ in scenario_options],
    format_func=lambda key: dict(scenario_options)[key],
)
low_fuel = st.checkbox(
    f"Simulate low fuel (reserve {rules.LOW_FUEL_SIMULATION_MINUTES} min). The system decides by itself "
    f"whether to declare Minimum Fuel (trigger: ≤ {rules.EMERGENCY_FUEL_TRIGGER_MINUTES} min)."
)

state = scenario.initial_state(scenario_id)
flight = state["flight"]
with st.container(border=True):
    cols = st.columns(4)
    cols[0].metric("Flight", f"{flight['airline']} {flight['flight_number']}")
    cols[1].metric("Route", f"{flight['origin_airport']} -> {flight['destination_airport']}")
    cols[2].metric(f"{flight['delay_cause'].replace('_', ' ').title()} delay", f"{flight['primary_delay_minutes']} min")
    cols[3].metric("Earliest possible arrival", state["naive_arrival"])

VOTE_ICON = {"PROPOSE": "📝", "ACCEPT": "✅", "OBJECT": "✋"}
DECL_LABELS = {
    "blocked_slots": "Blocked (red lines / hard rules)",
    "latest_acceptable_arrival": "Latest acceptable arrival",
    "preferred_window": "Preferred window",
    "preferred_window_importance": "Window importance",
    "preferred_gate": "Preferred gate",
    "preferred_gate_importance": "Gate importance",
    "i_can_give": "Can give",
    "i_need": "Needs",
}


def _p(p: dict) -> str:
    return f"{p['revised_arrival_time']} at {p['gate_assignment']}"


def _tiers(v: list[dict]) -> str:
    return "; ".join(f"[{x['tier'].replace('_', ' ').upper()}] {x['detail']}" for x in v)


def _declaration(d: dict) -> None:
    for key, label in DECL_LABELS.items():
        if d.get(key):
            st.write(f"**{label}:** {d[key]}")


if st.button("Run negotiation", type="primary"):
    final_log = None
    run_state = {}  # holds "emergency" once the system declares one, for the minimum-score column
    for event in orchestrator_balanced.run(scenario_id, simulate_low_fuel=low_fuel):
        t = event["type"]
        if t == "emergency":
            run_state["emergency"] = event["emergency"]
            e = event["emergency"]
            st.error(f"🚨 MINIMUM FUEL declared automatically by the {e['declared_by']}: {e['reason']}. "
                     f"Relaxed hard rules: {e['relaxed_hard_rules']}. Red lines unchanged.")
        elif t == "turn_start":
            st.subheader(f"Round {event['turn']}")
        elif t == "validation" and not event["passed"]:
            st.error(f"{event['role'].upper()}'s proposal {_p(event['proposal'])} breaks: {_tiers(event['violation_tiers'])}")
        elif t == "agent_response":
            r = event["response"]
            if r["from"] == "mediator":
                continue
            label = _p(r["proposal"]) if r["vote"] == "PROPOSE" else ""
            with st.expander(f"{VOTE_ICON.get(r['vote'], '')} {r['from'].upper()} — {r['vote']} {label}", expanded=True):
                st.write(r.get("rationale", ""))
                if r["vote"] == "PROPOSE":
                    _declaration(r.get("declaration", {}))
                else:
                    if r.get("objection_reason"):
                        st.write(f"**Objection type:** {r['objection_reason'].replace('_', ' ')}")
                    if r.get("revised_proposal"):
                        st.write(f"**Would accept instead:** {_p(r['revised_proposal'])}")
                    if r.get("declaration_update"):
                        st.write("**Updated declaration:**")
                        _declaration(r["declaration_update"])
                    if r.get("concession"):
                        st.caption("Accepted below its own minimum score - logged as a concession.")
        elif t == "balance":
            b = event["result"]
            c = b["counts"]
            st.info(
                f"**Mediator (no AI)** — {c['options']} options: {c['unsafe_real_check']} unsafe, "
                f"{c['declared_blocked']} declared blocked, {c['rejected_before']} rejected before, "
                f"{b['allowed']} allowed. Rule: {b['rule']}. Balanced option: **{_p(b['proposal'])}**."
            )
            st.table([
                {
                    "agent": x["role"].upper(),
                    "own proposal": _p(x["own_proposal"]),
                    "own proposal safe": "yes" if x["own_proposal_safe"] else "no",
                    "score on own": x["score_on_own_proposal"],
                    "score on balanced": x["score_on_balanced"],
                    "minimum": rules.accept_min(x["role"], run_state),
                    "gave up": x["gave_up"],
                    "why": "; ".join(x["why"]) or "-",
                }
                for x in b["trade_offs"]
            ])
            if b.get("parse_warnings"):
                st.caption(f"Ignored unreadable declaration parts: {b['parse_warnings']}")
        elif t == "turn_summary":
            m = event["mediator"]
            if m["consensus"]:
                st.success(f"Round {event['turn']}: CONSENSUS on {_p(event['final_proposal'])}")
            else:
                st.warning(f"Round {event['turn']}: no consensus — {'; '.join(m['reasons'])}")
        elif t == "error":
            st.error(f"Negotiation stopped — {event['message']}")
        elif t == "done":
            final_log = event["log"]

    st.divider()
    if final_log:
        outcome = final_log["outcome"]
        if outcome["status"] == "consensus":
            st.success(f"FINAL OUTCOME: consensus in round {outcome['turn_reached']} — {_p(outcome['proposal'])}")
        elif outcome["status"] == "fallback":
            fb = outcome["fallback_detail"]
            st.warning(f"FINAL OUTCOME: deterministic fallback ({fb['source']}) — {fb['reason']}")
            st.write(f"Fallback proposal: {_p(outcome['proposal'])}")
        else:
            st.error(f"FINAL OUTCOME: negotiation could not complete — {outcome['message']}")

        if outcome["status"] != "error":
            html_path = log_render.render_to_file(final_log)
            with open(html_path, encoding="utf-8") as f:
                components.html(f.read(), height=900, scrolling=True)
