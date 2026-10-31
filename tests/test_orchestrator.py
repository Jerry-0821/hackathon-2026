"""Integration test proving the fallback path actually fires through the real orchestrator
wiring - not just fallback.py in isolation. Mocks agents.call_agent so no real API calls or
cost are involved; everything else (turn loop, constraint checks, mediator, fallback) is real."""

import agents
import orchestrator


def _always_reject(role, state, current_proposal, constraint_result, turn):
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
    def _boom(role, state, current_proposal, constraint_result, turn):
        raise agents.AgentCallError(f"{role}: simulated failure")

    monkeypatch.setattr(agents, "call_agent", _boom)

    events = list(orchestrator.run())
    done = [e for e in events if e["type"] == "done"]
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert done[0]["log"]["outcome"]["status"] == "error"
    assert done[0]["log"]["outcome"]["role"] == "atc"  # first agent in fixed order
