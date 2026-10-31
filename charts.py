"""Altair charts for the cascade and evaluation pages.

Palette: the first three slots of the validated reference palette (blue, orange, aqua),
plus a neutral gray for background marks. Aqua is below 3:1 contrast on white, so every
chart that uses it also carries visible labels or a table view.
"""

from __future__ import annotations

import altair as alt
import pandas as pd


BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRAY, INK, MUTED = "#c9c8c2", "#0b0b0b", "#52514e"
BASE_DATE = pd.Timestamp("2015-01-01")


def _t(minutes: int) -> pd.Timestamp:
    return BASE_DATE + pd.Timedelta(minutes=int(minutes))


def plan_cloud(cloud: list[dict], options: list[dict], baseline: dict, winner_id: str | None) -> alt.Chart:
    """Every explored plan (gray), the Pareto-optimal ones (blue), the options and baseline labelled."""
    df = pd.DataFrame(cloud)
    df = df[df["diversions"] == 0]
    df["kind"] = df["on_front"].map({True: "Pareto-optimal plan", False: "Other safe plan"})
    marks = [{"label": o["id"] + (" (chosen)" if o["id"] == winner_id else ""), "kind": "Option shown to agents",
              "total_delay_min": o["metrics"]["total_delay_min"], "pax_missed": o["metrics"]["pax_missed"],
              "name": o["name"]} for o in options]
    marks.append({"label": "Baseline", "kind": "No-coordination baseline",
                  "total_delay_min": baseline["metrics"]["total_delay_min"],
                  "pax_missed": baseline["metrics"]["pax_missed"], "name": baseline["name"]})
    mdf = pd.DataFrame(marks)

    x = alt.X("total_delay_min:Q", title="Total delay (min)", scale=alt.Scale(zero=False))
    y = alt.Y("pax_missed:Q", title="Passengers missing connections", scale=alt.Scale(zero=False))
    color = alt.Color("kind:N", title=None, legend=alt.Legend(orient="top"),
                      scale=alt.Scale(domain=["Other safe plan", "Pareto-optimal plan", "Option shown to agents",
                                              "No-coordination baseline"],
                                      range=[GRAY, BLUE, INK, ORANGE]))
    background = alt.Chart(df).mark_circle(size=28, opacity=0.55).encode(
        x=x, y=y, color=color,
        tooltip=[alt.Tooltip("total_delay_min:Q", title="Total delay"), alt.Tooltip("pax_missed:Q", title="Missed pax")])
    points = alt.Chart(mdf).mark_point(size=150, filled=True, stroke="white", strokeWidth=2).encode(
        x=x, y=y, color=color,
        tooltip=["label:N", "name:N", alt.Tooltip("total_delay_min:Q", title="Total delay"),
                 alt.Tooltip("pax_missed:Q", title="Missed pax")])
    labels = alt.Chart(mdf).mark_text(align="left", dx=9, dy=-9, fontSize=13, fontWeight="bold", color=INK).encode(
        x=x, y=y, text="label:N")
    return (background + points + labels).properties(height=360)


def timeline(plan: dict, flights: list[dict], closures: list[tuple[int, int]], turnaround_min: int) -> alt.Chart:
    """One row per flight: holding (ETA to landing), taxi, then time at the gate."""
    by_id = {f["id"]: f for f in flights}
    rows = []
    order = []
    for r in plan["rows"]:
        f = by_id[r["flight"]]
        label = f"{r['flight']} ({f['aircraft_type']})"
        order.append(label)
        if r["diverted"]:
            rows.append({"flight": label, "phase": "Diverts (fuel)", "start": _t(f["eta_min"]),
                         "end": _t(f["eta_min"] + f["extra_fuel_min"]), "note": "diverts"})
            continue
        if r["holding_min"] > 0:
            rows.append({"flight": label, "phase": "Airborne holding", "start": _t(f["eta_min"]),
                         "end": _t(r["landing_min"]), "note": f"{r['holding_min']} min hold"})
        rows.append({"flight": label, "phase": "Taxi / gate wait", "start": _t(r["landing_min"]),
                     "end": _t(r["gate_at_min"]), "note": f"lands {r['landing']}"})
        turn = turnaround_min
        rows.append({"flight": label, "phase": "At gate", "start": _t(r["gate_at_min"]),
                     "end": _t(r["gate_at_min"] + turn),
                     "note": f"gate {r['gate']}" + (" (changed)" if r["gate_changed"] else "")})
    df = pd.DataFrame(rows)
    y = alt.Y("flight:N", sort=order, title=None)
    bars = alt.Chart(df).mark_bar(height=14, cornerRadius=3).encode(
        x=alt.X("start:T", title="Local time", axis=alt.Axis(format="%H:%M")),
        x2="end:T", y=y,
        color=alt.Color("phase:N", title=None, legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=["Airborne holding", "Taxi / gate wait", "At gate", "Diverts (fuel)"],
                                        range=[ORANGE, GRAY, BLUE, INK])),
        tooltip=["flight:N", "phase:N", "note:N", alt.Tooltip("start:T", format="%H:%M"),
                 alt.Tooltip("end:T", format="%H:%M")])
    gate_text = alt.Chart(df[df["phase"] == "At gate"]).mark_text(align="left", dx=4, color="white", fontSize=11).encode(
        x="start:T", y=y, text="note:N")
    layers = [bars, gate_text]
    if closures:
        cdf = pd.DataFrame([{"start": _t(s), "end": _t(e), "what": "Runway closed"} for s, e in closures])
        layers.insert(0, alt.Chart(cdf).mark_rect(color=GRAY, opacity=0.25).encode(
            x="start:T", x2="end:T", tooltip=["what:N", alt.Tooltip("start:T", format="%H:%M"),
                                               alt.Tooltip("end:T", format="%H:%M")]))
    return alt.layer(*layers).properties(height=34 * len(order) + 40)


METHOD_LABELS = {"baseline": "No coordination (FCFS)", "min_delay": "Min-delay optimiser",
                 "mediated": "Mediated (3 agents, demo default)", "vote": "Vote (3 agents)"}
# Slots 1-4 of the validated palette; adjacent bars only, and every bar carries a value label.
METHOD_COLORS = [ORANGE, AQUA, BLUE, "#eda100"]
METRIC_LABELS = {
    "total_delay_min": "Total delay (min)",
    "downstream_delay_min": "Knock-on departure delay (min)",
    "crew_overtime_min": "Crew overtime (min)",
    "pax_missed": "Missed connections (pax)",
    "min_fuel_margin_min": "Lowest fuel margin (min, higher is better)",
    "gate_changes": "Gate changes",
}


def method_bars(summary: dict) -> alt.Chart:
    """Small multiples, one per metric, each with its own scale (metrics have different units)."""
    rows = [{"method": METHOD_LABELS[m], "metric": METRIC_LABELS[k], "value": summary[m][k]}
            for m in METHOD_LABELS for k in METRIC_LABELS]
    df = pd.DataFrame(rows)
    color = alt.Color("method:N", title=None, legend=alt.Legend(orient="top"),
                      scale=alt.Scale(domain=list(METHOD_LABELS.values()), range=METHOD_COLORS))
    base = alt.Chart(df).encode(
        y=alt.Y("method:N", title=None, sort=list(METHOD_LABELS.values()), axis=None),
        x=alt.X("value:Q", title=None),
    )
    bars = base.mark_bar(cornerRadiusEnd=4, height=16).encode(color=color, tooltip=["method:N", "metric:N", "value:Q"])
    text = base.mark_text(align="left", dx=4, color=INK).encode(text=alt.Text("value:Q", format=".1f"))
    return (bars + text).properties(width=230, height=100).facet(
        facet=alt.Facet("metric:N", title=None, sort=list(METRIC_LABELS.values())), columns=3
    ).resolve_scale(x="independent")
