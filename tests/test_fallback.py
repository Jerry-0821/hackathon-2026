import constraints
import fallback
import scenario


def _state():
    return scenario.initial_state()


def test_uses_last_constraint_valid_proposal_from_history():
    state = _state()
    valid_proposal = {"revised_arrival_time": "10:41", "gate_assignment": "D12"}
    turn_history = [
        {"turn": 1, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
        {"turn": 2, "final_proposal": valid_proposal, "final_constraint_ok": True},
        {"turn": 3, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
    ]
    result = fallback.resolve(state, turn_history)
    assert result["source"] == "last_constraint_valid_proposal"
    assert result["proposal"] == valid_proposal


def test_search_finds_a_constraint_valid_default_when_history_has_none():
    """Regression test: the original fallback blindly added a fixed +15min buffer, which in
    this scenario actually breaks the fuel reserve constraint. It must now search for and
    return something that genuinely passes constraints.check, not just assert a fixed value."""
    state = _state()
    turn_history = [
        {"turn": 1, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
        {"turn": 2, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
        {"turn": 3, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
    ]
    result = fallback.resolve(state, turn_history)
    assert result["source"] == "deterministic_safe_default_search"
    assert result["constraint_check"]["passed"] is True
    ok, violations = constraints.check(result["proposal"], state)
    assert ok is True
    assert violations == []


def test_escalates_to_human_review_when_no_valid_outcome_exists():
    state = _state()
    state["constraints"]["min_fuel_reserve_minutes"] = 1000  # impossible to satisfy
    turn_history = [
        {"turn": 1, "final_proposal": {"revised_arrival_time": "10:38", "gate_assignment": "D9"}, "final_constraint_ok": False},
    ]
    result = fallback.resolve(state, turn_history)
    assert result["source"] == "needs_human_review"
    assert result["constraint_check"]["passed"] is False
