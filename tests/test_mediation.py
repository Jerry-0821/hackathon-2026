"""Offline tests for the rule tiers, the mediator and the 2-turn mediated negotiation. No API calls."""

import json

import ai
import ai_negotiation
import cascade_rules
import mediation
import negotiate_mediated
import solver
from agents import AgentCallError

with open("events/cascades.json", encoding="utf-8") as f:
    EVENTS = json.load(f)["events"]
DEMO = [e for e in EVENTS if e.get("demo")]

BASE = {"diversions": 0, "total_delay_min": 50, "max_delay_min": 20, "min_fuel_margin_min": 20,
        "pax_missed": 10, "downstream_delay_min": 30, "crew_overtime_min": 0, "gate_changes": 1}


def _opt(oid, cost=0.0, holding=10, **metrics):
    return {"id": oid, "name": oid, "balanced_cost": cost, "metrics": dict(BASE, **metrics),
            "rows": [{"flight": "X 1", "diverted": False, "holding_min": holding, "fuel_margin_min": 20}]}


# A is best for ATC, B best for the pilot, C best for ground, D is a fair middle option.
OPTIONS = [
    _opt("A", 0.1, total_delay_min=30, max_delay_min=10, pax_missed=40, downstream_delay_min=60),
    _opt("B", 0.2, total_delay_min=70, max_delay_min=30, pax_missed=0, min_fuel_margin_min=30, downstream_delay_min=60),
    _opt("C", 0.3, total_delay_min=70, max_delay_min=30, pax_missed=40, downstream_delay_min=0),
    _opt("D", 0.4, total_delay_min=45, max_delay_min=18, pax_missed=15, min_fuel_margin_min=25, downstream_delay_min=25),
]


def _pos(ranking, acceptable=(), vetoes=()):
    return {"ranking": list(ranking), "acceptable": list(acceptable),
            "vetoes": [{"option": v, "reason": "test"} for v in vetoes]}


def _offline(monkeypatch):
    monkeypatch.setattr(ai, "parse_disruption", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))
    monkeypatch.setattr(ai, "write_brief", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))


# --- mediator ---------------------------------------------------------------

def test_scores_reward_each_agents_own_objectives():
    t = mediation.score_table(mediation.score_options(OPTIONS))
    assert max(t, key=lambda i: t[i]["atc"]) == "A"
    assert max(t, key=lambda i: t[i]["pilot"]) == "B"
    assert max(t, key=lambda i: t[i]["ground"]) == "C"
    assert all(0 <= s <= 10 for row in t.values() for s in row.values())


def test_declared_priorities_change_the_scores():
    default = mediation.score_table(mediation.score_options(OPTIONS))
    connections_only = mediation.score_table(mediation.score_options(
        OPTIONS, {"pilot": {"min_fuel_margin_min": "none", "pax_missed": "high"}}))
    # D has less fuel margin than B. Once the pilot declares fuel "none", that no longer costs D points.
    assert connections_only["D"]["pilot"] > default["D"]["pilot"]
    assert connections_only["B"]["pilot"] == 10.0
    assert connections_only["D"]["atc"] == default["D"]["atc"]  # other agents' scores are untouched


def test_recommends_the_option_where_the_worst_off_agent_does_best():
    scores = mediation.score_options(OPTIONS)
    rec = mediation.recommend(OPTIONS, scores, {r: _pos("ABCD") for r in mediation.ROLES})
    assert rec["option"] == "D"  # nobody's favourite, but nobody loses badly


def test_vetoed_rejected_and_hard_rule_breaking_options_are_skipped():
    scores = mediation.score_options(OPTIONS)
    positions = {r: _pos("ABCD") for r in mediation.ROLES}
    assert mediation.recommend(OPTIONS, scores, {**positions, "pilot": _pos("BDAC", vetoes="D")})["option"] != "D"
    assert mediation.recommend(OPTIONS, scores, positions, rejected=["D"])["option"] != "D"
    hard = {"D": [{"role": "ground", "rule": "crew_overtime_min", "detail": "x"}]}
    assert mediation.recommend(OPTIONS, scores, positions, hard_violations=hard)["option"] != "D"


def test_emergency_puts_the_pilot_first():
    scores = mediation.score_options(OPTIONS)
    rec = mediation.recommend(OPTIONS, scores, {r: _pos("ABCD") for r in mediation.ROLES},
                              emergency={"type": "minimum_fuel"})
    assert rec["option"] == "B"


def test_without_consensus_code_picks_from_options_all_three_accept():
    scores = mediation.score_options(OPTIONS)
    votes = {r: _pos("ABCD", acceptable="AC") for r in mediation.ROLES}
    out = mediation.resolve_without_consensus(OPTIONS, scores, votes, {"option": "D"})
    assert out["option"] in ("A", "C")
    assert mediation.resolve_without_consensus(OPTIONS, scores, {r: _pos("ABCD") for r in mediation.ROLES},
                                               {"option": "D"}) is None


# --- rule tiers ---------------------------------------------------------------

def test_hard_rules_are_relaxed_only_when_no_option_meets_them():
    opts = [_opt("A", crew_overtime_min=40), _opt("B", crew_overtime_min=35)]
    hard = cascade_rules.evaluate(opts)
    relaxed = [r for r in hard["relaxed"] if r["metric"] == "crew_overtime_min"]
    assert relaxed and relaxed[0]["to"] == 35
    assert hard["violations"]["B"] == [] and hard["violations"]["A"]


def test_minimum_fuel_is_declared_automatically_on_real_events():
    flagged = []
    for e in EVENTS:
        options = solver.generate_options(solver.build_problem(e, e["disruption_truth"]))["options"]
        em = cascade_rules.detect_minimum_fuel(options)
        if em:
            flagged.append(e["id"])
            hard = cascade_rules.evaluate(options, em)
            assert any(r["reason"] == "Minimum Fuel emergency" for r in hard["relaxed"])
    assert 1 <= len(flagged) < len(EVENTS)  # rare, not every event


# --- information --------------------------------------------------------------

def test_private_views_do_not_leak_other_domains():
    e = DEMO[0]
    problem = solver.build_problem(e, e["disruption_truth"])
    views = {r: json.dumps(ai_negotiation.private_view(r, e, e["disruption_truth"], problem)) for r in mediation.ROLES}
    assert "fuel_above_final_reserve_min" in views["pilot"]
    assert "fuel_above_final_reserve_min" not in views["atc"] + views["ground"]
    assert "crew_shift_end" in views["ground"] and "crew_shift_end" not in views["atc"] + views["pilot"]
    assert "runway_closures" in views["atc"] and "runway_closures" not in views["pilot"] + views["ground"]
    opt = solver.generate_options(problem)["options"][0]
    assert "pax_missed" not in ai_negotiation.my_option_view("atc", opt)["summary"]
    assert "crew_overtime_min" not in ai_negotiation.my_option_view("pilot", opt)["summary"]


# --- pipeline -----------------------------------------------------------------

def test_offline_pipeline_reaches_consensus_on_every_demo_event():
    for event in DEMO:
        steps = {e["type"]: e for e in negotiate_mediated.run(event, use_llm=False)}
        assert steps["decision"]["method"] == "consensus"
        assert steps["decision"]["consensus_turn"] == 2
        assert sum(len(v) for v in steps["options"]["safety_violations"].values()) == 0


def test_never_more_than_two_agent_turns(monkeypatch):
    _offline(monkeypatch)
    calls = {"open": 0, "respond": 0}

    def open_position(role, ctx):
        calls["open"] += 1
        return {**mediation.proxy_position(role, ctx["options"], ctx["scores"]), "source": "llm"}, {}

    def always_object(role, ctx, rec, published, thread):
        calls["respond"] += 1
        p = mediation.proxy_position(role, ctx["options"], ctx["scores"])
        return {**p, "vote": "OBJECT", "acceptable": [], "source": "llm"}, {}

    monkeypatch.setattr(ai_negotiation, "open_position", open_position)
    monkeypatch.setattr(ai_negotiation, "respond", always_object)
    steps = {e["type"]: e for e in negotiate_mediated.run(DEMO[0])}
    assert calls == {"open": 3, "respond": 3}  # one call per agent per turn, two turns
    assert steps["decision"]["method"] == "vote_fallback"


def test_agent_plan_is_checked_and_added_if_safe(monkeypatch):
    _offline(monkeypatch)
    event = DEMO[0]
    flights = [f["id"] for f in event["flights"]]

    def open_position(role, ctx):
        p = {**mediation.proxy_position(role, ctx["options"], ctx["scores"]), "source": "llm"}
        if role == "pilot":  # a plan no option uses: reverse landing order, flexible gates
            p["proposal"] = {"landing_order": list(reversed(flights)), "gate_policy": "flex"}
        return p, {}

    monkeypatch.setattr(ai_negotiation, "open_position", open_position)
    monkeypatch.setattr(ai_negotiation, "respond",
                        lambda role, ctx, rec, pub, th: ({**mediation.proxy_vote(role, ctx["options"], ctx["scores"], rec),
                                                          "source": "llm"}, {}))
    steps = {e["type"]: e for e in negotiate_mediated.run(event)}
    report = steps["opening"]["plans"]
    assert report and report[0]["by"] == "pilot" and report[0]["result"] in ("added", "same as existing option", "unsafe")
    if report[0]["result"] == "added":
        new = next(o for o in steps["done"]["options"] if o["id"] == report[0]["option"])
        assert solver.verify(steps["options"]["problem"], new) == []


def test_agents_receive_messages_addressed_to_them(monkeypatch):
    _offline(monkeypatch)
    seen = {}

    def open_position(role, ctx):
        p = mediation.proxy_position(role, ctx["options"], ctx["scores"])
        msgs = [{"from": role, "to": "ground", "type": "request", "text": f"{role} asks ground"}] if role != "ground" else []
        return {**p, "messages": msgs, "source": "llm"}, {}

    def respond(role, ctx, rec, published, thread):
        seen[role] = [m["text"] for m in thread if m["to"] in (role, "all") and m["from"] != role]
        return {**mediation.proxy_position(role, ctx["options"], ctx["scores"]), "vote": "ACCEPT", "source": "llm"}, {}

    monkeypatch.setattr(ai_negotiation, "open_position", open_position)
    monkeypatch.setattr(ai_negotiation, "respond", respond)
    list(negotiate_mediated.run(DEMO[0]))
    assert sorted(seen["ground"]) == ["atc asks ground", "pilot asks ground"]
    assert seen["atc"] == []


def test_pipeline_finishes_when_every_llm_call_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise AgentCallError("simulated outage")

    monkeypatch.setattr(ai, "_call", boom)
    steps = {e["type"]: e for e in negotiate_mediated.run(DEMO[0])}
    assert steps["opening"]["warnings"]
    assert all(p["source"] == "deterministic" for p in steps["opening"]["positions"].values())
    assert steps["decision"]["winner"]
    assert steps["brief"]["source"].startswith("template")
