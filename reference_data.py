"""Reference tables that ground the synthetic fields in real-world sources.

The DOT dataset has no aircraft type, fuel, gate or runway data. We fill those gaps
from the tables below. Values are rounded public figures, simplified for a demo.
They are labelled SYNTHETIC in the UI and must not be used for real operations.
"""

from __future__ import annotations

# Aircraft types. Wake category is ICAO (M = medium, H = heavy).
# burn_kg_min is an approximate holding fuel burn. Seats are typical 2015 US configs.
AIRCRAFT = {
    "CRJ9": {"name": "Bombardier CRJ900", "wake": "M", "seats": 76, "burn_kg_min": 26, "widebody": False},
    "E175": {"name": "Embraer 175", "wake": "M", "seats": 76, "burn_kg_min": 28, "widebody": False},
    "B737": {"name": "Boeing 737-700", "wake": "M", "seats": 143, "burn_kg_min": 38, "widebody": False},
    "B738": {"name": "Boeing 737-800", "wake": "M", "seats": 166, "burn_kg_min": 42, "widebody": False},
    "A320": {"name": "Airbus A320", "wake": "M", "seats": 150, "burn_kg_min": 40, "widebody": False},
    "A321": {"name": "Airbus A321", "wake": "M", "seats": 185, "burn_kg_min": 46, "widebody": False},
    "B752": {"name": "Boeing 757-200", "wake": "M", "seats": 180, "burn_kg_min": 55, "widebody": False},
    "B763": {"name": "Boeing 767-300", "wake": "H", "seats": 211, "burn_kg_min": 80, "widebody": True},
    "B772": {"name": "Boeing 777-200", "wake": "H", "seats": 273, "burn_kg_min": 115, "widebody": True},
}

# Aircraft type is inferred from airline + flight distance (stage length), because the
# DOT data has tail numbers but no type. List of (max_distance_miles, type).
FLEET_BY_AIRLINE = {
    "DL": [(500, "CRJ9"), (1400, "B738"), (2200, "B752"), (99999, "B763")],
    "AA": [(500, "E175"), (1300, "A320"), (2000, "A321"), (99999, "B772")],
    "UA": [(500, "E175"), (1700, "B738"), (99999, "B772")],
    "US": [(500, "E175"), (1500, "A320"), (99999, "A321")],
}
DEFAULT_FLEET = [(500, "E175"), (99999, "A320")]

# Minimum time between two landings on the same runway, by wake category of
# (leader, follower). Simplified from ICAO distance minima at ~150 kt approach speed.
WAKE_SEPARATION_MIN = {("H", "H"): 2, ("H", "M"): 3, ("M", "H"): 2, ("M", "M"): 2}

# Ops constants (simplified).
FINAL_RESERVE_MIN = 30          # fuel a jet must still have on landing (ICAO final reserve)
TAXI_IN_MIN = 8                 # landing to gate
MIN_CONNECT_MIN = 45            # minimum connection time, gate-in to onward departure
TURNAROUND_MIN = {False: 40, True: 60}   # gate occupancy after arrival: narrowbody / widebody
NORMAL_SPACING_MIN = 2          # arrival spacing for this bank with all runways open
CLOSED_SPACING_MIN = {"VMC": 3, "IMC": 4}  # spacing when one arrival runway is closed

# Hub airports used for cascade events. gates_total is an approximate 2015 public figure.
# arrival_runways are real runway designators; the model treats two of them as the
# normal arrival pair. utc_offset is standard time; DST is applied in utc_offset_for().
AIRPORTS = {
    "ATL": {"hub_airline": "DL", "gates_total": 192, "concourse": "B", "utc_offset": -5,
            "arrival_runways": ["08L/26R", "09R/27L"]},
    "DFW": {"hub_airline": "AA", "gates_total": 165, "concourse": "C", "utc_offset": -6,
            "arrival_runways": ["17C/35C", "13R/31L"]},
    "ORD": {"hub_airline": "UA", "gates_total": 189, "concourse": "C", "utc_offset": -6,
            "arrival_runways": ["10L/28R", "09R/27L"]},
    "DEN": {"hub_airline": "UA", "gates_total": 111, "concourse": "B", "utc_offset": -7,
            "arrival_runways": ["16L/34R", "16R/34L"]},
    "IAH": {"hub_airline": "UA", "gates_total": 130, "concourse": "E", "utc_offset": -6,
            "arrival_runways": ["08L/26R", "09/27"]},
    "MSP": {"hub_airline": "DL", "gates_total": 131, "concourse": "G", "utc_offset": -6,
            "arrival_runways": ["12L/30R", "12R/30L"]},
    "DTW": {"hub_airline": "DL", "gates_total": 129, "concourse": "A", "utc_offset": -5,
            "arrival_runways": ["03R/21L", "04L/22R"]},
    "CLT": {"hub_airline": "US", "gates_total": 115, "concourse": "D", "utc_offset": -5,
            "arrival_runways": ["18C/36C", "18R/36L"]},
    "MIA": {"hub_airline": "AA", "gates_total": 131, "concourse": "D", "utc_offset": -5,
            "arrival_runways": ["08R/26L", "09/27"]},
}


def aircraft_type_for(airline: str, distance: float) -> str:
    for max_distance, ac_type in FLEET_BY_AIRLINE.get(airline, DEFAULT_FLEET):
        if distance <= max_distance:
            return ac_type
    return DEFAULT_FLEET[-1][1]


def utc_offset_for(airport: str, month: int, day: int) -> int:
    """Local-to-UTC offset in hours. US DST in 2015 ran 8 March to 1 November."""
    offset = AIRPORTS[airport]["utc_offset"]
    in_dst = (month, day) >= (3, 8) and (month, day) < (11, 1)
    return offset + 1 if in_dst else offset
