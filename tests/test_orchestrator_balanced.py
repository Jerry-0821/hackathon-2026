"""The parallel-proposal pipeline with fake agents - no API calls, no cost."""

import agents
import agents_balanced
import orchestrator_balanced
from tests.test_balancer import DECLARATIONS

BUSY_HUB = "weather_dfw_mia_busy_hub"


def _fake_propose(seen=None):
    def propose(role, state, round_no, context):
        if seen is not None:
            seen.append((role, round_no, context))
        d = DECLARATIONS[role]
        return {"from": role, "proposal": dict(d["proposal"]), "rationale": "test",
                **{k: v for k, v in d.items() if k != "proposal"}, "_perf": {"input_tokens": 0, "output_tokens": 0}}
    return propose


def _fake_review(vote):
    def review(role, state, round_no, mediated, published):
        r = {"from": role, "vote": vote, "rationale": "test"}
        if vote == "OBJECT":
            r["objection_reason"] = "preference"
        return r
    return review


def _run(monkeypatch, propose, review, **kw):
    monkeypatch.setattr(agents_balanced, "propose", propose)
    monkeypatch.setattr(agents_balanced, "review", review)
    events = list(orchestrator_balanced.run(BUSY_HUB, **kw))
    return events, events[-1]["log"]


def test_consensus_on_the_mediated_option(monkeypatch):
    events, log = _run(monkeypatch, _fake_propose(), _fake_review("ACCEPT"))
    assert log["outcome"]["status"] == "consensus"
    assert log["outcome"]["turn_reached"] == 1
    assert log["outcome"]["proposal"] == {"revised_arrival_time": "10:49", "gate_assignment": "D12"}
    assert any(e["type"] == "balance" for e in events)


def test_rejected_options_are_excluded_then_fallback(monkeypatch):
    events, log = _run(monkeypatch, _fake_propose(), _fake_review("OBJECT"))
    picks = [e["result"]["proposal"] for e in events if e["type"] == "balance"]
    assert len(picks) == 3 and len({(p["revised_arrival_time"], p["gate_assignment"]) for p in picks}) == 3
    assert log["outcome"]["status"] == "fallback"
    assert log["outcome"]["fallback_detail"]["source"] == "mediator_final_pick"
    assert log["outcome"]["fallback_detail"]["constraint_check"]["passed"] is True


def test_agents_only_see_others_declarations_from_round_two(monkeypatch):
    seen = []
    _run(monkeypatch, _fake_propose(seen), _fake_review("OBJECT"))
    round1 = [c for role, rnd, c in seen if rnd == 1]
    round2 = {role: c for role, rnd, c in seen if rnd == 2}
    assert all(c["published"] == {} and c["previous_round"] is None for c in round1)
    assert set(round2["pilot"]["published"]) == {"atc", "pilot", "ground"}
    assert round2["pilot"]["previous_round"]["objections"][0]["objection_reason"] == "preference"


def test_private_views_do_not_leak_other_domains():
    import scenario
    state = scenario.initial_state(BUSY_HUB)
    atc = agents_balanced.private_view("atc", state)
    pilot = agents_balanced.private_view("pilot", state)
    ground = agents_balanced.private_view("ground", state)
    assert "other_scheduled_arrivals" in atc and "other_scheduled_arrivals" not in pilot | ground
    assert "fuel_reserve_minutes_at_naive_arrival" in pilot and "fuel_reserve_minutes_at_naive_arrival" not in atc | ground
    assert "crew_free_from" in ground and "crew_free_from" not in atc | pilot


def test_low_fuel_declares_emergency_automatically(monkeypatch):
    events, log = _run(monkeypatch, _fake_propose(), _fake_review("ACCEPT"), simulate_low_fuel=True)
    assert any(e["type"] == "emergency" for e in events)
    assert log["scenario"]["emergency"]["declared_automatically"] is True


def test_agent_error_stops_cleanly(monkeypatch):
    def boom(role, state, round_no, context):
        raise agents.AgentCallError(f"{role}: simulated failure")

    events, log = _run(monkeypatch, boom, _fake_review("ACCEPT"))
    assert log["outcome"]["status"] == "error"
    assert any(e["type"] == "error" for e in events)
