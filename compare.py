"""Compare the two pipelines on the same scenarios with real Claude calls.

  sequential - orchestrator.py           (one proposal passed ATC -> Pilot -> Ground)
  balanced   - orchestrator_balanced.py  (parallel proposals + deterministic balancer)

Costs real API money, so it is a script you run on demand, not a test:
  python compare.py [trials_per_scenario_and_pipeline]   (default 3)
"""

from __future__ import annotations

import json
import statistics
import sys
import time

import orchestrator
import orchestrator_balanced
import scenario
import scoring

PIPELINES = {"sequential": orchestrator.run, "balanced": orchestrator_balanced.run}
# Haiku 4.5 list price per million tokens (input, output).
PRICE_PER_MTOK = (1.0, 5.0)


def run_one(name: str, scenario_id: str) -> dict:
    start = time.monotonic()
    perf = []
    for event in PIPELINES[name](scenario_id):
        if event["type"] == "agent_response" and "_perf" in event["response"]:
            perf.append(event["response"]["_perf"])
        elif event["type"] == "done":
            log = event["log"]
    outcome = log["outcome"]
    state = scenario.initial_state(scenario_id)
    scores = scoring.all_scores(outcome["proposal"], state) if outcome.get("proposal") else None
    tin, tout = sum(p["input_tokens"] for p in perf), sum(p["output_tokens"] for p in perf)
    return {
        "pipeline": name,
        "scenario_id": scenario_id,
        "status": outcome["status"],
        "rounds": len(log["turns"]),
        "final_proposal": outcome.get("proposal"),
        "scores": scores,
        "min_score": min(scores.values()) if scores else None,
        "total_score": round(sum(scores.values()), 1) if scores else None,
        "calls": len(perf),
        "seconds": round(time.monotonic() - start, 1),
        "cost_usd": round(tin / 1e6 * PRICE_PER_MTOK[0] + tout / 1e6 * PRICE_PER_MTOK[1], 4),
    }


def summarize(rows: list[dict]) -> dict:
    def avg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        return round(statistics.mean(vals), 2) if vals else None
    return {
        "runs": len(rows),
        "consensus_rate": round(sum(r["status"] == "consensus" for r in rows) / len(rows), 2),
        "fallback_rate": round(sum(r["status"] == "fallback" for r in rows) / len(rows), 2),
        "error_rate": round(sum(r["status"] == "error" for r in rows) / len(rows), 2),
        "avg_rounds": avg("rounds"),
        "avg_min_score": avg("min_score"),
        "avg_total_score": avg("total_score"),
        "avg_seconds": avg("seconds"),
        "avg_cost_usd": avg("cost_usd"),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    scenario_ids = [key for key, _ in scenario.list_scenarios()]
    results = []
    for sid in scenario_ids:
        for name in PIPELINES:
            for i in range(trials):
                r = run_one(name, sid)
                results.append(r)
                print(f"[{sid}] {name} {i + 1}/{trials}: {r['status']} in {r['rounds']} round(s) -> "
                      f"{r['final_proposal']} min score {r['min_score']}, ${r['cost_usd']}", flush=True)

    summary = {
        sid: {name: summarize([r for r in results if r["scenario_id"] == sid and r["pipeline"] == name])
              for name in PIPELINES}
        for sid in scenario_ids
    }
    with open("compare_results.json", "w") as f:
        json.dump({"trials": trials, "results": results, "summary": summary}, f, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
