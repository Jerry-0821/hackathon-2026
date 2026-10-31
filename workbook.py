"""The challenge's operational constraints workbook: one place that reads it.

The organisers' file stays local and is git-ignored (the repo is public). Put it in reference/
(either file name below). When it is present it is read directly; run `python workbook.py` (or
`python build_cascades.py`) to refresh reference/constraints_extract.json, a small committed extract
with only the values the app uses, so a fresh clone still runs.

Every limit the cascade uses comes from here. Where the workbook leaves something open, or
where we add or change a limit, the choice is listed in STATED_CHANGES - the rules of play
say "Any limit you change must be stated in your demo", and the Constraints page shows them.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).parent
XLSX_CANDIDATES = [ROOT / "reference" / "challenge-2-operational-constraints.xlsx",
                   ROOT / "reference" / "challenge2_operational_constraints.xlsx",
                   ROOT / "challenge2_operational_constraints.xlsx"]
EXTRACT = ROOT / "reference" / "constraints_extract.json"

# --- Our assumptions where the workbook leaves a gap (all shown in the demo) -------------------
APPROACH_SPEED_KT = 140          # converts wake separation in NM into time on final approach
SPARE_FUEL_INCLUDES_RESERVE = True  # "45 min spare fuel on arrival" read as total, incl. the 30 min reserve

# FAA wake category per aircraft type (FAA JO 7110.65). The B757 is its own FAA category; the
# workbook does not list it, so it falls under "any other pair".
FAA_CATEGORY = {"CRJ9": "Large", "E175": "Large", "B737": "Large", "B738": "Large", "A320": "Large",
                "A321": "Large", "B752": "B757", "B763": "Heavy", "B772": "Heavy"}


def _number(text, label: str) -> float:
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not match:
        raise ValueError(f"workbook: could not read a number for '{label}' from {text!r}")
    return float(match.group(0).replace(",", ""))


def xlsx_path() -> Path | None:
    return next((p for p in XLSX_CANDIDATES if p.exists()), None)


@lru_cache(maxsize=1)
def load() -> dict:
    """The workbook's values: from the .xlsx if it is here, otherwise from the committed extract."""
    path = xlsx_path()
    if path:
        return _parse_xlsx(path)
    if not EXTRACT.exists():
        raise FileNotFoundError("No constraints workbook found. Put challenge-2-operational-constraints.xlsx "
                                "in reference/ and run `python workbook.py`.")
    raw = json.loads(EXTRACT.read_text(encoding="utf-8"))
    return {**raw, "wake_nm": {(a, b): nm for a, b, nm in raw["wake_nm"]}, "source": f"{EXTRACT.name} (extract)"}


def write_extract(codes) -> Path:
    """Save only the values the app uses (these airports, wake, fuel/crew, rules of play)."""
    w = load()
    out = {
        "note": "Extract of the organisers' challenge constraints workbook (synthetic planning limits). "
                "Only the values this app uses. Regenerate with `python workbook.py`.",
        "airports_in_workbook": w["airports_in_workbook"],
        "airports": {c: w["airports"][c] for c in sorted(codes)},
        "wake_nm": [[a, b, nm] for (a, b), nm in w["wake_nm"].items()],
        "wake_default_nm": w["wake_default_nm"],
        "fuel_crew": w["fuel_crew"],
        "rules_of_play": w["rules_of_play"],
    }
    EXTRACT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return EXTRACT


def _parse_xlsx(path: Path) -> dict:
    """Parse the workbook. Raises ValueError with a clear message if its layout changed."""
    import openpyxl  # local import: only needed when the workbook is read

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    def table(sheet: str, first_header: str) -> tuple[list, list]:
        """(header, data rows) of a sheet, found by its first header cell, skipping notes above it."""
        rows = list(wb[sheet].iter_rows(values_only=True))
        for i, r in enumerate(rows):
            if r and str(r[0]).strip() == first_header:
                return list(r), [x for x in rows[i + 1:] if x and x[0] is not None]
        raise ValueError(f"workbook: sheet '{sheet}' has no header row starting with '{first_header}'")

    header, data = table("airport_capacity", "IATA_CODE")
    col = {name: header.index(name) for name in
           ("IATA_CODE", "AIRPORT", "size_class", "active_runways", "gates", "max_runway_ops_per_hour",
            "min_turnaround_min", "spare_gates_at_any_time")}
    airports = {}
    for r in data:
        airports[str(r[col["IATA_CODE"]])] = {
            "name": r[col["AIRPORT"]],
            "size_class": r[col["size_class"]],
            "active_runways": int(r[col["active_runways"]]),
            "gates": int(r[col["gates"]]),
            "max_runway_ops_per_hour": int(r[col["max_runway_ops_per_hour"]]),
            "min_turnaround_min": int(r[col["min_turnaround_min"]]),
            "spare_gates": int(r[col["spare_gates_at_any_time"]]),
        }

    wake, wake_default = {}, None
    for r in table("wake_separation", "leader")[1]:
        leader = str(r[0]).split(" (")[0]  # "Super (A380)" -> "Super"
        if leader.lower().startswith("any other"):
            wake_default = _number(r[2], "wake any other pair")
        else:
            wake[(leader, str(r[1]))] = _number(r[2], f"wake {leader}->{r[1]}")
    if wake_default is None:
        raise ValueError("workbook: wake_separation has no 'Any other pair' row")

    fc = {str(r[0]).strip().lower(): r[1] for r in table("fuel_and_crew", "rule")[1]}

    def rule(prefix: str):
        for k, v in fc.items():
            if k.startswith(prefix):
                return v
        raise ValueError(f"workbook: fuel_and_crew has no rule starting with '{prefix}'")

    shift_text = str(rule("ground crew shift"))
    overtime = re.search(r"(\d+(?:\.\d+)?)\s*x", shift_text)
    fuel_crew = {
        "final_reserve_min": _number(rule("final fuel reserve"), "final reserve"),
        "contingency_pct": _number(rule("contingency fuel"), "contingency"),
        "burn_narrow_kg_h": _number(rule("holding fuel burn (narrowbody)"), "narrowbody burn"),
        "burn_wide_kg_h": _number(rule("holding fuel burn (widebody)"), "widebody burn"),
        "spare_fuel_on_arrival_min": _number(rule("assumed spare fuel"), "spare fuel"),
        "flight_crew_max_duty_h": _number(rule("flight crew max duty"), "crew duty"),
        "ground_shift_h": _number(shift_text, "ground shift"),
        "overtime_rate": float(overtime.group(1)) if overtime else 1.5,
        "gate_change_notice_min": _number(rule("gate change notice"), "gate change notice"),
    }
    rules_of_play = {str(r[0]): str(r[1]) for r in table("rules_of_play", "topic")[1]}
    wb.close()
    return {"airports": airports, "airports_in_workbook": len(airports), "wake_nm": wake,
            "wake_default_nm": wake_default, "fuel_crew": fuel_crew, "rules_of_play": rules_of_play,
            "source": path.name}


# --- Derived values used by the solver --------------------------------------------------------

def airport(code: str) -> dict:
    try:
        return load()["airports"][code]
    except KeyError as e:
        raise KeyError(f"airport {code} is not in the constraints workbook") from e


def wake_nm(leader_type: str, follower_type: str) -> float:
    w = load()
    key = (FAA_CATEGORY[leader_type], FAA_CATEGORY[follower_type])
    return w["wake_nm"].get(key, w["wake_default_nm"])


def wake_minutes(leader_type: str, follower_type: str) -> int:
    """Wake separation as whole minutes on final approach, rounded up (conservative)."""
    return math.ceil(wake_nm(leader_type, follower_type) / (APPROACH_SPEED_KT / 60))


def burn_kg_per_min(widebody: bool) -> float:
    fc = load()["fuel_crew"]
    return (fc["burn_wide_kg_h"] if widebody else fc["burn_narrow_kg_h"]) / 60


def usable_fuel_min(air_time_min: float) -> int:
    """Holding minutes available above final reserve on arrival: spare fuel (minus the final
    reserve if the spare figure includes it) plus the unused contingency (% of trip)."""
    fc = load()["fuel_crew"]
    spare = fc["spare_fuel_on_arrival_min"] - (fc["final_reserve_min"] if SPARE_FUEL_INCLUDES_RESERVE else 0)
    return round(spare + fc["contingency_pct"] / 100 * air_time_min)


def runway_capacity_per_hour(code: str, runways_closed: int) -> float:
    a = airport(code)
    open_runways = max(a["active_runways"] - runways_closed, 0)
    return a["max_runway_ops_per_hour"] * open_runways / a["active_runways"]


def bank_landing_interval_min(code: str, runways_closed: int, demand_ops_per_hour: float,
                              bank_arrivals_per_hour: float) -> int:
    """Minimum time between two landings of the modelled bank. The airport's runway capacity
    (workbook) is shared with all real traffic in that hour (flights.csv): the bank gets its
    fair share, capacity x bank / max(demand, capacity). Rounded up to whole minutes."""
    cap = runway_capacity_per_hour(code, runways_closed)
    if cap <= 0:
        return 10**6  # no runway open
    share_rate = cap * bank_arrivals_per_hour / max(demand_ops_per_hour, cap)
    return math.ceil(60 / share_rate)


STATED_CHANGES = [
    "Wake separation: the workbook gives distances (NM). We convert them to time at a "
    f"{APPROACH_SPEED_KT} kt final approach speed and round up to whole minutes. Aircraft type is mapped "
    "to the FAA category; the B757 is not in the workbook, so it uses 'any other pair'.",
    "Runway capacity: the workbook's max runway ops per hour is shared by all traffic. Demand comes from "
    "the real 2015 schedule in flights.csv for that hour (all airlines, arrivals and departures). The "
    "modelled bank gets its fair share of capacity. A closed runway removes its share of capacity "
    "(e.g. 1 of 4 runways closed leaves 75%). Landing intervals are rounded up to whole minutes.",
    "Weather: the METAR is read and shown, but the workbook has no weather capacity rule, so weather "
    "does not change capacity.",
    "Spare fuel: '45 minutes of holding on arrival' is read as the total holding fuel, including the "
    "30 minute final reserve, so 15 minutes are usable before touching the reserve. The unused "
    "contingency fuel (5% of trip) is added on top." if SPARE_FUEL_INCLUDES_RESERVE else
    "Spare fuel: '45 minutes of holding on arrival' is read as extra fuel above the final reserve.",
    "Turnaround: the workbook's minimum turnaround for the airport applies to every aircraft, "
    "including widebodies, and is how long an arriving aircraft occupies its gate.",
    "Gates: the modelled pier has each flight's planned gate plus the airport's spare gates "
    "(workbook: spare gates at any time). One spare gate can take a widebody (our assumption).",
    "Gate change notice: a gate can only change if the flight lands at least 20 minutes after the "
    "re-plan is issued (the start of the runway closure). Exception: if the planned gate is out of "
    "service, the change is forced.",
    "Flight crew duty (13 h): not modelled. DOT data has no crew records. We tried assuming the crew "
    "stays with the aircraft all day, but then 17 of 272 flights would already break the limit on their "
    "normal schedule, so crews clearly change aircraft and that estimate would be wrong.",
    "Ground crew shift (8 h, overtime at 1.5x): overtime minutes are reported and costed at 1.5x. The "
    "mediated process also uses a team policy of at most 30 minutes overtime (not from the workbook).",
    "Not in the workbook, our own simplified values: taxi-in 8 minutes, minimum connection time 45 minutes, "
    "ATC holding at most 30 minutes and a 5 minute company fuel buffer (mediated process hard rules).",
    "Disruption row: each event contains at least one flights.csv row with WEATHER_DELAY > 15 or "
    "LATE_AIRCRAFT_DELAY > 20 (rules of play). The runway closure, gate fault and messages that extend "
    "that row into a cascade are synthetic.",
]


if __name__ == "__main__":
    import reference_data

    print(f"Read {load()['source']}; wrote {write_extract(reference_data.AIRPORTS)}")
