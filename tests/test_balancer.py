import balancer
import scenario

BUSY_HUB = "weather_dfw_mia_busy_hub"


def _p(t, gate):
    return {"revised_arrival_time": t, "gate_assignment": gate}


def test_balancer_finds_the_middle_ground_from_selfish_proposals():
    """ATC and Pilot want early, Ground wants late. Only ~10:48 keeps everyone at or above 6."""
    state = scenario.initial_state(BUSY_HUB)
    result = balancer.balance(
        {"atc": _p("10:40", "D9"), "pilot": _p("10:39", "D12"), "ground": _p("10:55", "D9")}, state
    )
    assert result["found"]
    assert result["proposal"]["revised_arrival_time"] == "10:48"
    assert result["below_minimum"] == []
    gave_up = {t["role"]: t["gave_up"] for t in result["trade_offs"]}
    assert gave_up["atc"] > 0 and gave_up["ground"] > 0  # both conceded something


def test_never_picks_an_unsafe_option():
    state = scenario.initial_state(BUSY_HUB)
    # Everyone asks for slots that break a red line (wake separation / gate occupied / too early).
    result = balancer.balance(
        {"atc": _p("10:36", "D9"), "pilot": _p("10:20", "D12"), "ground": _p("10:44", "D14")}, state
    )
    assert result["found"]
    assert balancer.check(result["proposal"], state)[0] is True


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


def test_malformed_proposals_do_not_crash_the_balancer():
    state = scenario.initial_state(BUSY_HUB)
    result = balancer.balance({"atc": _p("ten forty", "D9"), "pilot": {}, "ground": _p("10:48", "Z99")}, state)
    assert result["found"]
    assert balancer.check(result["proposal"], state)[0] is True


def test_full_window_search_reports_nothing_when_no_safe_option_exists():
    state = scenario.initial_state(BUSY_HUB)
    state["constraints"]["min_fuel_reserve_minutes"] = 1000
    assert balancer.balance({}, state, window_minutes=60)["found"] is False
