import json
from pathlib import Path

import pandas as pd
import streamlit as st

import charts

RESULTS = Path("events/evaluation_results.json")

st.title("Evaluation")
st.caption("The same pipeline run over many real disrupted arrival banks, compared against baselines. "
           "Regenerate with `python evaluate.py` (no API cost) or `python evaluate.py --agents 6` "
           "(also runs the Claude agents).")

if not RESULTS.exists():
    st.warning("No results yet. Run `python evaluate.py` first.")
    st.stop()

data = json.loads(RESULTS.read_text(encoding="utf-8"))
s = data["summary"]
base, neg = s["baseline"], s["mediated"]


def change(key: str) -> str:
    b, n = base[key], neg[key]
    return f"{(n - b) / b * 100:+.0f}%" if b else "n/a"


st.markdown(f"**{data['num_events']} events · {data['num_flights']} real flights · "
            f"{len({r['airport'] for r in data['events']})} hub airports · "
            f"{s['total_safety_violations']} safety violations across every option and baseline**")
t = st.columns(5)
t[0].metric("Total delay", f"{neg['total_delay_min']:.0f} min", change("total_delay_min"), delta_color="inverse")
t[1].metric("Knock-on departure delay", f"{neg['downstream_delay_min']:.0f} min", change("downstream_delay_min"),
            delta_color="inverse")
t[2].metric("Crew overtime", f"{neg['crew_overtime_min']:.0f} min", change("crew_overtime_min"), delta_color="inverse")
t[3].metric("Missed connections", f"{neg['pax_missed']:.0f} pax", change("pax_missed"), delta_color="inverse")
t[4].metric("Events with a diversion", neg["events_with_diversion"],
            f"{neg['events_with_diversion'] - base['events_with_diversion']:+d}", delta_color="inverse")
st.caption("Averages per event for the mediated process (the demo default) with deterministic stand-in agents. "
           "Change is against the no-coordination baseline.")

st.subheader("Four methods, same disruptions, same safety rules")
st.markdown(
    "- **No coordination (FCFS):** flights land in ETA order and wait for their planned gate. What happens if nobody re-plans.\n"
    "- **Min-delay optimiser:** the safe plan with the lowest total delay. One objective, one stakeholder.\n"
    "- **Mediated (3 agents, demo default):** hard rules per role, a mediator that recommends the option where the "
    "worst-off agent does best, and all three must accept. Otherwise code decides.\n"
    "- **Vote (3 agents):** two ranking rounds, then Borda count + veto.\n\n"
    "In both agent methods the agents here are deterministic stand-ins, so the numbers are reproducible. "
    f"Mediated outcomes: {s['mediated_outcomes']['consensus']} consensus, "
    f"{s['mediated_outcomes']['mediator_fallback']} mediator fallback, {s['mediated_outcomes']['vote_fallback']} vote fallback; "
    f"Minimum Fuel was declared in {s['minimum_fuel_events']} events. Stand-ins accept whatever the mediator recommends, "
    "so the consensus count says nothing about the live Claude agents."
)
st.altair_chart(charts.method_bars(s), width="content")
table = pd.DataFrame({charts.METHOD_LABELS[m]: {charts.METRIC_LABELS[k]: s[m][k] for k in charts.METRIC_LABELS}
                      for m in charts.METHOD_LABELS})
st.dataframe(table, width="stretch")
st.caption("Both agent methods give up a little total delay against the single-objective optimiser, in exchange for "
           "more fuel margin. They use more gate changes than the baseline: that is the price of re-planning.")

st.subheader("Mediated vs baseline, event by event")
wins = s["mediated_vs_baseline"]
st.dataframe(pd.DataFrame([{"Metric": charts.METRIC_LABELS.get(k, k), "Better": v["better"], "Same": v["same"],
                            "Worse": v["worse"]} for k, v in wins.items()]), hide_index=True, width="stretch")

llm = data.get("llm_runs") or []
if llm:
    st.subheader(f"Live Claude runs ({len(llm)} events, vote process)")
    st.caption("These runs use the vote process. Live runs of the mediated process are not measured here yet.")
    intake_ok = sum(all(r["intake_matches_truth"].values()) for r in llm if r.get("intake_matches_truth"))
    agree = sum(r["winner"] == r["proxy_winner"] for r in llm)
    c = st.columns(4)
    c[0].metric("Messages parsed exactly right", f"{intake_ok}/{len(llm)}")
    c[1].metric("Agents' pick = deterministic stand-in", f"{agree}/{len(llm)}")
    c[2].metric("Avg time per run", f"{sum(r['wall_clock_seconds'] for r in llm) / len(llm):.0f} s")
    c[3].metric("API calls per run", llm[0]["api_calls"])
    st.dataframe(pd.DataFrame([{
        "Event": r["event_id"],
        "Intake correct": "✅" if all(r["intake_matches_truth"].values()) else "❌",
        "Claude agents chose": r["winner"],
        "Stand-in would choose": r["proxy_winner"],
        "Vetoes": ", ".join(f"{k} x{v}" for k, v in r["vetoes"].items()) or "-",
        "Total delay (min)": r["metrics"]["total_delay_min"],
        "Missed pax": r["metrics"]["pax_missed"],
        "Fallbacks used": len(r.get("fallbacks", [])),
        "Tokens in/out": f"{r['input_tokens']:,} / {r['output_tokens']:,}",
        "Seconds": r["wall_clock_seconds"],
    } for r in llm]), hide_index=True, width="stretch")
    st.caption("The stand-in ranks each option by fixed rules per role. The Claude agents also weigh things the "
               "rules do not cover, like the weather or a crew shortage in the ops note, and can veto.")

with st.expander("Limits of this evaluation"):
    st.markdown(
        "- Flights, times, delays, tail numbers and onward departures are real (DOT 2015). Aircraft type, fuel, "
        "gates, passenger counts, the closure and the weather messages are synthetic, derived deterministically.\n"
        "- Runway rate, wake separation and turnaround times are simplified round numbers, not certified values.\n"
        "- Only the hub carrier's arrivals are modelled; other traffic on the runway is not.\n"
        "- The baseline is deliberately simple. The fairer comparison is against the min-delay optimiser."
    )
