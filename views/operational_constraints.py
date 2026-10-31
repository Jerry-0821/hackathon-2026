from __future__ import annotations

import pandas as pd
import streamlit as st

import reference_data as ref
import scenario
import workbook

st.title("Operational constraints")
st.caption(f"Source: the challenge's constraints workbook (read from {workbook.load()['source']}). "
           "The workbook's capacity figures are synthetic planning limits set by the organisers, not real "
           "airport data. Rules of play: \"Use the limits in this workbook. Any limit you change must be "
           "stated in your demo.\" Everything we changed or assumed is listed at the bottom of this page.")

wb = workbook.load()
fc = wb["fuel_crew"]

st.subheader("Airports in our scenarios")
st.dataframe(pd.DataFrame([{
    "Airport": code,
    "Name": a["name"],
    "Size": a["size_class"],
    "Active runways": a["active_runways"],
    "Gates": a["gates"],
    "Max runway ops / hour": a["max_runway_ops_per_hour"],
    "Min turnaround (min)": a["min_turnaround_min"],
    "Spare gates": a["spare_gates"],
} for code, a in ((c, workbook.airport(c)) for c in sorted(ref.AIRPORTS))]), hide_index=True, width="stretch")
st.caption(f"The workbook lists {wb['airports_in_workbook']} airports; these are the hubs our 40 events use.")

st.subheader("Wake separation (FAA categories)")
speed = workbook.APPROACH_SPEED_KT
rows = [{"Leader": lead, "Follower": follow, "Workbook (NM)": nm,
         f"Our time at {speed} kt (min, rounded up)": -(-nm // (speed / 60))}
        for (lead, follow), nm in wb["wake_nm"].items()]
rows.append({"Leader": "Any other pair", "Follower": "", "Workbook (NM)": wb["wake_default_nm"],
             f"Our time at {speed} kt (min, rounded up)": -(-wb["wake_default_nm"] // (speed / 60))})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
st.caption("Aircraft type to FAA category: " + ", ".join(f"{t} = {c}" for t, c in workbook.FAA_CATEGORY.items())
           + ". No Super or Small aircraft appear in our events.")

st.subheader("Fuel and crew")
st.dataframe(pd.DataFrame([
    {"Rule": "Final fuel reserve", "Workbook": f"{fc['final_reserve_min']:.0f} min", "How we use it":
     "Red line. A flight that would land below it diverts instead."},
    {"Rule": "Contingency fuel", "Workbook": f"{fc['contingency_pct']:.0f}% of trip", "How we use it":
     "Added to the usable fuel on arrival (unused contingency)."},
    {"Rule": "Spare fuel on arrival", "Workbook": f"{fc['spare_fuel_on_arrival_min']:.0f} min of holding",
     "How we use it": "Usable holding before the final reserve: spare - reserve + contingency."},
    {"Rule": "Holding fuel burn", "Workbook": f"{fc['burn_narrow_kg_h']:,.0f} kg/h narrowbody, "
     f"{fc['burn_wide_kg_h']:,.0f} kg/h widebody", "How we use it": "Fuel margins shown in kg."},
    {"Rule": "Flight crew max duty", "Workbook": f"{fc['flight_crew_max_duty_h']:.0f} h", "How we use it":
     "Not modelled (no crew data, see below)."},
    {"Rule": "Ground crew shift", "Workbook": f"{fc['ground_shift_h']:.0f} h, overtime {fc['overtime_rate']}x",
     "How we use it": "Overtime minutes reported and costed at the overtime rate."},
    {"Rule": "Gate change notice", "Workbook": f"{fc['gate_change_notice_min']:.0f} min before arrival",
     "How we use it": "Hard rule in the solver and the independent safety check."},
]), hide_index=True, width="stretch")

st.subheader("Rules of play")
how = {
    "Input": "Every event contains at least one flights.csv row that meets this rule; the page names it.",
    "Turn": "Mediated process: 2 agent turns. Vote process: 2 ranking rounds. Single-flight pages: up to 3 turns.",
    "No consensus": "A deterministic fallback decides, and the decision log records why.",
    "Constraints": "This page.",
    "Output": "A revised landing time and gate for every flight, plus a downloadable decision log.",
}
st.dataframe(pd.DataFrame([{"Topic": k, "Workbook": v, "How we meet it": how.get(k, "")}
                           for k, v in wb["rules_of_play"].items()]), hide_index=True, width="stretch")

st.subheader("Changes and assumptions we state")
for line in workbook.STATED_CHANGES:
    st.markdown(f"- {line}")
c = scenario.CONSTRAINTS
st.markdown(f"- The **Single flight (v1)** and **Balanced negotiation** pages use their own simplified rules: "
            f"{c['min_wake_separation_minutes']} min separation between any two arrivals, "
            f"{c['min_fuel_reserve_minutes']} min final reserve, and at most {c['max_crew_overtime_minutes']} "
            "min crew overtime. The runway closure cascade uses the workbook.")
