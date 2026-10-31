import constraints
import scenario


def _state():
    return scenario.initial_state()


def test_naive_baseline_violates_wake_separation():
    state = _state()
    proposal = {
        "revised_arrival_time": state["naive_arrival"],
        "gate_assignment": state["derived"]["current_gate"],
    }
    ok, violations = constraints.check(proposal, state)
    assert ok is False
    assert any("wake_separation" in v for v in violations)


def test_valid_proposal_passes_clean():
    state = _state()
    proposal = {
        "revised_arrival_time": scenario.hhmm(state["naive_arrival_minutes"] + 3),
        "gate_assignment": "D12",
    }
    ok, violations = constraints.check(proposal, state)
    assert ok is True
    assert violations == []


def test_fuel_reserve_violation():
    state = _state()
    # +9min clears wake separation but drops fuel margin (34 - 9 = 25) below the 30min minimum.
    proposal = {
        "revised_arrival_time": scenario.hhmm(state["naive_arrival_minutes"] + 9),
        "gate_assignment": "D12",
    }
    ok, violations = constraints.check(proposal, state)
    assert ok is False
    assert len(violations) == 1
    assert "fuel_reserve" in violations[0]


def test_gate_conflict_violation():
    state = _state()
    # +1min is wake-safe and fuel-safe, but D10 is occupied at that time.
    proposal = {
        "revised_arrival_time": scenario.hhmm(state["naive_arrival_minutes"] + 1),
        "gate_assignment": "D10",
    }
    ok, violations = constraints.check(proposal, state)
    assert ok is False
    assert len(violations) == 1
    assert "gate_conflict" in violations[0]


def test_large_delay_breaks_both_fuel_and_crew_overtime():
    # In this scenario the fuel margin (34min) is exhausted long before crew overtime (30min
    # past a shift ending naive+20) kicks in, so a single "far future" arrival trips both.
    state = _state()
    proposal = {
        "revised_arrival_time": scenario.hhmm(state["naive_arrival_minutes"] + 100),
        "gate_assignment": "D12",
    }
    ok, violations = constraints.check(proposal, state)
    assert ok is False
    joined = "; ".join(violations)
    assert "fuel_reserve" in joined
    assert "crew_overtime" in joined
