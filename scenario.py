"""Scenarios: real disrupted-flight rows from data/flights.csv (DOT 2015 dataset).

Fields not present in the DOT dataset (fuel, gates, crew, connections) are derived
deterministically from the real fields below so each scenario is reproducible, not random.
"""

from __future__ import annotations


def _minutes_since_midnight(hhmm: str) -> int:
    digits = hhmm.replace(":", "")
    return int(digits[:2]) * 60 + int(digits[2:])


def _hhmm(minutes: int) -> str:
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# Shared ops rulebook - simplified, clearly-labeled round numbers (not FAA-accurate).
CONSTRAINTS = {
    "min_wake_separation_minutes": 3,
    "min_fuel_reserve_minutes": 30,
    "max_crew_overtime_minutes": 30,
}

SCENARIOS = {
    "weather_dfw_mia": {
        "label": "AA 1057 - DFW to MIA - weather delay (102min)",
        "flight": {
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
            "late_aircraft_delay_minutes": 0,
            "delay_cause": "weather",
            "primary_delay_minutes": 102,
            "cancelled": False,
            "diverted": False,
        },
        "derived": {
            "fuel_reserve_minutes_at_naive_arrival": 34,
            "connecting_passengers": 18,
            "current_gate": "D9",
            "requested_gate": "D12",
            "ground_crew_shift_end_offset": 20,  # minutes after naive arrival
        },
        "other_arrivals_offsets": [
            {"flight": "DL 824", "offset": -2, "gate": "D10"},
            {"flight": "UA 331", "offset": 6, "gate": "D14"},
        ],
        "gate_occupancy_offsets": {
            "D9": [(-40, -5)],
            "D10": [(-10, 25)],
            "D12": [],
            "D14": [(0, 30)],
        },
    },
    "late_aircraft_mia_dca": {
        "label": "AA 2326 - MIA to DCA - late aircraft delay (31min)",
        "flight": {
            "airline": "AA",
            "flight_number": "2326",
            "origin_airport": "MIA",
            "destination_airport": "DCA",
            "date": "2015-01-01",
            "scheduled_departure": "06:45",
            "scheduled_arrival": "09:15",
            "departure_delay_minutes": 33,
            "arrival_delay_minutes": 31,
            "weather_delay_minutes": 0,
            "late_aircraft_delay_minutes": 31,
            "delay_cause": "late_aircraft",
            "primary_delay_minutes": 31,
            "cancelled": False,
            "diverted": False,
        },
        "derived": {
            "fuel_reserve_minutes_at_naive_arrival": 36,
            "connecting_passengers": 7,
            "current_gate": "B4",
            "requested_gate": "B7",
            "ground_crew_shift_end_offset": 10,
        },
        "other_arrivals_offsets": [
            {"flight": "DL 55", "offset": -2, "gate": "B5"},
            {"flight": "WN 812", "offset": 5, "gate": "B9"},
        ],
        "gate_occupancy_offsets": {
            "B4": [(-40, -5)],
            "B5": [(-10, 25)],
            "B7": [],
            "B9": [(0, 30)],
        },
    },
}

# Same real DOT row as weather_dfw_mia, but the derived ops fields give the agents real
# trade-offs: a ~25min fuel window, more traffic to sequence around, a crew that is busy
# until naive+12, and gates that favour different agents (D12 for connections, D9 for crew).
SCENARIOS["weather_dfw_mia_busy_hub"] = {
    "label": "AA 1057 - DFW to MIA - weather delay (102min) - busy hub, real trade-offs",
    "flight": dict(SCENARIOS["weather_dfw_mia"]["flight"]),
    "derived": {
        "fuel_reserve_minutes_at_naive_arrival": 55,
        "connecting_passengers": 18,
        "current_gate": "D9",
        "requested_gate": "D12",
        "ground_crew_shift_end_offset": 32,
        "crew_ready_offset": 12,  # crew is turning another aircraft until naive+12
        "ground_preferred_gate": "D9",  # nearest the crew base
        "connection_cutoff_offsets": {"D12": 12, "default": 6},  # D12 is next to the connecting concourse
    },
    "other_arrivals_offsets": [
        {"flight": "DL 824", "offset": -2, "gate": "D10"},
        {"flight": "UA 331", "offset": 6, "gate": "D14"},
        {"flight": "WN 210", "offset": 14, "gate": "D10"},
        {"flight": "B6 77", "offset": 20, "gate": "D14"},
    ],
    "gate_occupancy_offsets": {
        "D9": [(-40, -5)],
        "D10": [(-10, 25)],
        "D12": [],
        "D14": [(0, 30)],
    },
}

DEFAULT_SCENARIO = "weather_dfw_mia"


def list_scenarios() -> list[tuple[str, str]]:
    return [(key, spec["label"]) for key, spec in SCENARIOS.items()]


def initial_state(scenario_id: str = DEFAULT_SCENARIO) -> dict:
    """Fresh mutable negotiation state for one run of the given scenario."""
    spec = SCENARIOS[scenario_id]
    flight = spec["flight"]

    scheduled_arrival_minutes = _minutes_since_midnight(flight["scheduled_arrival"])
    naive_arrival_minutes = scheduled_arrival_minutes + flight["primary_delay_minutes"]
    naive_arrival = _hhmm(naive_arrival_minutes)

    other_arrivals = [
        {"flight": a["flight"], "arrival_minutes": naive_arrival_minutes + a["offset"], "gate": a["gate"]}
        for a in spec["other_arrivals_offsets"]
    ]
    gate_occupancy = {
        gate: [(naive_arrival_minutes + start, naive_arrival_minutes + end) for start, end in windows]
        for gate, windows in spec["gate_occupancy_offsets"].items()
    }
    derived = dict(spec["derived"])
    derived["ground_crew_shift_end_minutes"] = naive_arrival_minutes + derived.pop("ground_crew_shift_end_offset")

    return {
        "scenario_id": scenario_id,
        "flight": dict(flight),
        "derived": derived,
        "other_arrivals": other_arrivals,
        "gate_occupancy": gate_occupancy,
        "constraints": dict(CONSTRAINTS),
        "naive_arrival_minutes": naive_arrival_minutes,
        "naive_arrival": naive_arrival,
    }


def minutes_since_midnight(hhmm: str) -> int:
    return _minutes_since_midnight(hhmm)


def hhmm(minutes: int) -> str:
    return _hhmm(minutes)
