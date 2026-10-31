from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import ai
import charts
import mediation
import negotiate
import negotiate_mediated
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
PROCESSES = {"mediated": "Negotiate to consensus in 2 turns (mediator recommends, all three must accept)",
             "vote": "Vote once (two ranking rounds, Borda + veto)"}
process = st.radio("Decision process", list(PROCESSES), format_func=PROCESSES.get, horizontal=True)

if st.button("Run", type="primary"):
    steps = {}
    with st.status("Working...", expanded=True) as status:
        labels = {"intake": "Read the messages", "options": "Generated safe options",
                  "round": "Agents ranked the options", "decision": "Made the decision",
                  "rules": "Checked hard rules and fuel state",
                  "opening": "Turn 1: agents declared priorities, positions and plans",
                  "recommendation": "Mediator made a recommendation",
                  "responses": "Turn 2: agents accepted or objected",
                  "brief": "Wrote the decision brief"}
        pipeline = negotiate_mediated.run if process == "mediated" else negotiate.run
        for ev in pipeline(event, texts, use_llm=use_llm):
            t = ev["type"]
            if t == "round":
                steps[f"round{ev['round']}"] = ev
                st.write(f"Round {ev['round']}: agents ranked the options")
            else:
                steps[t] = ev
                if t in labels:
                    st.write(labels[t])
        status.update(label="Done", state="complete", expanded=False)
    st.session_state["cascade"] = {"event_id": event_id, "texts": texts, "steps": steps, "process": process}

result = st.session_state.get("cascade")
if not result or result["event_id"] != event_id or result.get("process", "vote") != process:
    st.info("Press Run to start.")
    st.stop()

steps = result["steps"]
intake, opts, decision, brief = steps["intake"], steps["options"], steps["decision"], steps["brief"]
# The mediated process can add options proposed by the agents; "done" carries the final list.
options, baseline = steps["done"]["options"], opts["baseline"]
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
def _message_line(m: dict) -> str:
    to = "everyone" if m["to"] == "all" else ai.ROLE_NAMES[m["to"]]
    return f"{ROLE_ICON[m['from']]} **{ai.ROLE_NAMES[m['from']]} → {to}** · _{m['type']}_: {m['text']}"


def _position_card(col, role: str, p: dict, vote: str | None = None) -> None:
    with col.container(border=True):
        badge = {"ACCEPT": " · ✅ ACCEPT", "OBJECT": " · ✋ OBJECT"}.get(vote or "", "")
        st.markdown(f"{ROLE_ICON[role]} **{ai.ROLE_NAMES[role]}**{badge}"
                    + (" · deterministic stand-in" if p["source"] != "llm" else ""))
        st.markdown(f"Prefers **{p['ranking'][0]}** · can accept: {', '.join(p.get('acceptable', [])) or '-'}")
        if p.get("priorities"):
            st.caption("Declared priorities: " + ", ".join(f"{k.replace('_', ' ')} **{v}**" for k, v in p["priorities"].items()))
        if p.get("proposal"):
            st.caption(f"Proposed plan: land {' → '.join(p['proposal']['landing_order'])} "
                       f"(gates: {p['proposal']['gate_policy']})")
        for v in p["vetoes"]:
            st.error(f"Vetoes {v['option']}: {v['reason']}")
        if p["argument"]:
            st.write(p["argument"])


if process == "mediated":
    rules_step, opening, rec, responses = steps["rules"], steps["opening"], steps["recommendation"], steps["responses"]
    hard = opening["hard"]

    st.subheader("4. Rules: red lines, hard rules, soft preferences")
    if rules_step["emergency"]:
        e = rules_step["emergency"]
        st.error(f"🚨 MINIMUM FUEL declared automatically for {', '.join(e['flights'])}: {e['reason']}. "
                 "The mediator puts the Flight Crew first. Red lines are unchanged.")
    rcol, hcol = st.columns(2)
    with rcol:
        st.markdown("**🔴 Red lines** - never relaxed, built into every option by code")
        for line in rules_step["red_lines"]:
            st.caption(f"• {line}")
    with hcol:
        st.markdown("**🟠 Hard rules** - checked by code on every option")
        for r in hard["limits"]:
            st.caption(f"• {ai.ROLE_NAMES[r['role']]}: {r['description']} "
                       f"{'≤' if r['kind'] == 'max' else '≥'} {r['limit']}")
        for r in hard["relaxed"]:
            st.warning(f"Relaxed: {ai.ROLE_NAMES[r['role']]} {r['description']} → {r['to']} ({r['reason']})")
    st.caption("🟢 Soft preferences: each agent's own metrics, weighted by the priorities it declares in turn 1.")

    st.subheader("5. Turn 1 - every agent publishes its position at the same time")
    st.caption("Each agent only sees its own organisation's data. It declares priorities (high / low / none), "
               "preferred and acceptable options, vetoes, and may propose its own landing order.")
    for w in opening["warnings"]:
        st.warning(w)
    for col, role in zip(st.columns(3), negotiate.ROLES):
        _position_card(col, role, opening["positions"][role])
    for m in opening["messages"]:
        st.markdown(_message_line(m))
    for p in opening["plans"]:
        msg = f"{ai.ROLE_NAMES[p['by']]}'s plan: {p['result']}" + (f" (option {p['option']})" if p.get("option") else "")
        (st.success if p["result"] == "added" else st.info)(msg + (f" - {p['detail']}" if p.get("detail") else ""))

    st.subheader("6. Mediator recommends (code, no LLM)")
    st.caption("Scores 0-10 per agent from that agent's declared priorities (10 = best of these options for them). "
               "Skips options that break a hard rule or were vetoed, prefers options all three can accept, and "
               "recommends the one where the worst-off agent scores highest.")
    table = mediation.score_table(opening["scores"])
    st.dataframe(pd.DataFrame([{"Option": o["id"], "Name": o["name"],
                                **{ai.ROLE_NAMES[r]: table[o["id"]][r] for r in negotiate.ROLES},
                                "Lowest score": min(table[o["id"]].values()),
                                "Breaks hard rule": "; ".join(v["detail"] for v in hard["violations"].get(o["id"], [])) or "-"}
                               for o in options]), hide_index=True, use_container_width=True)
    st.markdown(f"Recommendation: **{rec['option']}** ({rec['rule']}) - scores "
                + ", ".join(f"{ai.ROLE_NAMES[k]} {v}" for k, v in rec["scores"].items())
                + ("; acceptable to all three" if rec["acceptable_to_all"] else ""))
    for n in rec["notes"]:
        st.caption(n)

    st.subheader("7. Turn 2 - accept or object (last turn)")
    for w in responses["warnings"]:
        st.warning(w)
    for col, role in zip(st.columns(3), negotiate.ROLES):
        _position_card(col, role, responses["votes"][role], responses["votes"][role].get("vote"))
    for m in responses["messages"]:
        st.markdown(_message_line(m))
    st.caption("Agents can misread numbers. The tables above are computed by code and are the source of truth.")

    st.subheader("8. Decision")
    wm = winner["metrics"]
    if decision["method"] == "consensus":
        how = "all three agents accepted it in turn 2"
    elif decision["method"] == "mediator_fallback":
        how = (f"{', '.join(ai.ROLE_NAMES[r] for r in decision['objected_by'])} objected, so code chose the best "
               f"balanced option all three listed as acceptable ({', '.join(decision['fallback_pool'])})")
    else:
        how = "no option was acceptable to all three, so the deterministic fallback (Borda + veto) chose it"
    st.success(f"**Option {winner['id']} - {winner['name']}**: {how}. Scores: "
               + ", ".join(f"{ai.ROLE_NAMES[k]} {v}" for k, v in decision["scores"].items()) + ". "
               f"{wm['diversions']} diversions · {wm['total_delay_min']} min total delay "
               f"(baseline {baseline['metrics']['total_delay_min']}) · {wm['pax_missed']} pax miss connections "
               f"(baseline {baseline['metrics']['pax_missed']}) · lowest fuel margin {wm['min_fuel_margin_min']} min.")
else:
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

    # -----------------------------------------------------------------------
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
st.subheader(f"{9 if process == 'mediated' else 6}. The plan")
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
