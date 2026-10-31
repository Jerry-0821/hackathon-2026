"""The mediator (balancer.py): real safety filter + scoring from declared preferences."""

import balancer
import rules
import scenario

BUSY_HUB = "weather_dfw_mia_busy_hub"


def _p(t, gate):
    return {"revised_arrival_time": t, "gate_assignment": gate}


# What well-informed agents would publish for the busy hub scenario.
DECLARATIONS = {
    "atc": {"proposal": _p("10:40", "D9"), "blocked_slots": "10:34-10:38 any; 10:42-10:46 any; 10:50-10:54 any",
            "preferred_window": "10:38-10:42", "preferred_window_importance": "low"},
    "pilot": {"proposal": _p("10:39", "D12"), "latest_acceptable_arrival": "11:03",
              "preferred_window": "10:38-10:50", "preferred_window_importance": "high",
              "preferred_gate": "D12", "preferred_gate_importance": "high"},
    "ground": {"proposal": _p("10:52", "D9"), "blocked_slots": "10:28-11:03 D10; 10:38-11:08 D14",
               "preferred_window": "10:50-11:10", "preferred_window_importance": "high",
               "preferred_gate": "D9", "preferred_gate_importance": "low"},
}


def test_mediator_finds_the_middle_ground_from_declarations():
    state = scenario.initial_state(BUSY_HUB)
    result = balancer.mediate(DECLARATIONS, state)
    assert result["found"]
    assert result["proposal"] == _p("10:49", "D12")
    assert result["below_minimum"] == []
    gave_up = {t["role"]: t["gave_up"] for t in result["trade_offs"]}
    assert gave_up["atc"] > 0 and gave_up["pilot"] == 0  # ATC gives up time; pilot gets its wish


def test_scores_come_only_from_declarations():
    """Ground declares nothing about the crew - the mediator must not use the hidden crew data."""
    state = scenario.initial_state(BUSY_HUB)
    decl = {"ground": {"proposal": _p("10:40", "D12")}}  # no window -> own time +/- 2min is the ideal
    result = balancer.mediate(decl, state)
    assert result["scores"]["ground"] == 10.0
    assert result["proposal"]["revised_arrival_time"] in ("10:39", "10:40", "10:41")


def test_gate_preference_only_counts_if_declared():
    state = scenario.initial_state(BUSY_HUB)
    parsed = balancer.parse_declaration({"proposal": _p("10:40", "D9")}, state)
    assert parsed["gate"] is None


def test_declared_blocked_and_rejected_options_are_never_picked():
    state = scenario.initial_state(BUSY_HUB)
    first = balancer.mediate(DECLARATIONS, state)["proposal"]
    second = balancer.mediate(DECLARATIONS, state, excluded=[first])
    assert second["proposal"] != first
    for d in DECLARATIONS.values():
        assert balancer.declared_block(second["proposal"], balancer.parse_declaration(d, state)) is None


def test_never_picks_an_unsafe_option_even_if_declarations_are_wrong():
    state = scenario.initial_state(BUSY_HUB)
    wrong = {"atc": {"proposal": _p("10:36", "D10"), "preferred_window": "10:36-10:36",
                     "preferred_window_importance": "high", "preferred_gate": "D10"}}
    result = balancer.mediate(wrong, state)
    assert balancer.check(result["proposal"], state)[0] is True


def test_unreadable_declarations_are_ignored_with_a_warning():
    state = scenario.initial_state(BUSY_HUB)
    decl = {"atc": {"proposal": _p("10:40", "D9"), "preferred_window": "around ten forty",
                    "latest_acceptable_arrival": "soon", "preferred_gate": "Z99"}}
    result = balancer.mediate(decl, state)
    assert result["found"]
    assert len(result["parse_warnings"]["atc"]) == 3


def test_bad_format_unknown_gate_and_too_early_are_rejected():
    state = scenario.initial_state(BUSY_HUB)
    for proposal, rule in (
        (_p("10:48 AM", "D12"), "bad_format"),
        (_p("10:48", "Z99"), "unknown_gate"),
        (_p("10:00", "D12"), "earliest_arrival"),
        ({"gate_assignment": "D12"}, "bad_format"),
    ):
        ok, violations = balancer.check(proposal, state)
        assert ok is False
        assert any(v.startswith(rule) for v in violations), (proposal, violations)


def test_emergency_is_automatic_and_puts_the_pilot_first():
    state = scenario.initial_state(BUSY_HUB)
    assert rules.fuel_emergency_needed(state) is False
    rules.simulate_low_fuel(state)
    assert rules.fuel_emergency_needed(state) is True
    rules.declare_fuel_emergency(state)
    assert state["constraints"]["max_crew_overtime_minutes"] == 45
    assert state["constraints"]["min_fuel_reserve_minutes"] == 30  # red line unchanged
    result = balancer.mediate(DECLARATIONS, state)
    assert result["scores"]["pilot"] == 10.0
    assert balancer.check(result["proposal"], state)[0] is True


def test_declaration_that_blocks_its_own_proposal_is_repaired():
    # Seen in a live run: ATC proposed 10:39 but declared "latest 10:39" and "blocked 10:33-10:39".
    import scenario
    state = scenario.initial_state()
    decl = {
        "proposal": {"revised_arrival_time": "10:39", "gate_assignment": "D12"},
        "blocked_slots": "10:33-10:39 any; 10:41-10:47 any",
        "latest_acceptable_arrival": "10:39",
    }
    parsed = balancer.parse_declaration(decl, state)
    assert parsed["latest"] == balancer.minutes_since_midnight("10:39")  # own time is not after it: kept
    assert all(not (s <= parsed["latest"] <= e) for s, e, _ in parsed["blocked"])
    assert any("dropped blocked slot 10:33-10:39" in w for w in parsed["warnings"])
    assert balancer.declared_block(decl["proposal"], parsed) is None


def test_latest_before_own_proposal_is_dropped():
    import scenario
    state = scenario.initial_state()
    decl = {"proposal": {"revised_arrival_time": "10:50", "gate_assignment": "D12"},
            "latest_acceptable_arrival": "10:45"}
    parsed = balancer.parse_declaration(decl, state)
    assert parsed["latest"] is None
    assert parsed["warnings"]
