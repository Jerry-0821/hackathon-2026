"""Second page: the parallel-proposal + balancer pipeline (orchestrator_balanced.py).
Streamlit lists it in the sidebar next to the original app.py page."""

import streamlit.components.v1 as components
import streamlit as st

import log_render
import orchestrator_balanced
import rules
import scenario

st.set_page_config(page_title="Balanced negotiation", layout="wide")
st.title("Multi-Agent ATC Negotiation — parallel proposals + balancer")
st.caption(
    "All three agents propose at the same time. A deterministic (non-AI) balancer drops every option "
    "that breaks a red line or hard rule, scores the rest per agent, and picks the option where the "
    "worst-off agent is best off. The agents then ACCEPT or OBJECT. Max 3 rounds, deterministic fallback."
)

scenario_options = scenario.list_scenarios()
scenario_id = st.selectbox(
    "Scenario (real disrupted flight from the 2015 DOT dataset)",
    options=[key for key, _ in scenario_options],
    format_func=lambda key: dict(scenario_options)[key],
)
fuel_emergency = st.checkbox(
    "Simulate fuel emergency (red lines still hold; hard rules relax; ATC and Ground must yield preferences)"
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


def _p(p: dict) -> str:
    return f"{p['revised_arrival_time']} at {p['gate_assignment']}"


def _tiers(v: list[dict]) -> str:
    return "; ".join(f"[{x['tier'].replace('_', ' ').upper()}] {x['detail']}" for x in v)


if st.button("Run negotiation", type="primary"):
    final_log = None
    for event in orchestrator_balanced.run(scenario_id, fuel_emergency=fuel_emergency):
        t = event["type"]
        if t == "turn_start":
            st.subheader(f"Round {event['turn']}")
        elif t == "validation" and not event["passed"]:
            st.error(f"{event['role'].upper()}'s proposal {_p(event['proposal'])} breaks: {_tiers(event['violation_tiers'])}")
        elif t == "agent_response":
            r = event["response"]
            label = _p(r["proposal"]) if r["vote"] == "PROPOSE" else ""
            with st.expander(f"{VOTE_ICON.get(r['vote'], '')} {r['from'].upper()} — {r['vote']} {label}", expanded=True):
                st.write(r.get("rationale", ""))
                for name, key in (("Red lines", "my_red_lines"), ("Can give", "i_can_give"), ("Needs", "i_need")):
                    if r.get(key):
                        st.write(f"**{name}:** {r[key]}")
                if r.get("revised_proposal"):
                    st.write(f"**Revised proposal:** {_p(r['revised_proposal'])}")
                if r.get("concession"):
                    st.caption("Accepted below its own minimum score - logged as a concession.")
        elif t == "balance":
            b = event["result"]
            st.info(
                f"**Balancer (no AI)** checked {b['candidates_checked']} options, {b['safe_candidates']} safe. "
                f"Balanced proposal: **{_p(b['proposal'])}** — lowest agent score {b['min_score']}, total {b['total_score']}."
            )
            st.table([
                {
                    "agent": x["role"].upper(),
                    "own proposal": _p(x["own_proposal"]),
                    "own proposal safe": "yes" if x["own_proposal_safe"] else "no",
                    "score on own": x["score_on_own_proposal"],
                    "score on balanced": x["score_on_balanced"],
                    "minimum": rules.accept_min(x["role"], state),
                    "gave up": x["gave_up"],
                }
                for x in b["trade_offs"]
            ])
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
