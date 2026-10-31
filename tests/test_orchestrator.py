"""Integration test proving the fallback path actually fires through the real orchestrator
wiring - not just fallback.py in isolation. Mocks agents.call_agent so no real API calls or
cost are involved; everything else (turn loop, constraint checks, mediator, fallback) is real."""

import agents
import orchestrator


def _always_reject(role, state, current_proposal, constraint_result, turn, history=None):
    return {
        "from": role,
        "turn": turn,
        "proposal_under_review": current_proposal,
        "vote": "REJECT",
        "rationale": "test: forced rejection, never agrees",
        "utility_fields": {},
    }


def test_fallback_fires_after_three_turns_of_no_consensus(monkeypatch):
    monkeypatch.setattr(agents, "call_agent", _always_reject)

    events = list(orchestrator.run())
    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1

    log = done[0]["log"]
    outcome = log["outcome"]
    assert outcome["status"] == "fallback"
    assert len(log["turns"]) == 3  # ran the full 3-turn cap, never found consensus
    assert outcome["fallback_detail"]["source"] == "deterministic_safe_default_search"
    assert outcome["fallback_detail"]["constraint_check"]["passed"] is True


def test_agent_call_error_stops_the_run_cleanly(monkeypatch):
    def _boom(role, state, current_proposal, constraint_result, turn, history=None):
        raise agents.AgentCallError(f"{role}: simulated failure")

    monkeypatch.setattr(agents, "call_agent", _boom)

    events = list(orchestrator.run())
    done = [e for e in events if e["type"] == "done"]
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert done[0]["log"]["outcome"]["status"] == "error"
    assert done[0]["log"]["outcome"]["role"] == "atc"  # first agent in fixed order


def _counter_agent(counter):
    """ATC sends the given counter; Pilot and Ground reject."""

    def _agent(role, state, current_proposal, constraint_result, turn, history=None):
        response = {
            "from": role,
            "turn": turn,
            "proposal_under_review": current_proposal,
            "vote": "REJECT",
            "rationale": "test",
            "utility_fields": {},
        }
        if role == "atc":
            response["vote"] = "COUNTER"
            response["counter_proposal"] = dict(counter)
        return response

    return _agent


def _turn_starts(events):
    return [e["proposal"] for e in events if e["type"] == "turn_start"]


def test_badly_formatted_counter_is_treated_as_reject(monkeypatch):
    monkeypatch.setattr(
        agents, "call_agent", _counter_agent({"revised_arrival_time": "10:41 AM", "gate_assignment": "D12"})
    )
    events = list(orchestrator.run())
    atc = [e["response"] for e in events if e["type"] == "agent_response" and e["response"]["from"] == "atc"]
    assert all(r["vote"] == "REJECT" for r in atc)
    starts = _turn_starts(events)
    assert starts[1] == starts[0]  # baseline never changed


def test_constraint_failing_counter_is_not_adopted(monkeypatch):
    # Z99 is not a real gate, so this counter fails the constraint check.
    monkeypatch.setattr(
        agents, "call_agent", _counter_agent({"revised_arrival_time": "10:41", "gate_assignment": "Z99"})
    )
    events = list(orchestrator.run())
    atc = next(e["response"] for e in events if e["type"] == "agent_response")
    assert atc["vote"] == "COUNTER"
    assert atc["counter_constraint_check"]["passed"] is False
    starts = _turn_starts(events)
    assert starts[1] == starts[0]


def test_valid_counter_becomes_new_baseline_and_history_is_passed(monkeypatch):
    seen_history = []
    inner = _counter_agent({"revised_arrival_time": "10:41", "gate_assignment": "D12"})

    def _agent(role, state, current_proposal, constraint_result, turn, history=None):
        seen_history.append(list(history or []))
        return inner(role, state, current_proposal, constraint_result, turn, history)

    monkeypatch.setattr(agents, "call_agent", _agent)
    events = list(orchestrator.run())
    starts = _turn_starts(events)
    assert starts[1] == {"revised_arrival_time": "10:41", "gate_assignment": "D12"}
    assert seen_history[0] == []  # first call has no history
    assert seen_history[1][0]["from"] == "atc"  # pilot sees ATC's vote
    assert len(seen_history[3]) == 3  # turn 2 ATC sees all of turn 1
