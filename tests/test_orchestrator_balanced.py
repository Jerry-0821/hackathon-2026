"""The parallel-proposal pipeline with fake agents - no API calls, no cost."""

import agents
import agents_balanced
import orchestrator_balanced

BUSY_HUB = "weather_dfw_mia_busy_hub"
SELFISH = {"atc": ("10:40", "D9"), "pilot": ("10:39", "D12"), "ground": ("10:55", "D9")}


def _fake_propose(seen=None):
    def propose(role, state, round_no, previous_round):
        if seen is not None:
            seen.append((role, round_no, previous_round))
        t, g = SELFISH[role]
        return {"from": role, "proposal": {"revised_arrival_time": t, "gate_assignment": g},
                "rationale": "test", "my_red_lines": "test", "_perf": {"input_tokens": 0, "output_tokens": 0}}
    return propose


def _fake_review(vote):
    def review(role, state, round_no, balanced, proposals):
        r = {"from": role, "vote": vote, "rationale": "test"}
        if vote == "OBJECT":
            r["revised_proposal"] = {"revised_arrival_time": "10:47", "gate_assignment": "D12"}
        return r
    return review


def _run(monkeypatch, propose, review):
    monkeypatch.setattr(agents_balanced, "propose", propose)
    monkeypatch.setattr(agents_balanced, "review", review)
    events = list(orchestrator_balanced.run(BUSY_HUB))
    return events, events[-1]["log"]


def test_consensus_on_the_balanced_option(monkeypatch):
    events, log = _run(monkeypatch, _fake_propose(), _fake_review("ACCEPT"))
    assert log["outcome"]["status"] == "consensus"
    assert log["outcome"]["turn_reached"] == 1
    assert log["outcome"]["proposal"]["revised_arrival_time"] == "10:48"
    assert any(e["type"] == "balance" for e in events)


def test_fallback_after_three_rounds_of_objections(monkeypatch):
    _, log = _run(monkeypatch, _fake_propose(), _fake_review("OBJECT"))
    assert len(log["turns"]) == 3
    detail = log["outcome"]["fallback_detail"]
    assert log["outcome"]["status"] == "fallback"
    assert detail["source"] == "balancer_full_search"
    assert detail["constraint_check"]["passed"] is True


def test_second_round_sees_first_round_objections(monkeypatch):
    seen = []
    _run(monkeypatch, _fake_propose(seen), _fake_review("OBJECT"))
    round2 = [prev for role, rnd, prev in seen if rnd == 2]
    assert len(round2) == 3
    assert round2[0]["balanced_proposal"]["revised_arrival_time"] == "10:48"
    assert {o["from"] for o in round2[0]["objections"]} == {"atc", "pilot", "ground"}


def test_concession_is_flagged_when_accepting_below_minimum(monkeypatch):
    import balancer
    real = balancer.balance

    def force_early(by_role, state, **kw):
        return real({"atc": {"revised_arrival_time": "10:39", "gate_assignment": "D12"}}, state)

    monkeypatch.setattr(balancer, "balance", force_early)
    _, log = _run(monkeypatch, _fake_propose(), _fake_review("ACCEPT"))
    assert "ground" in log["outcome"]["concessions"]  # ground scores < 6 at 10:39 but accepted


def test_agent_error_stops_cleanly(monkeypatch):
    def boom(role, state, round_no, previous_round):
        raise agents.AgentCallError(f"{role}: simulated failure")

    events, log = _run(monkeypatch, boom, _fake_review("ACCEPT"))
    assert log["outcome"]["status"] == "error"
    assert any(e["type"] == "error" for e in events)
