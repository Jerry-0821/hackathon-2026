"""Tests for the red line / hard rule / soft preference split, the scores, the fuel
emergency, and agents seeing each other's messages. No real API calls."""

import agents
import constraints
import orchestrator
import rules
import scenario
import scoring

BUSY_HUB = "weather_dfw_mia_busy_hub"


def _p(state, offset, gate):
    return {"revised_arrival_time": scenario.hhmm(state["naive_arrival_minutes"] + offset), "gate_assignment": gate}


def test_arriving_earlier_than_possible_is_a_red_line():
    state = scenario.initial_state()
    ok, violations = constraints.check(_p(state, -30, "D12"), state)
    assert ok is False
    tiers = rules.classify(violations)
    assert any(t["rule"] == "earliest_arrival" and t["tier"] == rules.RED_LINE for t in tiers)


def test_gate_occupied_is_red_line_but_short_turnaround_is_hard_rule():
    state = scenario.initial_state(BUSY_HUB)
    # D14 is busy from naive+0 to naive+30: arriving inside that window is a red line.
    inside = rules.classify(constraints.check(_p(state, 9, "D14"), state)[1])
    assert {t["rule"]: t["tier"] for t in inside}["gate_conflict"] == rules.RED_LINE
    # D10 is needed again later; arriving just before leaves too little turnaround - hard rule.
    state["gate_occupancy"]["D10"] = [(state["naive_arrival_minutes"] + 20, state["naive_arrival_minutes"] + 60)]
    before = rules.classify(constraints.check(_p(state, 9, "D10"), state)[1])
    assert {t["rule"]: t["tier"] for t in before}["turnaround_buffer"] == rules.HARD_RULE


def test_busy_hub_has_a_real_trade_off():
    """Pilot wants early (connections, fuel), Ground wants later (crew busy) - only the middle works."""
    state = scenario.initial_state(BUSY_HUB)
    early, middle = _p(state, 1, "D12"), _p(state, 10, "D12")
    assert constraints.check(early, state)[0] and constraints.check(middle, state)[0]
    early_scores, middle_scores = scoring.all_scores(early, state), scoring.all_scores(middle, state)
    assert early_scores["ground"] < rules.accept_min("ground", state)  # crew not ready yet
    assert all(middle_scores[r] >= rules.accept_min(r, state) for r in scoring.ROLES)
    assert early_scores["pilot"] >= middle_scores["pilot"]  # the pilot gives something up


def test_fuel_emergency_relaxes_hard_rules_but_never_red_lines():
    state = rules.apply_fuel_emergency(scenario.initial_state(BUSY_HUB))
    assert state["constraints"]["max_crew_overtime_minutes"] == 45
    assert state["constraints"]["min_fuel_reserve_minutes"] == 30  # red line unchanged
    assert rules.accept_min("ground", state) == 0 and rules.accept_min("pilot", state) == 6
    # The middle slot that worked before now breaks the fuel red line.
    ok, violations = constraints.check(_p(state, 10, "D12"), state)
    assert ok is False and any(v.startswith("fuel_reserve") for v in violations)


def test_each_agent_sees_the_previous_agents_messages(monkeypatch):
    seen = []

    def _fake(role, state, current_proposal, constraint_result, turn):
        seen.append((role, [m["from"] for m in state["negotiation_thread"]]))
        return {
            "from": role, "turn": turn, "proposal_under_review": current_proposal,
            "vote": "REJECT", "rationale": "test", "utility_fields": {},
            "my_red_lines": f"{role} red line", "i_need": f"{role} need",
        }

    monkeypatch.setattr(agents, "call_agent", _fake)
    list(orchestrator.run(BUSY_HUB))
    assert seen[0] == ("atc", [])
    assert seen[1] == ("pilot", ["atc"])
    assert seen[2] == ("ground", ["atc", "pilot"])


def test_agent_prompt_includes_thread_scores_and_tiers():
    state = scenario.initial_state(BUSY_HUB)
    state["negotiation_thread"] = [{"from": "pilot", "i_need": "ground: accept D12"}]
    proposal = _p(state, 0, "D9")
    msg = agents._build_user_message("ground", state, proposal, constraints.check(proposal, state), 1)
    assert "ground: accept D12" in msg
    assert "your_score_for_current_proposal" in msg
    assert "red_line" in msg  # violations come with their tier
    assert "RED LINES" in rules.role_brief("ground", state)
