from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import ai
import charts
import negotiate
import solver

ROLE_ICON = {"atc": "🗼", "pilot": "👨‍✈️", "ground": "🧳"}


@st.cache_data
def load_events() -> list[dict]:
    with open("events/cascades.json", encoding="utf-8") as f:
        events = json.load(f)["events"]
    return sorted(events, key=lambda e: (not e.get("demo"), e["id"]))


st.title("Runway closure cascade")
st.caption(
    "A runway closes and a bank of real arrivals has to be re-planned. Claude reads the raw "
    "messages. Code generates only safe plans. Three Claude agents (ATC, Flight Crew, Ground Ops) "
    "rank the plans and argue trade-offs. A fixed voting rule picks the outcome."
)

events = load_events()
event_id = st.selectbox(
    "Disrupted arrival bank (real flights from the 2015 DOT dataset)",
    [e["id"] for e in events],
    format_func=lambda i: ("★ " if next(e for e in events if e["id"] == i).get("demo") else "")
    + next(e for e in events if e["id"] == i)["label"],
)
event = next(e for e in events if e["id"] == event_id)

c = st.columns(5)
c[0].metric("Airport", event["airport"])
c[1].metric("Arrivals affected", len(event["flights"]))
c[2].metric("Bank starts", event["window_start"])
c[3].metric("Gates on this pier", len(event["gates"]))
c[4].metric("Gates at airport (approx.)", event["gates_total_airport"])

# ---------------------------------------------------------------------------
st.subheader("1. Incoming messages")
st.caption("Synthetic messages written from the scenario. Edit them to change the situation: "
           "close another gate, move the closure time, or change the weather.")
mcols = st.columns(3)
texts = {
    "notam": mcols[0].text_area("NOTAM", event["texts"]["notam"], key=f"notam-{event_id}", height=110),
    "metar": mcols[1].text_area("METAR", event["texts"]["metar"], key=f"metar-{event_id}", height=110),
    "ops_note": mcols[2].text_area("Ops note (free text)", event["texts"]["ops_note"], key=f"ops-{event_id}", height=110),
}

with st.expander("Flights in this bank", expanded=False):
    st.dataframe(pd.DataFrame([{
        "Flight": f["id"],
        "From": f["origin"],
        "Sched. arrival": f["scheduled_arrival"],
        "ETA": f["eta"],
        "Real arrival delay (min)": f["real_arrival_delay"],
        "Aircraft*": f["aircraft_type"],
        "Wake*": f["wake"],
        "Fuel above reserve at ETA (min)*": f["extra_fuel_min"],
        "Planned gate*": f["preferred_gate"],
        "Connecting pax*": sum(c["pax"] for c in f["connections"]),
        "Onward flights": ", ".join(f"{c['flight']} {c['departure']}" for c in f["connections"]),
        "Aircraft's next departure": (f"{f['next_departure']['flight']} {f['next_departure']['departure']}"
                                      if f["next_departure"] else "-"),
    } for f in event["flights"]]), hide_index=True, use_container_width=True)
    st.caption("Real: flights, times, delays, tail numbers, each aircraft's next departure, and onward flights "
               "(DOT 2015). *Synthetic: aircraft type (from airline + distance), wake category, fuel, gate IDs "
               "and passenger counts, derived deterministically from reference tables. Simplified rules, not for "
               "operational use.")

use_llm = st.toggle("Use Claude agents", value=True,
                    help="Off: deterministic stand-ins for every LLM step. No API cost, same safety guarantees.")

if st.button("Run", type="primary"):
    steps = {}
    with st.status("Working...", expanded=True) as status:
        labels = {"intake": "Read the messages", "options": "Generated safe options",
                  "round": "Agents ranked the options", "decision": "Applied the voting rule",
                  "brief": "Wrote the decision brief"}
        for ev in negotiate.run(event, texts, use_llm=use_llm):
            t = ev["type"]
            if t == "round":
                steps[f"round{ev['round']}"] = ev
                st.write(f"Round {ev['round']}: agents ranked the options")
            else:
                steps[t] = ev
                if t in labels:
                    st.write(labels[t])
        status.update(label="Done", state="complete", expanded=False)
    st.session_state["cascade"] = {"event_id": event_id, "texts": texts, "steps": steps}

result = st.session_state.get("cascade")
if not result or result["event_id"] != event_id:
    st.info("Press Run to start.")
    st.stop()

steps = result["steps"]
intake, opts, decision, brief = steps["intake"], steps["options"], steps["decision"], steps["brief"]
options, baseline = opts["options"], opts["baseline"]
winner = next(o for o in options if o["id"] == decision["winner"])
d = intake["disruption"]

# ---------------------------------------------------------------------------
st.subheader("2. What Claude read from the messages")
icol, vcol = st.columns([3, 2])
with icol:
    rows = [{"Fact": f"Runway {c['runway']} closed", "Local time": f"{solver.hhmm(c['start_min'])}-{solver.hhmm(c['end_min'])}",
             "Source text": c.get("quote", "")} for c in d["runway_closures"]]
    rows += [{"Fact": f"Gate {g['gate']} out of service", "Local time": "until further notice",
              "Source text": g.get("quote", "") or g.get("reason", "")} for g in d["gates_out_of_service"]]
    ceiling = f"ceiling {d['ceiling_ft']} ft" if d.get("ceiling_ft") else "no ceiling"
    rows.append({"Fact": f"{d['flight_rules']} ({ceiling}, visibility {d['visibility_sm']} SM)",
                 "Local time": "", "Source text": "METAR"})
    rows += [{"Fact": n, "Local time": "", "Source text": "noted, not modelled"} for n in d.get("other_notes", [])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
with vcol:
    st.markdown("**Checked by code**")
    st.caption("Runways and gates must exist at this airport. UTC times are converted to local by code, not by the model.")
    for w in intake["warnings"]:
        st.warning(w)
    if not intake["warnings"]:
        st.success("Every extracted fact matched the airport data.")
    m = intake.get("matches_truth")
    if m and result["texts"] == event["texts"]:
        st.write("Matches the scenario's ground truth: " + ", ".join(
            f"{'✅' if ok else '❌'} {k.replace('_', ' ')}" for k, ok in m.items()))
    if d.get("summary"):
        st.caption(f"Summary: {d['summary']}")

# ---------------------------------------------------------------------------
st.subheader("3. Safe options generated by code")
total_viol = sum(len(v) for v in opts["safety_violations"].values())
k = st.columns(4)
k[0].metric("Plans explored", f"{opts['explored']:,}")
k[1].metric("Pareto-optimal plans", opts["front_size"])
k[2].metric("Options shown to agents", len(options))
k[3].metric("Safety violations (independent re-check)", total_viol)
st.caption("Every plan respects runway rate and wake separation, lands every flight above final reserve fuel "
           "(or diverts it), and uses only open, free, size-compatible gates. Pareto-optimal: no other plan is "
           "better on every objective at once.")
st.altair_chart(charts.plan_cloud(opts["cloud"], options, baseline, decision["winner"]), use_container_width=True)

metric_cols = {"diversions": "Diversions", "total_delay_min": "Total delay (min)", "pax_missed": "Missed connections (pax)",
               "min_fuel_margin_min": "Lowest fuel margin (min)", "gate_changes": "Gate changes",
               "downstream_delay_min": "Knock-on departure delay (min)", "crew_overtime_min": "Crew overtime (min)",
               "last_landing": "Last landing"}
table = [{"Option": o["id"], "Name": o["name"], **{v: o["metrics"][k2] for k2, v in metric_cols.items()}} for o in options]
table.append({"Option": "-", "Name": baseline["name"], **{v: baseline["metrics"][k2] for k2, v in metric_cols.items()}})
st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
st.subheader("4. Agents rank the options and argue")
for rnd in (1, 2):
    r = steps[f"round{rnd}"]
    st.markdown(f"**Round {rnd}**" + (" - each agent sees the others' round 1 positions" if rnd == 2 else " - independent"))
    for w in r["warnings"]:
        st.warning(w)
    cols = st.columns(3)
    for col, role in zip(cols, negotiate.ROLES):
        p = r["positions"][role]
        with col.container(border=True):
            st.markdown(f"{ROLE_ICON[role]} **{ai.ROLE_NAMES[role]}**"
                        + (" · deterministic stand-in" if p["source"] != "llm" else ""))
            st.markdown("Ranking: " + " > ".join(f"**{x}**" if i == 0 else x for i, x in enumerate(p["ranking"])))
            for v in p["vetoes"]:
                st.error(f"Vetoes {v['option']}: {v['reason']}")
            if p["argument"]:
                st.write(p["argument"])
            if rnd == 2 and p.get("reply"):
                st.caption(f"Reply: {p['reply']}")
st.caption("Agents can misread numbers. The table in step 3 is computed by code and is the source of truth.")

# ---------------------------------------------------------------------------
st.subheader("5. Decision")
st.caption("Fixed rule, no LLM: Borda count over the round 2 rankings (first place = "
           f"{len(options) - 1} points). A vetoed option is not eligible unless every option is vetoed. "
           "Ties go to the option with the best balance across all objectives.")
vote_rows = [{"Option": o["id"], "Name": o["name"], "Points": decision["borda"][o["id"]],
              "Vetoed by": ", ".join(v["by"] for v in decision["vetoes"].get(o["id"], [])) or "-",
              "Eligible": "yes" if o["id"] in decision["eligible"] else "no"} for o in options]
st.dataframe(pd.DataFrame(vote_rows).sort_values("Points", ascending=False), hide_index=True, use_container_width=True)
if decision["note"]:
    st.warning(decision["note"])
ranks = ", ".join(f"#{v} for {k2}" for k2, v in decision["winner_rank_by_agent"].items())
wm = winner["metrics"]
st.success(f"**Option {winner['id']} - {winner['name']}** ({ranks}). "
           f"{wm['diversions']} diversions · {wm['total_delay_min']} min total delay "
           f"(baseline {baseline['metrics']['total_delay_min']}) · {wm['pax_missed']} pax miss connections "
           f"(baseline {baseline['metrics']['pax_missed']}) · lowest fuel margin {wm['min_fuel_margin_min']} min.")
st.markdown("**Decision brief**" + ("" if brief["source"] == "llm" else f" ({brief['source']})"))
st.info(brief["text"])

# ---------------------------------------------------------------------------
st.subheader("6. The plan")
which = st.radio("Show", [f"Chosen option {winner['id']}", "No-coordination baseline"], horizontal=True)
plan = winner if which.startswith("Chosen") else baseline
problem = opts["problem"]
st.altair_chart(charts.timeline(plan, event["flights"], problem["closures"]), use_container_width=True)
st.dataframe(pd.DataFrame([{
    "Flight": r["flight"],
    "Lands": r.get("landing", "DIVERTS"),
    "Holding (min)": r["holding_min"],
    "Fuel margin (min / kg)": "" if r["diverted"] else f"{r['fuel_margin_min']} / {r['fuel_margin_kg']}",
    "Gate": r.get("gate", "-") + (" (changed)" if r.get("gate_changed") else ""),
    "Gate wait (min)": r.get("gate_wait_min", ""),
    "Missed connections": ", ".join(r.get("missed_connections", [])) or "-",
    "Knock-on to next departure (min)": r.get("knock_on_min", ""),
} for r in plan["rows"]]), hide_index=True, use_container_width=True)

st.download_button("Download the full decision log (JSON)", json.dumps(steps["done"]["log"], indent=1),
                   file_name=f"{event_id}-decision-log.json")
