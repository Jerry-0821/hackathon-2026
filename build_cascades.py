"""Build runway-closure cascade events from the real 2015 DOT flights data.

Run once: `python build_cascades.py` (needs data/flights.csv and pandas).
Writes events/cascades.json, which is committed, so the app does not need the raw data.

What is REAL (from the DOT data): the arriving flights, their schedules and delays, the
tail numbers, each aircraft's next departure, and the same airline's onward departures
used for passenger connections and gate occupancy.

What is SYNTHETIC (derived deterministically, labelled in the UI): aircraft type (from
airline + distance), fuel above final reserve, passenger counts per connection, gate
IDs, the runway closure, the weather report and the ops note.
"""

from __future__ import annotations

import json
import random
import zlib
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

import reference_data as ref

OUT_PATH = Path("events/cascades.json")
NUM_EVENTS = 40
MAX_PER_AIRPORT = 6
WINDOW_MIN = 20
GATES_PER_PIER = 8


def h(*parts) -> int:
    """Stable hash, so every rebuild gives the same events."""
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def hhmm(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def to_minutes(hhmm_int) -> int:
    v = int(hhmm_int)
    return (v // 100) * 60 + v % 100


def load_flights() -> pd.DataFrame:
    cols = [
        "MONTH", "DAY", "AIRLINE", "FLIGHT_NUMBER", "TAIL_NUMBER", "ORIGIN_AIRPORT",
        "DESTINATION_AIRPORT", "SCHEDULED_DEPARTURE", "DEPARTURE_DELAY", "SCHEDULED_ARRIVAL",
        "ARRIVAL_DELAY", "DISTANCE", "AIR_TIME", "SCHEDULED_TIME", "CANCELLED", "DIVERTED",
        "WEATHER_DELAY", "AIR_SYSTEM_DELAY",
    ]
    df = pd.read_csv(
        "data/flights.csv",
        usecols=cols,
        dtype={"ORIGIN_AIRPORT": str, "DESTINATION_AIRPORT": str, "TAIL_NUMBER": str},
    )
    hubs = set(ref.AIRPORTS)
    df = df[df["ORIGIN_AIRPORT"].isin(hubs) | df["DESTINATION_AIRPORT"].isin(hubs)]
    df = df[(df["CANCELLED"] == 0) & (df["DIVERTED"] == 0)].copy()
    df["sched_dep_min"] = df["SCHEDULED_DEPARTURE"].map(to_minutes)
    df["sched_arr_min"] = df["SCHEDULED_ARRIVAL"].map(to_minutes)
    for c in ["DEPARTURE_DELAY", "ARRIVAL_DELAY", "WEATHER_DELAY", "AIR_SYSTEM_DELAY", "AIR_TIME"]:
        df[c] = df[c].fillna(0)
    return df


def find_candidate_windows(df: pd.DataFrame) -> list[dict]:
    """Hub-carrier arrival banks where at least 2 flights had real weather/NAS delays."""
    candidates = []
    for airport, info in ref.AIRPORTS.items():
        arr = df[(df["DESTINATION_AIRPORT"] == airport) & (df["AIRLINE"] == info["hub_airline"])].copy()
        arr["eta_min"] = arr["sched_arr_min"] + arr["DEPARTURE_DELAY"].clip(lower=0).astype(int)
        arr = arr[(arr["eta_min"] >= 9 * 60) & (arr["eta_min"] <= 19 * 60)]
        arr["bucket"] = arr["eta_min"] // WINDOW_MIN
        arr["disrupted"] = (arr["WEATHER_DELAY"] > 0) | (arr["AIR_SYSTEM_DELAY"] > 0)
        for (month, day, bucket), g in arr.groupby(["MONTH", "DAY", "bucket"]):
            n = 6 + h(airport, month, day, bucket) % 3
            if len(g) >= n and g["disrupted"].sum() >= 2:
                candidates.append({"airport": airport, "month": int(month), "day": int(day),
                                   "bucket": int(bucket), "n": n})
    return candidates


def pick_windows(candidates: list[dict]) -> list[dict]:
    rng = random.Random(2026)
    rng.shuffle(candidates)
    picked, per_airport = [], {}
    for c in candidates:
        if per_airport.get(c["airport"], 0) >= MAX_PER_AIRPORT:
            continue
        per_airport[c["airport"]] = per_airport.get(c["airport"], 0) + 1
        picked.append(c)
        if len(picked) == NUM_EVENTS:
            break
    return picked


def gate_free(busy: dict, gate: str, start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e, _ in busy[gate])


def build_event(df: pd.DataFrame, c: dict) -> dict:
    airport, month, day = c["airport"], c["month"], c["day"]
    info = ref.AIRPORTS[airport]
    airline = info["hub_airline"]
    seed = h(airport, month, day, c["bucket"])
    day_df = df[(df["MONTH"] == month) & (df["DAY"] == day) & (df["AIRLINE"] == airline)]

    arr = day_df[day_df["DESTINATION_AIRPORT"] == airport].copy()
    arr["eta_min"] = arr["sched_arr_min"] + arr["DEPARTURE_DELAY"].clip(lower=0).astype(int)
    arr = arr[arr["eta_min"] // WINDOW_MIN == c["bucket"]].sort_values("eta_min").head(c["n"])
    window_start = int(arr["eta_min"].min())

    deps = day_df[day_df["ORIGIN_AIRPORT"] == airport].sort_values("sched_dep_min")

    # Gate pier: synthetic gate IDs, last two gates can take widebodies.
    first_num = 10 + seed % 20
    gates = [{"id": f"{info['concourse']}{first_num + i}", "widebody": i >= GATES_PER_PIER - 2}
             for i in range(GATES_PER_PIER)]
    busy = {g["id"]: [] for g in gates}

    # Gate occupancy from REAL same-airline departures (about 1 in 3 is parked on this pier).
    for _, d in deps[(deps["sched_dep_min"] >= window_start - 20) & (deps["sched_dep_min"] <= window_start + 200)].iterrows():
        label = f"{airline} {int(d['FLIGHT_NUMBER'])}"
        if h(label, month, day) % 3 != 0:
            continue
        dep = int(d["sched_dep_min"])
        for g in gates[: GATES_PER_PIER - 2]:
            if gate_free(busy, g["id"], dep - 45, dep):
                busy[g["id"]].append((dep - 45, dep, f"{label} departure"))
                break

    flights = []
    for _, r in arr.iterrows():
        fid = f"{airline} {int(r['FLIGHT_NUMBER'])}"
        fseed = h(fid, month, day)
        ac = ref.aircraft_type_for(airline, float(r["DISTANCE"]))
        spec = ref.AIRCRAFT[ac]
        eta = int(r["eta_min"])
        gate_in = eta + ref.TAXI_IN_MIN

        # Connections: REAL onward departures of the same airline; pax split is synthetic.
        # Picks are spread across the 1-3 hour bank, so some connections are tight and some are not.
        onward = deps[(deps["sched_dep_min"] >= gate_in + ref.MIN_CONNECT_MIN + 5)
                      & (deps["sched_dep_min"] <= eta + 180)]
        onward = onward[onward["DESTINATION_AIRPORT"] != r["ORIGIN_AIRPORT"]]
        share = 0.25 + (fseed % 20) / 100
        total_pax = round(spec["seats"] * 0.82 * share)
        n_on = len(onward)
        positions = sorted({min(n_on - 1, int(n_on * frac) + (fseed >> s) % 3)
                            for frac, s in [(0.0, 3), (0.3, 5), (0.65, 7)]}) if n_on else []
        picks = onward.iloc[positions]
        connections = []
        for (_, o), w in zip(picks.iterrows(), [0.5, 0.3, 0.2]):
            connections.append({
                "flight": f"{airline} {int(o['FLIGHT_NUMBER'])}",
                "destination": o["DESTINATION_AIRPORT"],
                "departure": hhmm(int(o["sched_dep_min"])),
                "departure_min": int(o["sched_dep_min"]),
                "pax": max(1, round(total_pax * w)),
            })

        # Next departure of the SAME aircraft (tail number) - real downstream dependency.
        nxt = deps[(deps["TAIL_NUMBER"] == r["TAIL_NUMBER"]) & (deps["sched_dep_min"] > eta)].head(1)
        next_departure = None
        if len(nxt):
            n0 = nxt.iloc[0]
            next_departure = {
                "flight": f"{airline} {int(n0['FLIGHT_NUMBER'])}",
                "destination": n0["DESTINATION_AIRPORT"],
                "departure": hhmm(int(n0["sched_dep_min"])),
                "departure_min": int(n0["sched_dep_min"]),
            }

        # Preferred (planned) gate: first compatible gate free at the planned gate-in time.
        turn = ref.TURNAROUND_MIN[spec["widebody"]]
        compatible = [g["id"] for g in gates if g["widebody"] or not spec["widebody"]]
        start = fseed % len(compatible)
        ordered = compatible[start:] + compatible[:start]
        preferred = next((g for g in ordered if gate_free(busy, g, gate_in, gate_in + turn)), None)
        if preferred is None:
            preferred = min(ordered, key=lambda g: max([e for _, e, _ in busy[g]] or [0]))
        busy[preferred].append((gate_in, gate_in + turn, f"{fid} planned arrival"))

        flights.append({
            "id": fid,
            "airline": airline,
            "flight_number": int(r["FLIGHT_NUMBER"]),
            "tail": r["TAIL_NUMBER"],
            "origin": r["ORIGIN_AIRPORT"],
            "distance": int(r["DISTANCE"]),
            "scheduled_arrival": hhmm(int(r["sched_arr_min"])),
            "eta": hhmm(eta),
            "eta_min": eta,
            "departure_delay": int(r["DEPARTURE_DELAY"]),
            "real_arrival_delay": int(r["ARRIVAL_DELAY"]),
            "real_weather_delay": int(r["WEATHER_DELAY"]),
            "real_nas_delay": int(r["AIR_SYSTEM_DELAY"]),
            "aircraft_type": ac,
            "aircraft_name": spec["name"],
            "wake": spec["wake"],
            "seats": spec["seats"],
            "widebody": spec["widebody"],
            "burn_kg_min": spec["burn_kg_min"],
            "extra_fuel_min": 8 + round(0.03 * float(r["AIR_TIME"])) + fseed % 14,
            "preferred_gate": preferred,
            "connections": connections,
            "next_departure": next_departure,
        })

    # Remove the planned-arrival entries; the solver assigns arrivals itself.
    gate_busy = {g: [[s, e, label] for s, e, label in w if "planned arrival" not in label] for g, w in busy.items()}

    disruption = build_disruption(airport, month, day, window_start, gates, arr, seed)
    texts = build_texts(airport, month, day, disruption, seed)
    n_heavy = sum(f["wake"] == "H" for f in flights)

    return {
        "id": f"{airport}-2015-{month:02d}-{day:02d}-{hhmm(window_start).replace(':', '')}",
        "label": (f"{airport} 2015-{month:02d}-{day:02d} {hhmm(window_start)} - {len(flights)} {airline} arrivals, "
                  f"RWY {disruption['runway_closures'][0]['runway']} closed, {disruption['flight_rules']}"),
        "airport": airport,
        "airline": airline,
        "date": f"2015-{month:02d}-{day:02d}",
        "utc_offset": ref.utc_offset_for(airport, month, day),
        "window_start": hhmm(window_start),
        "window_start_min": window_start,
        "gates_total_airport": info["gates_total"],
        "arrival_runways": info["arrival_runways"],
        "gates": gates,
        "gate_busy": gate_busy,
        "crew_shift_end_min": window_start + 70 + seed % 25,
        "flights": flights,
        "num_heavy": n_heavy,
        "disruption_truth": disruption,
        "texts": texts,
    }


def build_disruption(airport, month, day, window_start, gates, arr, seed) -> dict:
    info = ref.AIRPORTS[airport]
    closed = info["arrival_runways"][seed % 2]
    start = window_start - 10 + seed % 8
    end = start + 35 + seed % 25
    real_weather = bool((arr["WEATHER_DELAY"] > 0).any())
    if real_weather:
        rules, ceiling, vis = "IMC", 600 + 100 * (seed % 4), [1.0, 1.5, 2.0, 2.5][seed % 4]
    else:
        rules, ceiling, vis = "VMC", None, 10.0
    narrow_gates = [g["id"] for g in gates if not g["widebody"]]
    oos_gate = narrow_gates[(seed // 7) % len(narrow_gates)]
    reason = ["hydraulic leak on the jet bridge", "fuel hydrant fault", "ground power unit failure"][seed % 3]
    return {
        "runway_closures": [{"runway": closed, "start_min": start, "end_min": end}],
        "gates_out_of_service": [{"gate": oos_gate, "reason": reason}],
        "ceiling_ft": ceiling,
        "visibility_sm": vis,
        "flight_rules": rules,
        "real_weather_delay_in_window": real_weather,
    }


def build_texts(airport, month, day, d, seed) -> dict:
    off = ref.utc_offset_for(airport, month, day)
    closure = d["runway_closures"][0]
    midnight_utc = datetime(2015, month, day) - timedelta(hours=off)

    def utc(local_min: int) -> datetime:
        return midnight_utc + timedelta(minutes=local_min)

    notam = (f"!{airport} {month:02d}/{100 + seed % 400:03d} {airport} RWY {closure['runway']} CLSD "
             f"{utc(closure['start_min']):%y%m%d%H%M}-{utc(closure['end_min']):%y%m%d%H%M}")
    obs = utc(closure["start_min"] - 8)
    day, obs_z = obs.day, f"{obs:%H%M}"
    if d["flight_rules"] == "IMC":
        wx = ["+TSRA BR", "-RA BR", "TSRA", "RA BR"][seed % 4]
        vis = d["visibility_sm"]
        vis_txt = f"{int(vis)}SM" if vis == int(vis) else f"{int(vis)} 1/2SM" if vis > 1 else "1SM"
        ceil = d["ceiling_ft"] // 100
        metar = (f"K{airport} {day:02d}{obs_z}Z {(seed % 36) * 10:03d}{12 + seed % 10}G{24 + seed % 8}KT "
                 f"{vis_txt} {wx} BKN{ceil:03d} OVC{ceil + 7:03d} {14 + seed % 12:02d}/{12 + seed % 10:02d} A29{70 + seed % 25} RMK AO2")
    else:
        metar = f"K{airport} {day:02d}{obs_z}Z {(seed % 36) * 10:03d}08KT 10SM FEW045 SCT250 24/11 A3005 RMK AO2"
    gate = d["gates_out_of_service"][0]
    end_local = hhmm(closure["end_min"])
    ops_note = [
        f"ramp ctl - heads up, {gate['reason']} at gate {gate['gate']}. mx says 2+ hrs, nothing parks at "
        f"{gate['gate']} until further notice. tower says the closed rwy should be back around {end_local} local.",
        f"Ops update: gate {gate['gate']} is down ({gate['reason']}), do not assign. Also FYI crew B is short "
        "2 people today so expect slower turns.",
        f"{gate['gate']} out of service - {gate['reason']}. Stores truck is stuck behind it too. Will advise.",
    ][(seed // 5) % 3]
    return {"notam": notam, "metar": metar, "ops_note": ops_note}


def main() -> None:
    print("Loading data/flights.csv (takes a minute)...")
    df = load_flights()
    candidates = find_candidate_windows(df)
    print(f"{len(candidates)} candidate disrupted arrival banks found")
    events = [build_event(df, c) for c in pick_windows(candidates)]
    events.sort(key=lambda e: (e["airport"], e["date"], e["window_start"]))

    # Demo picks: events with a heavy aircraft, IMC weather and a fuel-tight flight, one per airport.
    demo_airports = set()
    for e in sorted(events, key=lambda e: (e["disruption_truth"]["flight_rules"] != "IMC", -e["num_heavy"], -len(e["flights"]))):
        if e["airport"] not in demo_airports and len(demo_airports) < 3:
            e["demo"] = True
            demo_airports.add(e["airport"])

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "source": "US DOT 2015 Flight Delays and Cancellations (flights.csv). Synthetic fields labelled.",
        "events": events,
    }, indent=1), encoding="utf-8")
    print(f"Wrote {len(events)} events to {OUT_PATH} ({OUT_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
