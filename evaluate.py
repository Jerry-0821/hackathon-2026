"""Evaluation over many real disrupted arrival banks, compared against baselines.

    python evaluate.py              # all events, deterministic only (no API cost, ~1 min)
    python evaluate.py --agents 5   # also run the full LLM pipeline on 5 events

Methods compared on every event (same disruption, same safety rules):
- baseline:      first-come-first-served, every flight waits for its planned gate
                 (no coordination - what happens if nobody re-plans)
- min_delay:     single-objective optimiser, minimises total delay only
- negotiated:    the multi-stakeholder process with deterministic stand-in agents
                 (same voting rule as the live demo, no LLM, fully reproducible)
- llm_agents:    the live pipeline with Claude agents (only with --agents N)

Writes events/evaluation_results.json, which the Evaluation page reads.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import negotiate
import solver

EVENTS_PATH = Path("events/cascades.json")
OUT_PATH = Path("events/evaluation_results.json")
METRICS = ["diversions", "total_delay_min", "pax_missed", "min_fuel_margin_min", "gate_changes",
           "downstream_delay_min", "crew_overtime_min"]


def load_events() -> list[dict]:
    return json.loads(EVENTS_PATH.read_text(encoding="utf-8"))["events"]


def evaluate_event(event: dict) -> dict:
    problem = solver.build_problem(event, event["disruption_truth"])
    gen = solver.generate_options(problem)
    options = gen["options"]
    positions = {r: negotiate.proxy_position(r, options) for r in negotiate.ROLES}
    decision = negotiate.decide(options, positions)
    chosen = {
        "baseline": gen["baseline"],
        "min_delay": min(options, key=lambda o: (o["metrics"]["diversions"], o["metrics"]["total_delay_min"])),
        "negotiated": next(o for o in options if o["id"] == decision["winner"]),
    }
    violations = sum(len(solver.verify(problem, p)) for p in options + [gen["baseline"]])
    return {
        "event_id": event["id"],
        "airport": event["airport"],
        "flights": len(event["flights"]),
        "flight_rules": event["disruption_truth"]["flight_rules"],
        "explored": gen["explored"],
        "front_size": gen["front_size"],
        "safety_violations": violations,
        "methods": {m: {k: p["metrics"][k] for k in METRICS} for m, p in chosen.items()},
    }


def run_llm(event: dict) -> dict:
    start = time.monotonic()
    out = {"event_id": event["id"]}
    for step in negotiate.run(event, use_llm=True):
        if step["type"] == "intake":
            out["intake_matches_truth"] = step["matches_truth"]
            out["intake_warnings"] = step["warnings"]
        elif step["type"] == "round":
            out.setdefault("fallbacks", []).extend(step["warnings"])
        elif step["type"] == "done":
            options = step["options"]
            winner = next(o for o in options if o["id"] == step["decision"]["winner"])
            proxy = negotiate.decide(options, {r: negotiate.proxy_position(r, options) for r in negotiate.ROLES})
            out["winner"] = winner["id"]
            out["proxy_winner"] = proxy["winner"]
            out["metrics"] = {k: winner["metrics"][k] for k in METRICS}
            out["vetoes"] = {k: len(v) for k, v in step["decision"]["vetoes"].items()}
            perf = step["log"]["perf"]
            out["api_calls"] = len(perf)
            out["input_tokens"] = sum(p["input_tokens"] for p in perf)
            out["output_tokens"] = sum(p["output_tokens"] for p in perf)
    out["wall_clock_seconds"] = round(time.monotonic() - start, 1)
    return out


def summarise(rows: list[dict]) -> dict:
    summary = {}
    for method in ["baseline", "min_delay", "negotiated"]:
        summary[method] = {k: round(statistics.mean(r["methods"][method][k] for r in rows), 2) for k in METRICS}
        summary[method]["events_with_diversion"] = sum(r["methods"][method]["diversions"] > 0 for r in rows)
    wins = {}
    for k in METRICS:
        better_is_higher = k == "min_fuel_margin_min"
        b = [(r["methods"]["negotiated"][k], r["methods"]["baseline"][k]) for r in rows]
        wins[k] = {"better": sum((n > x) if better_is_higher else (n < x) for n, x in b),
                   "same": sum(n == x for n, x in b),
                   "worse": sum((n < x) if better_is_higher else (n > x) for n, x in b)}
    summary["negotiated_vs_baseline"] = wins
    summary["total_safety_violations"] = sum(r["safety_violations"] for r in rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=0, help="also run the LLM pipeline on N events")
    args = parser.parse_args()

    events = load_events()
    rows = []
    for i, e in enumerate(events, 1):
        rows.append(evaluate_event(e))
        print(f"[{i}/{len(events)}] {e['id']} done", flush=True)

    result = {"num_events": len(rows), "num_flights": sum(r["flights"] for r in rows),
              "summary": summarise(rows), "events": rows}

    if args.agents:
        # Demo events first, then the rest in file order.
        ordered = sorted(events, key=lambda e: not e.get("demo"))[: args.agents]
        llm_rows = []
        for e in ordered:
            print(f"LLM run: {e['id']}", flush=True)
            llm_rows.append(run_llm(e))
        result["llm_runs"] = llm_rows
    elif OUT_PATH.exists():
        # Keep earlier LLM results when re-running the cheap part.
        result["llm_runs"] = json.loads(OUT_PATH.read_text(encoding="utf-8")).get("llm_runs", [])

    OUT_PATH.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result["summary"], indent=1))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
