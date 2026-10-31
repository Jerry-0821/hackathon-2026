"""Reference tables that ground the synthetic fields in real-world sources.

The DOT dataset has no aircraft type, fuel, gate or runway data. We fill those gaps
from the tables below. Values are rounded public figures, simplified for a demo.
They are labelled SYNTHETIC in the UI and must not be used for real operations. Operational
limits are NOT here: they come from the challenge workbook (workbook.py).
"""

from __future__ import annotations

# Aircraft types. Wake category is ICAO (M = medium, H = heavy); the separation rules use the FAA
# category in workbook.FAA_CATEGORY. Seats are typical 2015 US configs.
AIRCRAFT = {
    "CRJ9": {"name": "Bombardier CRJ900", "wake": "M", "seats": 76, "widebody": False},
    "E175": {"name": "Embraer 175", "wake": "M", "seats": 76, "widebody": False},
    "B737": {"name": "Boeing 737-700", "wake": "M", "seats": 143, "widebody": False},
    "B738": {"name": "Boeing 737-800", "wake": "M", "seats": 166, "widebody": False},
    "A320": {"name": "Airbus A320", "wake": "M", "seats": 150, "widebody": False},
    "A321": {"name": "Airbus A321", "wake": "M", "seats": 185, "widebody": False},
    "B752": {"name": "Boeing 757-200", "wake": "M", "seats": 180, "widebody": False},
    "B763": {"name": "Boeing 767-300", "wake": "H", "seats": 211, "widebody": True},
    "B772": {"name": "Boeing 777-200", "wake": "H", "seats": 273, "widebody": True},
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

# Not in the challenge workbook: our own simplified values (stated on the Constraints page).
TAXI_IN_MIN = 8                 # landing to gate
MIN_CONNECT_MIN = 45            # minimum connection time, gate-in to onward departure

# Every other limit (wake separation, runway capacity, turnaround, fuel, gates, crew rules)
# comes from the challenge workbook - see workbook.py.

# Hub airports used for cascade events. arrival_runways are real runway designators, used in
# the NOTAM text; runway counts and capacity come from the workbook. utc_offset is standard
# time; DST is applied in utc_offset_for().
AIRPORTS = {
    "ATL": {"hub_airline": "DL", "concourse": "B", "utc_offset": -5,
            "arrival_runways": ["08L/26R", "09R/27L"]},
    "DFW": {"hub_airline": "AA", "concourse": "C", "utc_offset": -6,
            "arrival_runways": ["17C/35C", "13R/31L"]},
    "ORD": {"hub_airline": "UA", "concourse": "C", "utc_offset": -6,
            "arrival_runways": ["10L/28R", "09R/27L"]},
    "DEN": {"hub_airline": "UA", "concourse": "B", "utc_offset": -7,
            "arrival_runways": ["16L/34R", "16R/34L"]},
    "IAH": {"hub_airline": "UA", "concourse": "E", "utc_offset": -6,
            "arrival_runways": ["08L/26R", "09/27"]},
    "MSP": {"hub_airline": "DL", "concourse": "G", "utc_offset": -6,
            "arrival_runways": ["12L/30R", "12R/30L"]},
    "DTW": {"hub_airline": "DL", "concourse": "A", "utc_offset": -5,
            "arrival_runways": ["03R/21L", "04L/22R"]},
    "CLT": {"hub_airline": "US", "concourse": "D", "utc_offset": -5,
            "arrival_runways": ["18C/36C", "18R/36L"]},
    "MIA": {"hub_airline": "AA", "concourse": "D", "utc_offset": -5,
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
