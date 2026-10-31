import streamlit.components.v1 as components
import streamlit as st

import log_render
import orchestrator
import scenario

st.set_page_config(page_title="ATC Multi-Agent Negotiation", layout="wide")
st.title("Multi-Agent ATC Negotiation")
st.caption(
    "ATC, Pilot, and Ground AI agents negotiate a revised arrival time and gate "
    "assignment for one real disrupted flight, each as an independent LLM call, "
    "capped at 3 turns, with a deterministic constraint validator and fallback."
)

scenario_options = scenario.list_scenarios()
scenario_id = st.selectbox(
    "Scenario (real disrupted flight from the 2015 DOT dataset)",
    options=[key for key, _ in scenario_options],
    format_func=lambda key: dict(scenario_options)[key],
)

state = scenario.initial_state(scenario_id)
flight = state["flight"]
derived = state["derived"]

with st.container(border=True):
    cols = st.columns(4)
    cols[0].metric("Flight", f"{flight['airline']} {flight['flight_number']}")
    cols[1].metric("Route", f"{flight['origin_airport']} -> {flight['destination_airport']}")
    cols[2].metric(f"{flight['delay_cause'].replace('_', ' ').title()} delay", f"{flight['primary_delay_minutes']} min")
    cols[3].metric("Naive arrival (no negotiation)", state["naive_arrival"])
    st.caption(
        f"Scheduled arrival {flight['scheduled_arrival']} | "
        f"fuel reserve at naive arrival: {derived['fuel_reserve_minutes_at_naive_arrival']} min | "
        f"current gate {derived['current_gate']} | requested gate {derived['requested_gate']} | "
        f"connecting passengers {derived['connecting_passengers']}"
    )

VOTE_ICON = {"ACCEPT": "✅", "REJECT": "❌", "COUNTER": "🔁"}

if st.button("Run negotiation", type="primary"):
    final_log = None
    for event in orchestrator.run(scenario_id):
        t = event["type"]
        if t == "turn_start":
            st.subheader(f"Turn {event['turn']}")
            st.write(f"Baseline proposal entering this turn: `{event['proposal']}`")
        elif t == "validation":
            if event["passed"]:
                st.success(f"Constraint check passed for `{event['proposal']}`")
            else:
                st.error(
                    f"Constraint check FAILED for `{event['proposal']}`: "
                    + "; ".join(event["violations"])
                )
        elif t == "agent_response":
            r = event["response"]
            icon = VOTE_ICON.get(r["vote"], "")
            with st.expander(f"{icon} {r['from'].upper()} — {r['vote']} (turn {r['turn']})", expanded=True):
                st.write(r["rationale"])
                st.json(r.get("utility_fields", {}))
                if r["vote"] == "COUNTER" and r.get("counter_proposal"):
                    st.write(f"Counter-proposal: `{r['counter_proposal']}`")
        elif t == "turn_summary":
            m = event["mediator"]
            if m["consensus"]:
                st.success(f"Turn {event['turn']}: CONSENSUS reached on {event['final_proposal']}")
            else:
                st.warning(f"Turn {event['turn']}: no consensus — {'; '.join(m['reasons'])}")
        elif t == "error":
            st.error(f"Negotiation stopped — {event['message']}")
        elif t == "done":
            final_log = event["log"]

    st.divider()
    if final_log:
        outcome = final_log["outcome"]
        if outcome["status"] == "consensus":
            st.success(
                f"FINAL OUTCOME: consensus reached at turn {outcome['turn_reached']} — "
                f"arrival {outcome['proposal']['revised_arrival_time']}, "
                f"gate {outcome['proposal']['gate_assignment']}"
            )
        elif outcome["status"] == "fallback":
            fb = outcome["fallback_detail"]
            st.warning(f"FINAL OUTCOME: deterministic fallback fired ({fb['source']}) — {fb['reason']}")
            st.write(
                f"Fallback proposal: arrival {outcome['proposal']['revised_arrival_time']}, "
                f"gate {outcome['proposal']['gate_assignment']}"
            )
        else:  # status == "error"
            st.error(
                f"FINAL OUTCOME: negotiation could not complete — {outcome['message']} "
                f"(failed on turn {outcome['turn']}, {outcome['role']} agent)"
            )

        if outcome["status"] != "error":
            html_path = log_render.render_to_file(final_log)
            st.caption(f"Full consensus log written to consensus_log.json; static visualization at {html_path}")
            with open(html_path, encoding="utf-8") as f:
                components.html(f.read(), height=900, scrolling=True)
