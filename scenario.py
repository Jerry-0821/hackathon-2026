"""Fixed demo scenario: AA 1057, DFW -> MIA, 2015-01-01 (real row from data/flights.csv).

Fields not present in the DOT dataset (fuel, gates, crew, connections) are derived
deterministically from the real fields below so the scenario is reproducible, not random.
"""

FLIGHT = {
    "airline": "AA",
    "flight_number": "1057",
    "origin_airport": "DFW",
    "destination_airport": "MIA",
    "date": "2015-01-01",
    "scheduled_departure": "05:15",
    "scheduled_arrival": "08:56",
    "departure_delay_minutes": 108,
    "arrival_delay_minutes": 102,
    "weather_delay_minutes": 102,
    "cancelled": False,
    "diverted": False,
}


def _minutes_since_midnight(hhmm: str) -> int:
    digits = hhmm.replace(":", "")
    return int(digits[:2]) * 60 + int(digits[2:])


def _hhmm(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


SCHEDULED_ARRIVAL_MINUTES = _minutes_since_midnight(FLIGHT["scheduled_arrival"])
# Naive "no disruption handled" arrival: scheduled + full weather delay.
NAIVE_ARRIVAL_MINUTES = SCHEDULED_ARRIVAL_MINUTES + FLIGHT["weather_delay_minutes"]
NAIVE_ARRIVAL = _hhmm(NAIVE_ARRIVAL_MINUTES)

# Deterministic derivation: fuel reserve shrinks with delay, but scenario is built to
# start just above the safety line so the negotiation is meaningful, not pre-doomed.
DERIVED = {
    "fuel_reserve_minutes_at_naive_arrival": 34,  # plausible given a 102 min weather hold
    "connecting_passengers": 18,
    "current_gate": "D9",
    "requested_gate": "D12",
    "ground_crew_shift_end_minutes": NAIVE_ARRIVAL_MINUTES + 20,  # shift ends 20 min after naive arrival
}

# Other scheduled traffic near the naive arrival slot, for wake-separation / gate-conflict
# checks. Hardcoded, small, deterministic.
OTHER_ARRIVALS = [
    {"flight": "DL 824", "arrival_minutes": NAIVE_ARRIVAL_MINUTES - 2, "gate": "D10"},
    {"flight": "UA 331", "arrival_minutes": NAIVE_ARRIVAL_MINUTES + 6, "gate": "D14"},
]

GATE_OCCUPANCY = {
    # gate_id -> list of (start_minutes, end_minutes) busy windows around the scenario time
    "D9": [(NAIVE_ARRIVAL_MINUTES - 40, NAIVE_ARRIVAL_MINUTES - 5)],
    "D10": [(NAIVE_ARRIVAL_MINUTES - 10, NAIVE_ARRIVAL_MINUTES + 25)],
    "D12": [],  # free - the ground agent's preferred requested gate
    "D14": [(NAIVE_ARRIVAL_MINUTES + 0, NAIVE_ARRIVAL_MINUTES + 30)],
}

# Constraint thresholds - simplified, clearly-labeled round numbers (not FAA-accurate).
CONSTRAINTS = {
    "min_wake_separation_minutes": 3,
    "min_fuel_reserve_minutes": 30,
    "max_crew_overtime_minutes": 30,
}


def initial_state() -> dict:
    """Fresh mutable negotiation state for one run."""
    return {
        "flight": dict(FLIGHT),
        "derived": dict(DERIVED),
        "other_arrivals": [dict(a) for a in OTHER_ARRIVALS],
        "gate_occupancy": {k: list(v) for k, v in GATE_OCCUPANCY.items()},
        "constraints": dict(CONSTRAINTS),
        "naive_arrival_minutes": NAIVE_ARRIVAL_MINUTES,
        "naive_arrival": NAIVE_ARRIVAL,
    }


def minutes_since_midnight(hhmm: str) -> int:
    return _minutes_since_midnight(hhmm)


def hhmm(minutes: int) -> str:
    return _hhmm(minutes)
