"""Deterministic solver. No LLM.

Given a cascade event and a disruption (runway closure, gates out of service, weather),
it explores many landing orders and gate policies, simulates each one under the safety
rules, and keeps only plans that are safe by construction:

- landings respect runway rate and wake separation,
- no flight lands below final reserve fuel (it diverts instead),
- no flight uses a closed gate, a gate too small for it, or a gate that is occupied.

From all safe plans it computes the Pareto front over the stakeholders' objectives and
picks a few representative options for the agents to rank. `verify()` re-checks any plan
independently, so the safety claim can be tested.
"""

from __future__ import annotations

import itertools
import random

import reference_data as ref

# Objectives, all minimised. Fuel margin is negated so that "lower is better" holds.
OBJECTIVES = [
    ("diversions", "Diversions"),
    ("total_delay_min", "Total delay (min)"),
    ("pax_missed", "Missed connections (pax)"),
    ("neg_min_fuel_margin_min", "Fuel margin (min, negated)"),
    ("gate_changes", "Gate changes"),
    ("downstream_delay_min", "Knock-on departure delay (min)"),
    ("crew_overtime_min", "Crew overtime (min)"),
]

GATE_KEEP_MAX_WAIT = 20  # "keep" policy waits up to this long for the planned gate
RANDOM_ORDERS = 2000
DIVERSION_KNOCK_ON_MIN = 120  # assumed knock-on delay to the next departure of a diverted aircraft


def hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------------------------------------------------------------------------
# Problem setup
# ---------------------------------------------------------------------------

def build_problem(event: dict, disruption: dict) -> dict:
    """disruption has the shape of event['disruption_truth'] (see build_cascades.py)."""
    oos = {g["gate"] for g in disruption.get("gates_out_of_service", [])}
    rules = disruption.get("flight_rules", "VMC")
    return {
        "event": event,
        "flights": event["flights"],
        "gates": [g for g in event["gates"] if g["id"] not in oos],
        "gates_out": sorted(oos),
        "busy": {g: [(s, e) for s, e, _ in w] for g, w in event["gate_busy"].items()},
        "closures": [(c["start_min"], c["end_min"]) for c in disruption.get("runway_closures", [])],
        "num_runways": len(event["arrival_runways"]),
        "closed_spacing": ref.CLOSED_SPACING_MIN[rules],
        "flight_rules": rules,
        "crew_shift_end_min": event["crew_shift_end_min"],
    }


def runways_closed_at(problem: dict, t: int) -> int:
    return sum(1 for s, e in problem["closures"] if s <= t < e)


def rate_spacing(problem: dict, t: int) -> int:
    return problem["closed_spacing"] if runways_closed_at(problem, t) else ref.NORMAL_SPACING_MIN


def required_gap(problem: dict, prev_t: int, prev_wake: str, wake: str) -> int:
    return max(rate_spacing(problem, prev_t), ref.WAKE_SEPARATION_MIN[(prev_wake, wake)])


def skip_full_closure(problem: dict, t: int) -> int:
    """If every arrival runway is closed at t, move t to the end of the closure."""
    while runways_closed_at(problem, t) >= problem["num_runways"]:
        t = min(e for s, e in problem["closures"] if s <= t < e)
    return t


def gate_free_at(windows: list, start: int, length: int) -> int:
    """Earliest time >= start at which the gate is free for `length` minutes."""
    t = start
    for s, e in sorted(windows):
        if t + length <= s:
            break
        if t < e:
            t = max(t, e)
    return t


# ---------------------------------------------------------------------------
# Simulation of one plan
# ---------------------------------------------------------------------------

def simulate(problem: dict, order: list[int], gate_policy: str) -> dict:
    flights = problem["flights"]
    busy = {g: list(w) for g, w in problem["busy"].items()}
    gate_ids = [g["id"] for g in problem["gates"]]
    widebody_ok = {g["id"]: g["widebody"] for g in problem["gates"]}

    rows, prev_t, prev_wake = [], None, None
    for idx in order:
        f = flights[idx]
        t = f["eta_min"]
        if prev_t is not None:
            t = max(t, prev_t + required_gap(problem, prev_t, prev_wake, f["wake"]))
        t = skip_full_closure(problem, t)
        holding = t - f["eta_min"]
        if holding > f["extra_fuel_min"]:
            rows.append({"idx": idx, "flight": f["id"], "diverted": True, "holding_min": holding})
            continue

        turn = ref.TURNAROUND_MIN[f["widebody"]]
        earliest_in = t + ref.TAXI_IN_MIN
        candidates = [g for g in gate_ids if widebody_ok[g] or not f["widebody"]]
        free = {g: gate_free_at(busy[g], earliest_in, turn) for g in candidates}
        pref = f["preferred_gate"] if f["preferred_gate"] in free else None
        # keep: wait up to GATE_KEEP_MAX_WAIT for the planned gate. keep_strict (the
        # no-coordination baseline): always wait for it. flex: take the first free gate.
        max_wait = {"keep": GATE_KEEP_MAX_WAIT, "keep_strict": 10**6}.get(gate_policy, -1)
        if pref and free[pref] - earliest_in <= max_wait:
            gate = pref
        else:
            gate = min(candidates, key=lambda g: (free[g], g != pref, g))
        gate_at = free[gate]
        busy[gate].append((gate_at, gate_at + turn))

        missed = [c for c in f["connections"] if gate_at + ref.MIN_CONNECT_MIN > c["departure_min"]]
        nd = f["next_departure"]
        knock_on = max(0, gate_at + turn - nd["departure_min"]) if nd else 0
        rows.append({
            "idx": idx,
            "flight": f["id"],
            "diverted": False,
            "landing_min": t,
            "landing": hhmm(t),
            "holding_min": holding,
            "fuel_margin_min": f["extra_fuel_min"] - holding,
            "fuel_margin_kg": (f["extra_fuel_min"] - holding) * f["burn_kg_min"],
            "gate": gate,
            "gate_changed": gate != f["preferred_gate"],
            "gate_at_min": gate_at,
            "gate_wait_min": gate_at - earliest_in,
            "delay_min": gate_at - (f["eta_min"] + ref.TAXI_IN_MIN),
            "pax_missed": sum(c["pax"] for c in missed),
            "missed_connections": [c["flight"] for c in missed],
            "knock_on_min": knock_on,
        })
        prev_t, prev_wake = t, f["wake"]

    return _score(problem, order, gate_policy, rows)


def _score(problem: dict, order: list[int], gate_policy: str, rows: list[dict]) -> dict:
    flights = problem["flights"]
    landed = [r for r in rows if not r["diverted"]]
    diverted = [r for r in rows if r["diverted"]]
    pax_missed = sum(r["pax_missed"] for r in landed)
    pax_missed += sum(c["pax"] for r in diverted for c in flights[r["idx"]]["connections"])
    knock_on = sum(r["knock_on_min"] for r in landed)
    knock_on += sum(DIVERSION_KNOCK_ON_MIN for r in diverted if flights[r["idx"]]["next_departure"])
    last_service_end = max((r["gate_at_min"] + 30 for r in landed), default=0)
    min_fuel = min((r["fuel_margin_min"] for r in landed), default=0)
    metrics = {
        "diversions": len(diverted),
        "total_delay_min": sum(r["delay_min"] for r in landed),
        "max_delay_min": max((r["delay_min"] for r in landed), default=0),
        "pax_missed": pax_missed,
        "min_fuel_margin_min": min_fuel,
        "neg_min_fuel_margin_min": -min_fuel,
        "gate_changes": sum(r["gate_changed"] for r in landed),
        "gate_wait_min": sum(r["gate_wait_min"] for r in landed),
        "downstream_delay_min": knock_on,
        "crew_overtime_min": max(0, last_service_end - problem["crew_shift_end_min"]),
        "last_landing": hhmm(max((r["landing_min"] for r in landed), default=0)),
    }
    return {
        "order": list(order),
        "gate_policy": gate_policy,
        "rows": sorted(rows, key=lambda r: r.get("landing_min", 10**6)),
        "metrics": metrics,
        "signature": tuple((r["flight"], r.get("landing_min"), r.get("gate")) for r in rows),
    }


# ---------------------------------------------------------------------------
# Option generation
# ---------------------------------------------------------------------------

def heuristic_orders(problem: dict) -> dict[str, list[int]]:
    fl = problem["flights"]
    idx = range(len(fl))
    return {
        "fcfs": sorted(idx, key=lambda i: (fl[i]["eta_min"], i)),
        "fuel_deadline_first": sorted(idx, key=lambda i: (fl[i]["eta_min"] + fl[i]["extra_fuel_min"], i)),
        "connections_first": sorted(idx, key=lambda i: (-sum(c["pax"] for c in fl[i]["connections"]), i)),
        "heavies_first": sorted(idx, key=lambda i: (fl[i]["wake"] != "H", fl[i]["eta_min"], i)),
        "next_departure_first": sorted(
            idx, key=lambda i: ((fl[i]["next_departure"] or {}).get("departure_min", 10**6), i)),
    }


def candidate_orders(problem: dict, seed: int = 0) -> list[list[int]]:
    n = len(problem["flights"])
    orders = {tuple(o) for o in heuristic_orders(problem).values()}
    if n <= 6:
        orders.update(itertools.permutations(range(n)))
    else:
        rng = random.Random(seed)
        base = list(range(n))
        for _ in range(RANDOM_ORDERS):
            rng.shuffle(base)
            orders.add(tuple(base))
    return [list(o) for o in sorted(orders)]


def pareto_front(plans: list[dict]) -> list[dict]:
    keys = [k for k, _ in OBJECTIVES]
    vecs = [tuple(p["metrics"][k] for k in keys) for p in plans]
    front = []
    for i, v in enumerate(vecs):
        dominated = any(
            all(a <= b for a, b in zip(w, v)) and any(a < b for a, b in zip(w, v))
            for j, w in enumerate(vecs) if j != i
        )
        if not dominated:
            front.append(plans[i])
    return front


def _balanced_costs(plans: list[dict]) -> list[float]:
    keys = [k for k, _ in OBJECTIVES]
    lo = {k: min(p["metrics"][k] for p in plans) for k in keys}
    hi = {k: max(p["metrics"][k] for p in plans) for k in keys}
    return [
        sum((p["metrics"][k] - lo[k]) / (hi[k] - lo[k]) if hi[k] > lo[k] else 0.0 for k in keys)
        for p in plans
    ]


# Each view picks the best plan on the front for one stakeholder's priorities.
# Diversions always come first: no view trades a diversion for anything else.
VIEWS = [
    ("Fastest flow", "ATC", lambda m: (m["diversions"], m["total_delay_min"], m["max_delay_min"])),
    ("Protect connections", "Airline/Pilot", lambda m: (m["diversions"], m["pax_missed"], m["total_delay_min"])),
    ("Max fuel margin", "Pilot", lambda m: (m["diversions"], -m["min_fuel_margin_min"], m["total_delay_min"])),
    ("Ground-friendly", "Ground", lambda m: (m["diversions"], m["downstream_delay_min"] + m["crew_overtime_min"],
                                             m["gate_changes"], m["total_delay_min"])),
]


def generate_options(problem: dict, seed: int = 0) -> dict:
    plans, seen = [], set()
    for order in candidate_orders(problem, seed):
        for policy in ("keep", "flex"):
            p = simulate(problem, order, policy)
            if p["signature"] not in seen:
                seen.add(p["signature"])
                plans.append(p)

    front = pareto_front(plans)
    balanced = _balanced_costs(front)
    for p, b in zip(front, balanced):
        p["balanced_cost"] = round(b, 3)

    picked: list[tuple[str, dict]] = []
    for name, _, key in VIEWS:
        picked.append((name, min(front, key=lambda p: (key(p["metrics"]), p["balanced_cost"]))))
    picked.append(("Balanced", min(front, key=lambda p: (p["metrics"]["diversions"], p["balanced_cost"]))))

    options, by_sig = [], {}
    for name, plan in picked:
        if plan["signature"] in by_sig:
            by_sig[plan["signature"]]["name"] += f" + {name}"
            continue
        opt = {"id": chr(ord("A") + len(options)), "name": name, **plan}
        by_sig[plan["signature"]] = opt
        options.append(opt)

    order = heuristic_orders(problem)["fcfs"]
    baseline = simulate(problem, order, "keep_strict")
    baseline["name"] = "First-come-first-served, planned gates (no coordination)"

    rng = random.Random(seed)
    sample = plans if len(plans) <= 1500 else rng.sample(plans, 1500)
    front_sigs = {p["signature"] for p in front}
    cloud = [{"total_delay_min": p["metrics"]["total_delay_min"], "pax_missed": p["metrics"]["pax_missed"],
              "diversions": p["metrics"]["diversions"], "on_front": p["signature"] in front_sigs} for p in sample]

    return {"options": options, "baseline": baseline, "explored": len(plans),
            "front_size": len(front), "cloud": cloud}


# ---------------------------------------------------------------------------
# Independent safety check
# ---------------------------------------------------------------------------

def verify(problem: dict, plan: dict) -> list[str]:
    """Re-check a plan against every hard rule, without trusting the simulator."""
    violations = []
    flights = {f["id"]: f for f in problem["flights"]}
    landed = sorted((r for r in plan["rows"] if not r["diverted"]), key=lambda r: r["landing_min"])
    gate_info = {g["id"]: g for g in problem["gates"]}

    for prev, cur in zip(landed, landed[1:]):
        gap = cur["landing_min"] - prev["landing_min"]
        need = required_gap(problem, prev["landing_min"], flights[prev["flight"]]["wake"], flights[cur["flight"]]["wake"])
        if gap < need:
            violations.append(f"{cur['flight']}: {gap} min after {prev['flight']}, needs {need}")

    occupancy: dict[str, list] = {g: list(w) for g, w in problem["busy"].items()}
    for r in landed:
        f = flights[r["flight"]]
        if r["landing_min"] < f["eta_min"]:
            violations.append(f"{r['flight']}: lands before its ETA")
        if runways_closed_at(problem, r["landing_min"]) >= problem["num_runways"]:
            violations.append(f"{r['flight']}: lands while all arrival runways are closed")
        if f["extra_fuel_min"] - (r["landing_min"] - f["eta_min"]) < 0:
            violations.append(f"{r['flight']}: lands below final reserve fuel")
        if r["gate"] in problem["gates_out"] or r["gate"] not in gate_info:
            violations.append(f"{r['flight']}: gate {r['gate']} is out of service or unknown")
            continue
        if f["widebody"] and not gate_info[r["gate"]]["widebody"]:
            violations.append(f"{r['flight']}: widebody at narrowbody gate {r['gate']}")
        start, end = r["gate_at_min"], r["gate_at_min"] + ref.TURNAROUND_MIN[f["widebody"]]
        if start < r["landing_min"] + ref.TAXI_IN_MIN:
            violations.append(f"{r['flight']}: at gate before it could taxi in")
        for s, e in occupancy[r["gate"]]:
            if start < e and end > s:
                violations.append(f"{r['flight']}: gate {r['gate']} occupied {hhmm(s)}-{hhmm(e)}")
        occupancy[r["gate"]].append((start, end))
    return violations
