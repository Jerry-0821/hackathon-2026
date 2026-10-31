"""Default page: the negotiation room.

Chat view of the mediated cascade pipeline (negotiate_mediated.run). Every bubble is one structured
message from one separate Claude call, or one step done by code. The side panel shows the turn limit,
which agent can accept which option, the cross-domain checks and the decision. The plan at the bottom
is a recommendation for the duty manager to approve. Nothing is executed.

Open with ?autorun=1 to start the run on page load, and &agents=0 to start with the Claude agents off.
"""

from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st

import ai
import charts
import mediation
import negotiate_mediated
import solver

ROLES = negotiate_mediated.ROLES
NAMES = ai.ROLE_NAMES
COLOR = {"atc": charts.BLUE, "pilot": charts.ORANGE, "ground": charts.AQUA, "mediator": charts.MUTED,
         "code": charts.MUTED, "claude": "#7c3aed"}
AVATAR = {"atc": "🗼", "pilot": "👨‍✈️", "ground": "🧳", "mediator": "⚖️", "code": "⚙️", "claude": "✨"}
TITLE = {"atc": "ATC", "pilot": "Flight Crew", "ground": "Ground Ops", "mediator": "Mediator",
         "code": "System", "claude": "Claude"}
SUBTITLE = {"atc": "runway, separation, throughput", "pilot": "fuel, safety, connections",
            "ground": "gates, crew, turnarounds", "mediator": "code, no AI", "code": "code, no AI",
            "claude": "reads text, writes text"}
SEES = {"atc": "ETAs, wake categories, runway closure, landing times, holding",
        "pilot": "fuel above final reserve, connections, holding, fuel margin, missed passengers",
        "ground": "gates, busy windows, crew shift end, next departures, gate waits, overtime"}
METRIC = {"total_delay_min": "total delay", "max_delay_min": "max delay", "min_fuel_margin_min": "fuel margin",
          "pax_missed": "missed pax", "downstream_delay_min": "knock-on delay", "crew_overtime_min": "crew overtime",
          "gate_changes": "gate changes", "diversions": "diversions", "last_landing": "last landing"}
MESSAGE_ICON = {"request": "❓", "offer": "🤝", "concession": "✅", "reject": "✋"}

st.html("""<style>
.who{font-weight:700;font-size:15px} .sub{color:#6b7280;font-size:12px;margin-left:8px}
.chip{display:inline-block;font-size:12px;border:1px solid #e5e7eb;border-radius:6px;padding:1px 8px;
      margin:2px 4px 2px 0;background:#fafafa;color:#1d2128}
.chip.high{border-color:#f4c97a;background:#fff8e8} .chip.yes{border-color:#a7e3bd;background:#f0fdf4;color:#15803d}
.chip.no{border-color:#f5b5b5;background:#fef2f2;color:#b91c1c;text-decoration:line-through}
.chip.win{border-color:#15803d;background:#15803d;color:#fff}
.to{display:inline-block;font-size:12px;font-weight:600;color:#fff;border-radius:99px;padding:1px 8px;margin-right:6px}
.turn{display:inline-block;width:32%;text-align:center;padding:6px 0;border-radius:8px;font-size:12px;font-weight:600;
      background:#f3f4f6;color:#6b7280;margin-right:1%}
.turn.on{background:#1d2128;color:#fff} .turn.done{background:#dcfce7;color:#15803d}
.turn.unused{border:1px dashed #d1d5db;background:#fff}
.opt{border:1px solid #e5e7eb;border-radius:10px;padding:8px 10px;margin-bottom:6px;font-size:13px}
.opt.out{opacity:.55;background:#fef2f2} .opt.win{border:2px solid #15803d;background:#f0fdf4}
.opt .meta{color:#6b7280;font-size:12px}
.acc{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;background:#f3f4f6;color:#6b7280;margin:4px 3px 0 0}
.acc.y{color:#fff} .acc.n{background:#fee2e2;color:#b91c1c;text-decoration:line-through}
.check{display:flex;justify-content:space-between;gap:8px;font-size:12px;padding:4px 0;border-bottom:1px solid #f1f5f9}
.check span:last-child{white-space:nowrap}
.ok{color:#15803d;font-weight:600} .bad{color:#b91c1c;font-weight:600} .warn{color:#b7791f;font-weight:600}
.vote{font-size:18px;font-weight:700} .vote.ok{color:#15803d} .vote.no{color:#b91c1c}
.divider{text-align:center;color:#6b7280;font-size:12px;margin:10px 0 4px}
</style>""")


# ---------------------------------------------------------------------------
# Small render helpers
# ---------------------------------------------------------------------------
def who(role: str, extra: str = "") -> None:
    st.markdown(f'<span class="who" style="color:{COLOR[role]}">{TITLE[role]}</span>'
                f'<span class="sub">{SUBTITLE[role]}{extra}</span>', unsafe_allow_html=True)


def chips(items: list[tuple[str, str]]) -> None:
    st.markdown("".join(f'<span class="chip {cls}">{text}</span>' for text, cls in items), unsafe_allow_html=True)


def divider(text: str) -> None:
    st.markdown(f'<div class="divider">— {text} —</div>', unsafe_allow_html=True)


def to_badge(to: str) -> str:
    if to == "all":
        return '<span class="to" style="background:#6b7280">→ everyone</span>'
    return f'<span class="to" style="background:{COLOR[to]}">→ {TITLE[to]}</span>'


def summary_line(metrics: dict) -> str:
    return (f"{metrics['total_delay_min']} min delay · {metrics['pax_missed']} missed pax · "
            f"fuel margin {metrics['min_fuel_margin_min']} min · knock-on {metrics['downstream_delay_min']} min · "
            f"{metrics['gate_changes']} gate changes" + (f" · {metrics['diversions']} DIVERSIONS" if metrics["diversions"] else ""))


# ---------------------------------------------------------------------------
# Chat messages, one per pipeline event
# ---------------------------------------------------------------------------
def say_intake(ev: dict, event: dict, edited: bool) -> None:
    d = ev["disruption"]
    with st.chat_message(TITLE["claude"], avatar=AVATAR["claude"]):
        who("claude", " · 1 call · reads the NOTAM, METAR and ops note")
        for c in d["runway_closures"]:
            st.markdown(f"**Runway {c['runway']} closed {solver.hhmm(c['start_min'])}–{solver.hhmm(c['end_min'])}** local"
                        + (f" — from `{c['quote']}`" if c.get("quote") else ""))
        for g in d["gates_out_of_service"]:
            st.markdown(f"**Gate {g['gate']} out of service** ({g.get('reason', '')})"
                        + (f" — from `{g['quote']}`" if g.get("quote") else ""))
        ceiling = f"ceiling {d['ceiling_ft']} ft" if d.get("ceiling_ft") else "no ceiling"
        st.markdown(f"**{d['flight_rules']}** · {ceiling} · visibility {d['visibility_sm']} SM — from the METAR")
        for n in d.get("other_notes", []):
            st.caption(f"Noted, not modelled: {n}")
        if ev["raw"] is None:
            st.caption("Claude was not called (agents off or the call failed): using the scenario's built-in facts.")
    with st.chat_message(TITLE["code"], avatar=AVATAR["code"]):
        who("code", " · checks every fact against the airport data, converts UTC to local")
        for w in ev["warnings"]:
            st.warning(w)
        if not ev["warnings"]:
            st.markdown("✅ Every runway and gate exists at this airport. Times converted by code, not by the model.")
        m = ev.get("matches_truth")
        if m and not edited:
            st.caption("Against the scenario's ground truth: " + ", ".join(
                f"{'✅' if ok else '❌'} {k.replace('_', ' ')}" for k, ok in m.items()))


def say_options(ev: dict, rules: dict | None) -> None:
    total_viol = sum(len(v) for v in ev["safety_violations"].values())
    with st.chat_message(TITLE["code"], avatar=AVATAR["code"]):
        who("code", " · solver, no AI")
        st.markdown(f"Simulated **{ev['explored']:,} safe plans**, kept **{ev['front_size']}** Pareto-optimal, "
                    f"sends **{len(ev['options'])} options** to the agents. Independent re-check: "
                    f"**{total_viol} safety violations**.")
        for o in ev["options"]:
            st.markdown(f"**{o['id']} · {o['name']}** — {summary_line(o['metrics'])}")
        st.caption("Red lines in every plan: runway rate and wake separation, no landing while every runway is closed, "
                   "every flight above final reserve fuel (or it diverts), gates open, free and size-compatible.")
        if rules:
            if rules["emergency"]:
                e = rules["emergency"]
                st.error(f"🚨 MINIMUM FUEL declared by code for {', '.join(e['flights'])}: {e['reason']}. "
                         "The mediator puts the Flight Crew first. Red lines unchanged.")
            for r in rules["hard"]["relaxed"]:
                st.warning(f"Hard rule relaxed and logged: {TITLE[r['role']]} {r['description']} "
                           f"{r['limit']} → {r['to']} min. Reason: {r['reason']}.")


def say_position(role: str, p: dict, turn: int) -> None:
    with st.chat_message(TITLE[role], avatar=AVATAR[role]):
        label = " · separate Claude call" if p["source"] == "llm" else " · ⚠ deterministic stand-in"
        who(role, f" · turn {turn}{label}")
        if turn == 1:
            st.caption(f"Sees only: {SEES[role]}")
        if p.get("vote"):
            ok = p["vote"] == "ACCEPT"
            st.markdown(f'<span class="vote {"ok" if ok else "no"}">{"✅" if ok else "✋"} {p["vote"]}</span>',
                        unsafe_allow_html=True)
        if p.get("argument"):
            st.markdown(p["argument"])
        items = []
        for m, v in (p.get("priorities") or {}).items():
            items.append((f"<b>{METRIC.get(m, m)}</b> {v}", "high" if v == "high" else ""))
        items.append((f"prefers <b>{p['ranking'][0]}</b>", ""))
        acceptable = p.get("acceptable") or []
        if acceptable:
            items.append((f"accepts {' '.join(acceptable)}", "yes"))
        for v in p.get("vetoes", []):
            items.append((f"vetoes {v['option']}", "no"))
        chips(items)
        for v in p.get("vetoes", []):
            st.error(f"Veto {v['option']}: {v['reason']}")
        if p.get("proposal"):
            st.markdown(f"📋 Own plan: land {' → '.join(p['proposal']['landing_order'])} · gates: {p['proposal']['gate_policy']}")
        if p.get("proposal_rejected"):
            st.caption(f"Own plan rejected by code: {p['proposal_rejected']}")
        for m in p.get("messages", []):
            st.markdown(f"{to_badge(m['to'])} {MESSAGE_ICON.get(m['type'], '')} _{m['type']}_ · {m['text']}",
                        unsafe_allow_html=True)
        if p.get("ranking_repaired"):
            st.caption("⚠ The model's option list was incomplete or messy; code repaired the ranking.")
        with st.expander("Structured message (JSON)"):
            st.json({k: v for k, v in p.items() if k not in ("source",)}, expanded=False)


def say_plans(plans: list[dict]) -> None:
    if not plans:
        return
    with st.chat_message(TITLE["code"], avatar=AVATAR["code"]):
        who("code", " · simulates and verifies every plan an agent proposed")
        for p in plans:
            text = f"{TITLE[p['by']]}'s plan: **{p['result']}**" + (f" as option {p['option']}" if p.get("option") else "")
            (st.success if p["result"] == "added" else st.info)(text + (f" — {p['detail']}" if p.get("detail") else ""))


def say_recommendation(rec: dict, scores: dict, options: list[dict], hard: dict) -> None:
    table = mediation.score_table(scores)
    with st.chat_message(TITLE["mediator"], avatar=AVATAR["mediator"]):
        who("mediator", " · scores from the priorities each agent declared")
        for oid in rec.get("breaks_hard_rules", []):
            detail = "; ".join(v["detail"] for v in hard["violations"].get(oid, []))
            st.markdown(f"✗ Drops **{oid}**: breaks a hard rule ({detail}).")
        for oid, vetoes in (rec.get("vetoed") or {}).items():
            st.markdown(f"✗ Drops **{oid}**: vetoed by {', '.join(TITLE[v['by']] for v in vetoes)}.")
        rows = [{"Option": oid, **{TITLE[r]: table[oid][r] for r in ROLES}, "Lowest": min(table[oid].values()),
                 "Eligible": "yes" if oid in rec["eligible"] else "no"} for oid in table]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.markdown(f"**Recommends {rec['option']}** — {rec['rule']}: its worst-off agent scores "
                    f"{rec['min_score']}" + (". Acceptable to all three." if rec["acceptable_to_all"] else "."))
        for n in rec.get("notes", []):
            st.caption(n)


def say_decision(dec: dict, options: list[dict], responses: dict) -> None:
    w = next(o for o in options if o["id"] == dec["winner"])
    with st.chat_message(TITLE["code"], avatar=AVATAR["code"]):
        who("code", " · decision rule, no AI")
        if dec["method"] == "consensus":
            st.success(f"**Consensus in turn 2 of 3.** All three accepted **option {dec['winner']} · {w['name']}**. "
                       "Turn 3 not needed.")
        elif dec["method"] == "mediator_fallback":
            st.warning(f"**No consensus**: {', '.join(TITLE[r] for r in dec['objected_by'])} objected. "
                       f"Deterministic fallback: the best max-min option every agent listed as acceptable "
                       f"({', '.join(dec['fallback_pool'])}) → **option {dec['winner']} · {w['name']}**.")
        else:
            st.warning(f"**No consensus** and no option acceptable to all three. Deterministic fallback "
                       f"(Borda count + veto) → **option {dec['winner']} · {w['name']}**."
                       + (f" {dec['note']}" if dec.get("note") else ""))
        st.caption("Scores for the chosen option: " + ", ".join(f"{TITLE[r]} {v}" for r, v in dec["scores"].items())
                   + " · ranked " + ", ".join(f"#{v} by {k}" for k, v in dec["winner_rank_by_agent"].items()))


def say_brief(brief: dict) -> None:
    with st.chat_message(TITLE["claude"], avatar=AVATAR["claude"]):
        who("claude", " · decision brief" + ("" if brief["source"] == "llm" else f" · {brief['source']}"))
        st.markdown(brief["text"])


def render_event(ev: dict, steps: dict, event: dict, edited: bool, live: bool) -> None:
    """Render the chat bubbles for one pipeline event. Works live and when replaying from session state."""
    t = ev["type"]
    if t == "intake":
        say_intake(ev, event, edited)
    elif t == "rules":
        say_options(steps["options"], ev)
    elif t == "opening":
        divider("Turn 1 of 3 · three separate Claude calls, in parallel · each agent sees only its own data")
        for w in ev["warnings"]:
            st.warning(w)
        for r in ROLES:
            say_position(r, ev["positions"][r], 1)
            if live:
                time.sleep(0.25)
        say_plans(ev["plans"])
    elif t == "recommendation":
        say_recommendation(ev, steps["opening"]["scores"], steps["options"]["options"], steps["opening"]["hard"])
    elif t == "responses":
        divider("Turn 2 of 3 · last agent turn · accept or object to the recommendation")
        for w in ev["warnings"]:
            st.warning(w)
        for r in ROLES:
            say_position(r, ev["votes"][r], 2)
            if live:
                time.sleep(0.25)
    elif t == "decision":
        say_decision(ev, steps["done"]["options"] if "done" in steps else _options_so_far(steps), steps["responses"])
    elif t == "brief":
        say_brief(ev)


def _options_so_far(steps: dict) -> list[dict]:
    """Options list before the 'done' event: the solver's options plus any the agents added in turn 1."""
    options = list(steps["options"]["options"])
    known = {o["id"] for o in options}
    for p in steps.get("opening", {}).get("plans", []):
        if p["result"] == "added" and p["option"] not in known:
            options.append({"id": p["option"], "name": f"Plan from {TITLE[p['by']]}", "metrics": None})
    return options


# ---------------------------------------------------------------------------
# Side panel
# ---------------------------------------------------------------------------
def draw_side(steps: dict) -> None:
    done = "decision" in steps
    t1 = "done" if "opening" in steps else ("on" if "rules" in steps else "")
    t2 = "done" if "responses" in steps else ("on" if "recommendation" in steps else "")
    t3 = "unused"
    t3_text = "Turn 3 unused" if done else "Turn 3 (limit)"
    st.markdown("**Turn limit: 3**")
    st.markdown(f'<span class="turn {t1}">Turn 1</span><span class="turn {t2}">Turn 2</span>'
                f'<span class="turn {t3}">{t3_text}</span>', unsafe_allow_html=True)

    if "options" not in steps:
        st.caption("Waiting for the solver.")
        return
    options = steps["done"]["options"] if "done" in steps else _options_so_far(steps)
    opening, responses, rec = steps.get("opening"), steps.get("responses"), steps.get("recommendation")
    hard = (opening or steps["rules"])["hard"] if "rules" in steps else {"violations": {}, "limits": [], "relaxed": []}
    winner = steps["decision"]["winner"] if done else None

    st.markdown("**Options · who can accept**")
    for o in options:
        oid = o["id"]
        out = oid in hard["violations"] and hard["violations"][oid]
        cls = "win" if oid == winner else ("out" if out else "")
        tag = ""
        if oid == winner:
            tag = '<span class="ok">chosen</span>'
        elif rec and rec["option"] == oid:
            tag = '<span class="ok">recommended</span>'
        elif out:
            tag = '<span class="bad">breaks hard rule</span>'
        acc_html = ""
        for r in ROLES:
            src = (responses["votes"][r] if responses else opening["positions"][r]) if opening else None
            if src is None:
                acc_html += f'<span class="acc">{TITLE[r]}</span>'
            elif oid in src.get("acceptable", []) or (oid == winner and src.get("vote") == "ACCEPT"):
                acc_html += f'<span class="acc y" style="background:{COLOR[r]}">{TITLE[r]}</span>'
            elif any(v["option"] == oid for v in src.get("vetoes", [])):
                acc_html += f'<span class="acc n">{TITLE[r]}</span>'
            else:
                acc_html += f'<span class="acc">{TITLE[r]}</span>'
        meta = summary_line(o["metrics"]) if o.get("metrics") else "proposed by an agent, checked by code"
        st.markdown(f'<div class="opt {cls}"><div><b>{oid} · {o["name"]}</b> &nbsp;{tag}</div>'
                    f'<div class="meta">{meta}</div>{acc_html}</div>', unsafe_allow_html=True)

    st.markdown("**Cross-domain checks (code)**")
    viol = sum(len(v) for v in steps["options"]["safety_violations"].values())
    rows = [("Red lines: separation, final reserve, gates", '<span class="ok">✓ all</span>' if viol == 0
             else f'<span class="bad">✗ {viol}</span>')]
    relaxed = {r["metric"]: r for r in hard.get("relaxed", [])}
    for lim in hard.get("limits", []):
        breakers = [oid for oid, vs in hard["violations"].items() if any(v["rule"] == lim["metric"] for v in vs)]
        sign = "≤" if lim["kind"] == "max" else "≥"
        label = f"{TITLE[lim['role']]}: {lim['description']} {sign} {lim['limit']}"
        if breakers:
            status = f'<span class="bad">✗ {" ".join(breakers)}</span>'
        elif lim["metric"] in relaxed:
            status = f'<span class="warn">relaxed {relaxed[lim["metric"]]["limit"]} → {lim["limit"]}</span>'
        else:
            status = '<span class="ok">✓ all</span>'
        rows.append((label, status))
    st.markdown("".join(f'<div class="check"><span>{a}</span>{b}</div>' for a, b in rows), unsafe_allow_html=True)

    st.markdown("**Decision**")
    if not done:
        st.caption("Waiting for the agents.")
        return
    dec = steps["decision"]
    how = {"consensus": "consensus in turn 2 of 3", "mediator_fallback": "deterministic fallback (max-min)",
           "vote_fallback": "deterministic fallback (Borda + veto)"}[dec["method"]]
    perf = steps["done"]["log"]["perf"] if "done" in steps else []
    calls = f"{len(perf)} API calls · {sum(p['latency_seconds'] for p in perf):.0f} s · " \
            f"{sum(p['input_tokens'] + p['output_tokens'] for p in perf):,} tokens" if perf else "no API calls (agents off)"
    st.markdown(f'<div class="opt win"><b>Option {dec["winner"]}</b> · {how}<br>'
                f'<span class="meta">{calls} · 0 safety violations</span></div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
@st.cache_data
def load_events() -> list[dict]:
    with open("events/cascades.json", encoding="utf-8") as f:
        events = json.load(f)["events"]
    return sorted(events, key=lambda e: (not e.get("demo"), e["id"]))


events = load_events()
by_id = {e["id"]: e for e in events}

top = st.columns([3, 1.2, 1])
event_id = top[0].selectbox("Disruption (real arrival banks, DOT 2015)", list(by_id),
                            format_func=lambda i: ("★ " if by_id[i].get("demo") else "") + by_id[i]["label"],
                            label_visibility="collapsed")
use_llm = top[1].toggle("Claude agents", value=st.query_params.get("agents") != "0",
                        help="Off: deterministic stand-ins for every Claude step. No API cost, same safety checks.")
run_clicked = top[2].button("▶ Run", type="primary", width="stretch")
event = by_id[event_id]
truth = event["disruption_truth"]

closure = truth["runway_closures"][0]
st.markdown(
    f"**#{event['airport'].lower()}-runway-closure** &nbsp; {event['airport']} · "
    f"runway {closure['runway']} closed {solver.hhmm(closure['start_min'])}–{solver.hhmm(closure['end_min'])} · "
    f"{truth['flight_rules']} · {len(event['flights'])} arrivals from {event['window_start']} · "
    f"{len(event['gates'])} gates on this pier"
)
st.caption("For the airline duty manager and the tower supervisor. Today this is phone calls between the tower, "
           "the airline and the ramp. Here three separate Claude agents negotiate; code checks safety and decides; "
           "the duty manager approves.")

with st.expander("Incoming messages and flights (edit the messages to change the situation)"):
    mcols = st.columns(3)
    texts = {
        "notam": mcols[0].text_area("NOTAM", event["texts"]["notam"], key=f"notam-{event_id}", height=100),
        "metar": mcols[1].text_area("METAR", event["texts"]["metar"], key=f"metar-{event_id}", height=100),
        "ops_note": mcols[2].text_area("Ops note", event["texts"]["ops_note"], key=f"ops-{event_id}", height=100),
    }
    st.dataframe(pd.DataFrame([{
        "Flight": f["id"], "From": f["origin"], "ETA": f["eta"], "Real arrival delay (min)": f["real_arrival_delay"],
        "Aircraft*": f["aircraft_type"], "Fuel above reserve (min)*": f["extra_fuel_min"],
        "Planned gate*": f["preferred_gate"], "Connecting pax*": sum(c["pax"] for c in f["connections"]),
    } for f in event["flights"]]), hide_index=True, width="stretch")
    st.caption("Real: flights, times, delays, onward flights (DOT 2015). *Synthetic, derived deterministically: "
               "aircraft type, fuel, gate IDs, passenger counts, and the three messages. Simplified rules, "
               "not for operational use.")

edited = texts != event["texts"]
autorun = st.query_params.get("autorun") == "1" and "room" not in st.session_state
chat_col, side_col = st.columns([2, 1.1], gap="large")
side = side_col.empty()

if run_clicked or autorun:
    steps = {}
    with chat_col:
        for ev in negotiate_mediated.run(event, texts, use_llm=use_llm):
            steps[ev["type"]] = ev
            render_event(ev, steps, event, edited, live=True)
            with side.container():
                draw_side(steps)
    st.session_state["room"] = {"event_id": event_id, "texts": texts, "steps": steps}

result = st.session_state.get("room")
if not result or result["event_id"] != event_id:
    with chat_col:
        st.info("Press **Run**. Every message that appears is one structured message from one separate agent, "
                "or one step done by code.")
    with side.container():
        draw_side({})
    st.stop()

steps = result["steps"]
if not (run_clicked or autorun):
    with chat_col:
        for t in ("intake", "rules", "opening", "recommendation", "responses", "decision", "brief"):
            if t in steps:
                render_event(steps[t], steps, event, result["texts"] != event["texts"], live=False)
    with side.container():
        draw_side(steps)

# ---------------------------------------------------------------------------
# The plan: a recommendation for a person to approve
# ---------------------------------------------------------------------------
options, baseline, decision = steps["done"]["options"], steps["done"]["baseline"], steps["decision"]
winner = next(o for o in options if o["id"] == decision["winner"])
wm, bm = winner["metrics"], baseline["metrics"]

st.subheader(f"Recommended plan: option {winner['id']} · {winner['name']}")
k = st.columns(5)
k[0].metric("Total delay", f"{wm['total_delay_min']} min", f"{wm['total_delay_min'] - bm['total_delay_min']:+d} vs no coordination",
            delta_color="inverse")
k[1].metric("Missed connections", f"{wm['pax_missed']} pax", f"{wm['pax_missed'] - bm['pax_missed']:+d}", delta_color="inverse")
k[2].metric("Lowest fuel margin", f"{wm['min_fuel_margin_min']} min", f"{wm['min_fuel_margin_min'] - bm['min_fuel_margin_min']:+d}")
k[3].metric("Knock-on departure delay", f"{wm['downstream_delay_min']} min",
            f"{wm['downstream_delay_min'] - bm['downstream_delay_min']:+d}", delta_color="inverse")
k[4].metric("Diversions", wm["diversions"], f"{wm['diversions'] - bm['diversions']:+d}", delta_color="inverse")

which = st.radio("Show", [f"Option {winner['id']}", "No-coordination baseline (land in ETA order, wait for planned gate)"],
                 horizontal=True, label_visibility="collapsed")
plan = winner if which.startswith("Option") else baseline
st.altair_chart(charts.timeline(plan, event["flights"], steps["options"]["problem"]["closures"],
                                steps["options"]["problem"]["turnaround_min"]), width="stretch")
st.dataframe(pd.DataFrame([{
    "Flight": r["flight"], "ETA": next(f["eta"] for f in event["flights"] if f["id"] == r["flight"]),
    "New landing": r.get("landing", "DIVERTS"), "Holding (min)": r["holding_min"],
    "Gate": r.get("gate", "-") + (" (changed)" if r.get("gate_changed") else ""),
    "Fuel margin (min)": None if r["diverted"] else r["fuel_margin_min"],
    "Missed connections": ", ".join(r.get("missed_connections", [])) or "-",
    "Knock-on (min)": r.get("knock_on_min"),
} for r in plan["rows"]]), hide_index=True, width="stretch")

a, b, c = st.columns([1, 1, 2])
if a.button("✅ Approve plan (duty manager)", type="primary", width="stretch"):
    st.session_state["approved"] = (event_id, time.strftime("%H:%M:%S"))
b.download_button("Download consensus log (JSON)", json.dumps(steps["done"]["log"], indent=1),
                  file_name=f"{event_id}-consensus-log.json", width="stretch")
approved = st.session_state.get("approved")
if approved and approved[0] == event_id:
    c.success(f"Approved at {approved[1]}. Demo only: nothing is sent to any operational system.")
else:
    c.caption("The system recommends. A qualified person decides. Agents can misread numbers; the tables are computed by code.")
