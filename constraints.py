"""Deterministic, non-AI constraint validator.

Runs after every proposal. A proposal that fails here can never be accepted by the
mediator, regardless of what the LLM agents voted. Thresholds are simplified,
clearly-labeled round numbers (see scenario.CONSTRAINTS), not FAA-certified values.
"""

from __future__ import annotations

from scenario import minutes_since_midnight


def check(proposal: dict, state: dict) -> tuple[bool, list[str]]:
    """proposal: {"revised_arrival_time": "HH:MM", "gate_assignment": "D12"}"""
    violations: list[str] = []

    arrival_minutes = minutes_since_midnight(proposal["revised_arrival_time"])
    gate = proposal["gate_assignment"]
    c = state["constraints"]

    # 1. Wake separation vs other scheduled traffic.
    min_sep = c["min_wake_separation_minutes"]
    for other in state["other_arrivals"]:
        gap = abs(arrival_minutes - other["arrival_minutes"])
        if gap < min_sep:
            violations.append(
                f"wake_separation: {gap}min gap to {other['flight']} at gate "
                f"{other['gate']} is below the {min_sep}min minimum"
            )

    # 2. Minimum fuel reserve at the proposed arrival time.
    naive = state["naive_arrival_minutes"]
    delay_added = arrival_minutes - naive  # extra holding beyond the naive estimate
    fuel_margin = state["derived"]["fuel_reserve_minutes_at_naive_arrival"] - max(delay_added, 0)
    min_fuel = c["min_fuel_reserve_minutes"]
    if fuel_margin < min_fuel:
        violations.append(
            f"fuel_reserve: projected margin {fuel_margin}min is below the {min_fuel}min minimum"
        )

    # 3. Ground crew overtime.
    shift_end = state["derived"]["ground_crew_shift_end_minutes"]
    overtime = max(arrival_minutes - shift_end, 0)
    max_overtime = c["max_crew_overtime_minutes"]
    if overtime > max_overtime:
        violations.append(
            f"crew_overtime: {overtime}min overtime exceeds the {max_overtime}min maximum"
        )

    # 4. Gate conflict - is the proposed gate occupied at the proposed arrival time?
    busy_windows = state["gate_occupancy"].get(gate, [])
    turnaround_end = arrival_minutes + 25  # assume a 25min minimum occupancy on arrival
    for start, end in busy_windows:
        if arrival_minutes < end and turnaround_end > start:
            violations.append(
                f"gate_conflict: gate {gate} is occupied {start}-{end} (mins since midnight), "
                f"conflicts with arrival window {arrival_minutes}-{turnaround_end}"
            )

    return (len(violations) == 0, violations)
