"""The challenge constraints workbook is read correctly and its limits are enforced. No API calls."""

import copy
import json

import solver
import workbook

with open("events/cascades.json", encoding="utf-8") as f:
    EVENTS = json.load(f)["events"]


def test_workbook_values_are_read():
    w = workbook.load()
    atl = workbook.airport("ATL")
    assert (atl["active_runways"], atl["max_runway_ops_per_hour"], atl["min_turnaround_min"], atl["spare_gates"]) == (4, 90, 45, 3)
    assert workbook.airport("CLT")["max_runway_ops_per_hour"] == 60
    assert w["wake_nm"][("Heavy", "Large")] == 5 and w["wake_default_nm"] == 3
    fc = w["fuel_crew"]
    assert (fc["final_reserve_min"], fc["spare_fuel_on_arrival_min"], fc["gate_change_notice_min"]) == (30, 45, 20)
    assert (fc["burn_narrow_kg_h"], fc["burn_wide_kg_h"], fc["overtime_rate"]) == (2400, 6500, 1.5)
    assert "Input" in w["rules_of_play"]


def test_wake_separation_converts_nm_to_whole_minutes():
    assert workbook.wake_minutes("B772", "A320") == 3   # Heavy then Large, 5 NM at 140 kt = 2.14 min
    assert workbook.wake_minutes("B772", "B763") == 2   # Heavy then Heavy, 4 NM
    assert workbook.wake_minutes("A320", "B772") == 2   # any other pair, 3 NM
    assert workbook.wake_minutes("B752", "A320") == 2   # B757 is not in the workbook: any other pair


def test_usable_fuel_is_spare_minus_reserve_plus_contingency():
    assert workbook.usable_fuel_min(0) == 15
    assert workbook.usable_fuel_min(200) == 25  # + 5% of a 200 min trip


def test_landing_interval_is_fair_share_of_capacity():
    # Uncongested: the bank lands at its own rate. Congested: it gets its share of capacity.
    assert workbook.bank_landing_interval_min("ATL", 0, demand_ops_per_hour=60, bank_arrivals_per_hour=20) == 3
    assert workbook.bank_landing_interval_min("ATL", 0, demand_ops_per_hour=180, bank_arrivals_per_hour=20) == 6
    # Closing a runway never makes it faster, and closing all of them stops landings.
    for demand in (30, 90, 150):
        times = [workbook.bank_landing_interval_min("ATL", k, demand, 21) for k in range(5)]
        assert times == sorted(times)
        assert times[-1] >= 10**6


def test_every_event_meets_the_rules_of_play():
    for e in EVENTS:
        assert e["disruption_row"], e["id"]
        for row in e["disruption_row"]:
            assert row["weather_delay"] > 15 or row["late_aircraft_delay"] > 20


def test_no_gate_change_inside_the_notice_period():
    for e in [x for x in EVENTS if x.get("demo")]:
        problem = solver.build_problem(e, e["disruption_truth"])
        for plan in solver.generate_options(problem)["options"]:
            for r in plan["rows"]:
                if r["diverted"] or not r["gate_changed"]:
                    continue
                planned = next(f for f in e["flights"] if f["id"] == r["flight"])["preferred_gate"]
                assert planned in problem["gates_out"] or solver.gate_change_allowed(problem, r["landing_min"])


def test_verify_catches_a_gate_change_with_too_little_notice():
    e = next(x for x in EVENTS if x.get("demo"))
    problem = solver.build_problem(e, e["disruption_truth"])
    plan = copy.deepcopy(solver.generate_options(problem)["options"][0])
    row = next(r for r in plan["rows"] if not r["diverted"]
               and not solver.gate_change_allowed(problem, r["landing_min"])
               and next(f for f in e["flights"] if f["id"] == r["flight"])["preferred_gate"] not in problem["gates_out"])
    spare = next(g["id"] for g in problem["gates"] if g.get("spare") and g["id"] != row["gate"])
    row["gate"] = spare
    assert any("notice" in v for v in solver.verify(problem, plan))


def test_no_page_file_shadows_a_project_module():
    # A page named like a module (e.g. views/constraints.py) replaces that module once the page
    # has run, which crashed the single-flight pages.
    from pathlib import Path
    modules = {p.stem for p in Path(".").glob("*.py")}
    pages = {p.stem for p in Path("views").glob("*.py")}
    assert not modules & pages, f"page names shadow modules: {modules & pages}"
