"""Performance benchmark: runs the real negotiation (real Claude API calls) N times per
scenario and reports convergence rate, turns-to-resolution, latency, and token usage.

Not a pytest test - it costs real API time/money, so it's a separate script you run
on demand: `python benchmark.py [trials_per_scenario]` (default 5).
"""

from __future__ import annotations

import json
import statistics
import sys
import time

import orchestrator
import scenario


def run_one(scenario_id: str) -> dict:
    start = time.monotonic()
    agent_calls = []
    votes_by_turn = {}

    for event in orchestrator.run(scenario_id):
        if event["type"] == "agent_response":
            r = event["response"]
            agent_calls.append(r.get("_perf", {}))
            votes_by_turn.setdefault(r["turn"], []).append(f"{r['from']}:{r['vote']}")
        elif event["type"] == "done":
            log = event["log"]

    wall_clock = round(time.monotonic() - start, 3)
    outcome = log["outcome"]

    return {
        "scenario_id": scenario_id,
        "status": outcome["status"],
        "turns_used": len(log["turns"]),
        "turn_reached": outcome.get("turn_reached"),
        "wall_clock_seconds": wall_clock,
        "num_agent_calls": len(agent_calls),
        "total_input_tokens": sum(c.get("input_tokens", 0) for c in agent_calls),
        "total_output_tokens": sum(c.get("output_tokens", 0) for c in agent_calls),
        "avg_call_latency_seconds": round(statistics.mean(c["latency_seconds"] for c in agent_calls), 3) if agent_calls else None,
        "votes_by_turn": votes_by_turn,
        "final_proposal": outcome.get("proposal"),
    }


def summarize(results: list[dict], scenario_id: str) -> dict:
    rows = [r for r in results if r["scenario_id"] == scenario_id]
    n = len(rows)
    consensus = [r for r in rows if r["status"] == "consensus"]
    fallback = [r for r in rows if r["status"] == "fallback"]
    error = [r for r in rows if r["status"] == "error"]

    return {
        "scenario_id": scenario_id,
        "trials": n,
        "consensus_rate": round(len(consensus) / n, 2) if n else None,
        "fallback_rate": round(len(fallback) / n, 2) if n else None,
        "error_rate": round(len(error) / n, 2) if n else None,
        "avg_turns_to_consensus": round(statistics.mean(r["turn_reached"] for r in consensus), 2) if consensus else None,
        "avg_wall_clock_seconds": round(statistics.mean(r["wall_clock_seconds"] for r in rows), 2) if rows else None,
        "avg_agent_calls_per_run": round(statistics.mean(r["num_agent_calls"] for r in rows), 1) if rows else None,
        "avg_call_latency_seconds": round(
            statistics.mean(r["avg_call_latency_seconds"] for r in rows if r["avg_call_latency_seconds"]), 3
        ) if any(r["avg_call_latency_seconds"] for r in rows) else None,
        "avg_input_tokens_per_run": round(statistics.mean(r["total_input_tokens"] for r in rows), 0) if rows else None,
        "avg_output_tokens_per_run": round(statistics.mean(r["total_output_tokens"] for r in rows), 0) if rows else None,
    }


def main():
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    scenario_ids = [key for key, _ in scenario.list_scenarios()]

    all_results = []
    for scenario_id in scenario_ids:
        for i in range(trials):
            print(f"[{scenario_id}] trial {i + 1}/{trials}...", flush=True)
            result = run_one(scenario_id)
            all_results.append(result)
            print(f"  -> {result['status']} in {result['turns_used']} turn(s), {result['wall_clock_seconds']}s")

    summaries = [summarize(all_results, sid) for sid in scenario_ids]

    with open("benchmark_results.json", "w") as f:
        json.dump({"trials_per_scenario": trials, "raw_results": all_results, "summaries": summaries}, f, indent=2)

    print("\n=== SUMMARY ===")
    for s in summaries:
        print(json.dumps(s, indent=2))
    print("\nFull results written to benchmark_results.json")


if __name__ == "__main__":
    main()
