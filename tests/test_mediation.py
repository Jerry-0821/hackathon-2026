"""Offline tests for the mediator and the mediated negotiation. No API calls."""

import json

import ai
import ai_negotiation
import mediation
import negotiate_mediated
from agents import AgentCallError

with open("events/cascades.json", encoding="utf-8") as f:
    DEMO = [e for e in json.load(f)["events"] if e.get("demo")]

BASE = {"diversions": 0, "total_delay_min": 50, "max_delay_min": 20, "min_fuel_margin_min": 20,
        "pax_missed": 10, "downstream_delay_min": 30, "crew_overtime_min": 0, "gate_changes": 1}


def _opt(oid, cost=0.0, **metrics):
    return {"id": oid, "name": oid, "balanced_cost": cost, "metrics": dict(BASE, **metrics)}


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


def test_scores_reward_each_agents_own_objectives():
    t = mediation.score_table(mediation.score_options(OPTIONS))
    assert max(t, key=lambda i: t[i]["atc"]) == "A"
    assert max(t, key=lambda i: t[i]["pilot"]) == "B"
    assert max(t, key=lambda i: t[i]["ground"]) == "C"
    assert all(0 <= s <= 10 for row in t.values() for s in row.values())


def test_recommends_the_option_where_the_worst_off_agent_does_best():
    scores = mediation.score_options(OPTIONS)
    rec = mediation.recommend(OPTIONS, scores, {r: _pos("ABCD") for r in mediation.ROLES})
    assert rec["option"] == "D"  # nobody's favourite, but nobody loses badly


def test_vetoed_and_rejected_options_are_skipped():
    scores = mediation.score_options(OPTIONS)
    positions = {r: _pos("ABCD") for r in mediation.ROLES}
    positions["pilot"] = _pos("BDAC", vetoes="D")
    assert mediation.recommend(OPTIONS, scores, positions)["option"] != "D"
    rec = mediation.recommend(OPTIONS, scores, {r: _pos("ABCD") for r in mediation.ROLES}, rejected=["D"])
    assert rec["option"] != "D"


def test_options_acceptable_to_all_come_first():
    scores = mediation.score_options(OPTIONS)
    positions = {r: _pos("ABCD", acceptable="A") for r in mediation.ROLES}
    rec = mediation.recommend(OPTIONS, scores, positions)
    assert rec["option"] == "A" and rec["acceptable_to_all"]


def test_offline_pipeline_reaches_consensus_on_every_demo_event():
    for event in DEMO:
        steps = {e["type"]: e for e in negotiate_mediated.run(event, use_llm=False)}
        assert steps["decision"]["method"] == "consensus"
        assert steps["decision"]["winner"] in [o["id"] for o in steps["options"]["options"]]
        assert sum(len(v) for v in steps["options"]["safety_violations"].values()) == 0


def test_no_consensus_after_three_rounds_falls_back_to_borda(monkeypatch):
    def open_position(role, event, disruption, options, scores):
        return {**mediation.proxy_position(role, options, scores), "source": "llm"}, {}

    def always_object(role, rnd, event, disruption, options, scores, rec, positions, thread):
        p = mediation.proxy_position(role, options, scores)
        return {**p, "vote": "OBJECT", "source": "llm",
                "messages": [{"from": role, "to": "all", "type": "reject", "text": "no"}]}, {}

    monkeypatch.setattr(ai, "parse_disruption", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))
    monkeypatch.setattr(ai, "write_brief", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))
    monkeypatch.setattr(ai_negotiation, "open_position", open_position)
    monkeypatch.setattr(ai_negotiation, "respond", always_object)
    events = list(negotiate_mediated.run(DEMO[0]))
    rounds = [e for e in events if e["type"] == "mediation_round"]
    decision = next(e for e in events if e["type"] == "decision")
    assert len(rounds) == negotiate_mediated.MAX_ROUNDS
    assert len({r["recommendation"]["option"] for r in rounds}) == len(rounds)  # each round tries a new option
    assert decision["method"] == "vote_fallback"


def test_agents_receive_messages_addressed_to_them(monkeypatch):
    seen = {}

    def open_position(role, event, disruption, options, scores):
        p = mediation.proxy_position(role, options, scores)
        msgs = [{"from": role, "to": "ground", "type": "request", "text": f"{role} asks ground"}] if role != "ground" else []
        return {**p, "messages": msgs, "source": "llm"}, {}

    def respond(role, rnd, event, disruption, options, scores, rec, positions, thread):
        seen[role] = [m["text"] for m in thread if m["to"] in (role, "all") and m["from"] != role]
        return {**mediation.proxy_position(role, options, scores), "vote": "ACCEPT", "source": "llm"}, {}

    monkeypatch.setattr(ai, "parse_disruption", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))
    monkeypatch.setattr(ai, "write_brief", lambda *a, **k: (_ for _ in ()).throw(AgentCallError("off")))
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
