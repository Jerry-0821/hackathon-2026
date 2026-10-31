import mediator


def _vote(role, vote):
    return {"from": role, "vote": vote, "rationale": "", "utility_fields": {}}


def test_consensus_when_all_accept_and_constraint_valid():
    votes = [_vote("atc", "ACCEPT"), _vote("pilot", "ACCEPT"), _vote("ground", "ACCEPT")]
    result = mediator.evaluate_turn(votes, final_constraint_ok=True)
    assert result["consensus"] is True
    assert result["reasons"] == []


def test_no_consensus_on_dissent():
    votes = [_vote("atc", "ACCEPT"), _vote("pilot", "COUNTER"), _vote("ground", "ACCEPT")]
    result = mediator.evaluate_turn(votes, final_constraint_ok=True)
    assert result["consensus"] is False
    assert "pilot" in result["reasons"][0]


def test_constraint_failure_overrides_unanimous_accept():
    """The core safety requirement: agents agreeing is not enough if the deterministic
    validator says the proposal breaks a hard limit."""
    votes = [_vote("atc", "ACCEPT"), _vote("pilot", "ACCEPT"), _vote("ground", "ACCEPT")]
    result = mediator.evaluate_turn(votes, final_constraint_ok=False)
    assert result["consensus"] is False
    assert any("constraint validator vetoed" in r for r in result["reasons"])
