"""Offline tests for the runway-closure cascade: solver safety, intake validation, voting rule,
and the full pipeline with LLM calls failing. No API calls."""

import copy
import json

import ai
import negotiate
import solver
from agents import AgentCallError

with open("events/cascades.json", encoding="utf-8") as f:
    EVENTS = json.load(f)["events"]
DEMO = [e for e in EVENTS if e.get("demo")]


def _problem(event):
    return solver.build_problem(event, event["disruption_truth"])


def test_every_option_and_baseline_passes_the_independent_check():
    for event in DEMO:
        problem = _problem(event)
        gen = solver.generate_options(problem)
        assert 2 <= len(gen["options"]) <= 5
        for plan in gen["options"] + [gen["baseline"]]:
            assert solver.verify(problem, plan) == []


def test_verify_catches_broken_plans():
    problem = _problem(DEMO[0])
    plan = solver.generate_options(problem)["options"][0]
    landed = [r for r in plan["rows"] if not r["diverted"]]

    too_close = copy.deepcopy(plan)
    rows = [r for r in too_close["rows"] if not r["diverted"]]
    rows[1]["landing_min"] = rows[0]["landing_min"]  # two landings at the same minute
    assert any("needs" in v for v in solver.verify(problem, too_close))

    closed_gate = copy.deepcopy(plan)
    closed_gate["rows"][0]["gate"] = problem["gates_out"][0]
    assert any("out of service" in v for v in solver.verify(problem, closed_gate))
    assert landed  # sanity: the plan has landings to tamper with


def test_flight_short_of_fuel_diverts_instead_of_landing_below_reserve():
    event = copy.deepcopy(DEMO[0])
    last = max(range(len(event["flights"])), key=lambda i: event["flights"][i]["eta_min"])
    for i, f in enumerate(event["flights"]):
        f["extra_fuel_min"] = 0 if i == last else 60
    # Same ETA as the first flight, sequenced last: it must hold, and it has no fuel to hold.
    event["flights"][last]["eta_min"] = min(f["eta_min"] for f in event["flights"])
    problem = _problem(event)
    order = [i for i in range(len(event["flights"])) if i != last] + [last]
    plan = solver.simulate(problem, order, "flex")
    row = next(r for r in plan["rows"] if r["idx"] == last)
    assert row["holding_min"] > 0
    assert row["diverted"]
    assert plan["metrics"]["diversions"] == 1
    assert solver.verify(problem, plan) == []


def test_intake_validation_converts_utc_and_drops_unknown_items():
    event = DEMO[0]
    truth = event["disruption_truth"]["runway_closures"][0]
    off = event["utc_offset"] * 60
    start_utc = solver.hhmm(truth["start_min"] - off).replace(":", "")
    end_utc = solver.hhmm(truth["end_min"] - off).replace(":", "")
    good_gate = event["gates"][0]["id"]
    raw = {
        "runway_closures": [
            {"runway": "RWY " + truth["runway"].split("/")[1], "start": start_utc, "end": end_utc,
             "time_basis": "UTC", "quote": "x"},
            {"runway": "99X", "start": "1200", "end": "1300", "time_basis": "local", "quote": "y"},
        ],
        "gates_out_of_service": [{"gate": f"gate {good_gate}", "reason": "leak", "quote": "z"},
                                 {"gate": "Z99", "reason": "?", "quote": "?"}],
        "ceiling_ft": 800, "visibility_sm": 5, "other_notes": [], "summary": "",
    }
    d, warnings = ai.validate_disruption(raw, event)
    assert [(c["runway"], c["start_min"], c["end_min"]) for c in d["runway_closures"]] == [
        (truth["runway"], truth["start_min"], truth["end_min"])]
    assert [g["gate"] for g in d["gates_out_of_service"]] == [good_gate]
    assert d["flight_rules"] == "IMC"  # ceiling below 1000 ft
    assert len(warnings) == 2


def test_ranking_is_repaired_when_the_model_drops_or_repeats_ids():
    p = ai._clean_position({"ranking": ["b", "B", "Z"], "vetoes": [{"option": "c", "reason": "r"}],
                            "argument": "", "reply": ""}, ["A", "B", "C"])
    assert p["ranking"] == ["B", "A", "C"]
    assert p["ranking_repaired"] is True
    assert p["vetoes"] == [{"option": "C", "reason": "r"}]


def _opts(*ids):
    return [{"id": i, "balanced_cost": n} for n, i in enumerate(ids)]


def _pos(ranking, vetoes=()):
    return {"ranking": list(ranking), "vetoes": [{"option": v, "reason": "test"} for v in vetoes]}


def test_borda_winner_and_veto():
    options = _opts("A", "B", "C")
    positions = {"atc": _pos("ABC"), "pilot": _pos("ABC"), "ground": _pos("BAC")}
    assert negotiate.decide(options, positions)["winner"] == "A"
    positions["pilot"] = _pos("ABC", vetoes="A")
    d = negotiate.decide(options, positions)
    assert d["winner"] == "B"
    assert "A" not in d["eligible"]


def test_vetoes_are_set_aside_when_every_option_is_vetoed():
    options = _opts("A", "B")
    positions = {"atc": _pos("AB", vetoes="A"), "pilot": _pos("AB", vetoes="B"), "ground": _pos("AB")}
    d = negotiate.decide(options, positions)
    assert d["winner"] == "A"
    assert d["note"]


def test_tie_goes_to_lower_balanced_cost():
    options = _opts("A", "B")  # A has the lower balanced cost
    positions = {"atc": _pos("BA"), "pilot": _pos("AB")}
    assert negotiate.decide(options, positions)["winner"] == "A"


def test_pipeline_reaches_a_decision_when_every_llm_call_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise AgentCallError("simulated outage")

    monkeypatch.setattr(ai, "parse_disruption", boom)
    monkeypatch.setattr(ai, "rank_options", boom)
    monkeypatch.setattr(ai, "write_brief", boom)
    steps = {e["type"] if e["type"] != "round" else f"round{e['round']}": e for e in negotiate.run(DEMO[0])}
    assert steps["intake"]["warnings"]  # fell back to built-in disruption
    assert all(p["source"] == "deterministic" for p in steps["round2"]["positions"].values())
    assert steps["decision"]["winner"] in [o["id"] for o in steps["options"]["options"]]
    assert steps["brief"]["source"].startswith("template")
    assert sum(len(v) for v in steps["options"]["safety_violations"].values()) == 0
