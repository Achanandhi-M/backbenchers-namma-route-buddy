"""
routing_google.py – Google Maps Routes API v2 integration.

Calls POST https://routes.googleapis.com/directions/v2:computeRoutes
and maps the response to our internal leg format.
Falls back to engine.py on any failure.
"""

from __future__ import annotations
import os, math, time
import requests
from typing import Optional

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.travelAdvisory.transitFare,"
    "routes.legs.steps.travelMode,"
    "routes.legs.steps.staticDuration,"
    "routes.legs.steps.distanceMeters,"
    "routes.legs.steps.navigationInstruction,"
    "routes.legs.steps.transitDetails"
)

# Language code map: BCP-47 UI lang → IETF for Google
_LANG_MAP = {
    "en-IN": "en",
    "kn-IN": "kn",
    "hi-IN": "hi",
    "ta-IN": "ta",
}

# ── In-memory cache ───────────────────────────────────────────────────────────
_cache: dict[tuple, tuple[float, dict]] = {}   # key → (expires_ts, payload)
CACHE_TTL = 600   # 10 minutes


def _cache_get(key: tuple) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def _cache_set(key: tuple, value: dict) -> None:
    _cache[key] = (time.time() + CACHE_TTL, value)


# ── Fare estimation ───────────────────────────────────────────────────────────
def _auto_fare(distance_m: int) -> int:
    """Approximate Bengaluru auto fare: ₹30 flag-fall + ₹15/km after 2 km."""
    km = distance_m / 1000
    if km <= 2:
        return 30
    return int(30 + (km - 2) * 15)


# ── Step mapping ──────────────────────────────────────────────────────────────
_VEHICLE_MODE_MAP = {
    "SUBWAY": "metro",
    "METRO_RAIL": "metro",
    "RAIL": "train",
    "BUS": "bus",
    "INTERCITY_BUS": "bus",
    "TROLLEYBUS": "bus",
    "SHARE_TAXI": "auto",
    "FERRY": "train",
    "CABLE_CAR": "metro",
    "GONDOLA_LIFT": "metro",
    "FUNICULAR": "metro",
    "OTHER": "bus",
}


def _parse_duration(dur_str: str) -> float:
    """Parse Google duration string like '1234s' → minutes."""
    if not dur_str:
        return 0.0
    return round(int(dur_str.rstrip("s")) / 60, 1)


def _map_steps(raw_steps: list) -> list[dict]:
    """Map Google route steps to our internal leg format. Merges consecutive walk steps."""
    legs = []
    pending_walk: Optional[dict] = None

    for step in raw_steps:
        mode = step.get("travelMode", "WALK")
        dur_min = _parse_duration(step.get("staticDuration", "0s"))
        dist_m = step.get("distanceMeters", 0)
        nav = step.get("navigationInstruction", {})
        instruction = nav.get("instructions", "")

        if mode == "WALK":
            if pending_walk:
                pending_walk["time_min"]  = round(pending_walk["time_min"] + dur_min, 1)
                pending_walk["walk_km"]   = round(pending_walk["walk_km"] + dist_m / 1000, 2)
                pending_walk["instruction"] += (" " + instruction).strip()
            else:
                pending_walk = {
                    "type":        "walk",
                    "from":        "",
                    "to":          "",
                    "walk_km":     round(dist_m / 1000, 2),
                    "time_min":    dur_min,
                    "fare_inr":    0,
                    "instruction": instruction,
                    "line":        "",
                    "direction":   "",
                    "stops":       0,
                    "is_interchange": False,
                    "interchange_at": "",
                }
            continue

        # flush pending walk
        if pending_walk:
            legs.append(pending_walk)
            pending_walk = None

        td = step.get("transitDetails", {})
        line_info = td.get("transitLine", {})
        vehicle   = line_info.get("vehicle", {})
        v_type    = vehicle.get("type", "BUS")
        leg_mode  = _VEHICLE_MODE_MAP.get(v_type, "bus")

        line_name = (
            line_info.get("nameShort")
            or line_info.get("name")
            or ""
        )
        # For Namma Metro lines derive color
        line_color = _metro_color(line_name)

        dep_stop = td.get("departureStop", {}).get("name", "")
        arr_stop = td.get("arrivalStop",   {}).get("name", "")
        headsign = td.get("headsign", "")
        stop_count = td.get("stopCount", 0)

        fare_inr = 0  # fare comes from route-level, not step-level

        legs.append({
            "type":           leg_mode,
            "line":           _normalise_metro_line(line_name),
            "color":          line_color,
            "from":           dep_stop,
            "to":             arr_stop,
            "direction":      f"towards {headsign}" if headsign else "",
            "stops":          stop_count,
            "time_min":       dur_min,
            "fare_inr":       fare_inr,
            "instruction":    instruction,
            "line_display":   line_name,
            "is_interchange": False,
            "interchange_at": "",
            "walk_km":        0,
        })

    if pending_walk:
        legs.append(pending_walk)

    return legs


def _normalise_metro_line(name: str) -> str:
    """Map Google transit line names to our line key."""
    n = name.lower()
    if "purple" in n:
        return "PurpleEast"
    if "green" in n:
        return "Green"
    if "yellow" in n:
        return "Yellow"
    return name


def _metro_color(name: str) -> str:
    n = name.lower()
    if "purple" in n: return "#8a3ffc"
    if "green"  in n: return "#198038"
    if "yellow" in n: return "#f1c21b"
    if not name:      return "#8d8d8d"
    return "#198038"   # default bus green


def _count_transfers(legs: list[dict]) -> int:
    transit = [l for l in legs if l["type"] in ("metro", "bus", "train")]
    return max(0, len(transit) - 1)


# ── Build option variants from multiple routes ────────────────────────────────
def _build_options(raw_routes: list, total_dist_m: int) -> list[dict]:
    """
    Map up to 3 Google routes to labelled option dicts.
    Also always append a Direct Auto option.
    """
    options = []

    for i, route in enumerate(raw_routes[:3]):
        raw_steps = []
        for leg in route.get("legs", []):
            raw_steps.extend(leg.get("steps", []))

        legs = _map_steps(raw_steps)
        dur_s = int(route.get("duration", "0s").rstrip("s"))
        dur_min = round(dur_s / 60, 1)
        dist_m = route.get("distanceMeters", total_dist_m)

        # fare from travelAdvisory (optional)
        fare_struct = route.get("travelAdvisory", {}).get("transitFare", {})
        fare_inr = 0
        if fare_struct:
            units = int(fare_struct.get("units", 0))
            nanos = int(fare_struct.get("nanos", 0))
            fare_inr = units + round(nanos / 1e9)

        # distribute fare across transit legs
        transit_legs = [l for l in legs if l["type"] in ("metro", "bus", "train")]
        if fare_inr and transit_legs:
            per_leg = fare_inr // len(transit_legs)
            for l in transit_legs:
                l["fare_inr"] = per_leg

        transfers = _count_transfers(legs)
        options.append({
            "label":            "",   # filled below
            "total_time_min":   dur_min,
            "total_fare_inr":   fare_inr,
            "interchange_count": transfers,
            "legs":             legs,
            "distance_m":       dist_m,
        })

    if not options:
        return options

    # label by ranking
    by_time     = sorted(options, key=lambda o: o["total_time_min"])
    by_fare     = sorted(options, key=lambda o: (o["total_fare_inr"], len([l for l in o["legs"] if l["type"]=="metro"])))
    by_transfer = sorted(options, key=lambda o: o["interchange_count"])

    used_labels: set[int] = set()

    def assign(opt_list, label):
        for opt in opt_list:
            idx = options.index(opt)
            if idx not in used_labels:
                options[idx]["label"] = label
                used_labels.add(idx)
                break

    assign(by_time,     "Fastest")
    assign(by_fare,     "Cheapest")
    assign(by_transfer, "Fewest transfers")

    # fill any unlabelled
    for i, opt in enumerate(options):
        if not opt["label"]:
            opt["label"] = f"Option {i+1}"

    # Direct Auto option
    best_dist = options[0]["distance_m"] if options else total_dist_m
    auto_fare = _auto_fare(best_dist)
    auto_time = round(best_dist / 1000 * 3.5, 1)  # rough 17 km/h city speed
    options.append({
        "label":            "Direct Auto",
        "total_time_min":   auto_time,
        "total_fare_inr":   auto_fare,
        "interchange_count": 0,
        "legs": [{
            "type":        "auto",
            "from":        "",   # filled by caller
            "to":          "",
            "walk_km":     round(best_dist / 1000, 1),
            "time_min":    auto_time,
            "fare_inr":    auto_fare,
            "instruction": f"Take an auto-rickshaw directly. Approx Bengaluru meter fare ₹{auto_fare} (approx).",
            "line":        "",
            "direction":   "",
            "stops":       0,
            "is_interchange": False,
            "interchange_at": "",
        }],
        "distance_m":       best_dist,
        "is_auto_estimate": True,
    })

    # For short trips (< 4 km) move Auto/Bus options first
    if best_dist < 4000:
        options.sort(key=lambda o: (
            0 if o["label"] == "Direct Auto" else
            1 if any(l["type"] == "bus" for l in o["legs"]) else 2
        ))

    return options


# ── Main public function ──────────────────────────────────────────────────────
def get_google_routes(
    from_place: str,
    to_place: str,
    lang: str = "en-IN",
) -> Optional[dict]:
    """
    Returns a dict with keys: options, source="google", origin, destination.
    Returns None on failure (caller should fall back to engine.py).
    """
    if not GOOGLE_MAPS_API_KEY:
        return None

    cache_key = (from_place.lower(), to_place.lower(), lang)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    google_lang = _LANG_MAP.get(lang, "en")

    body = {
        "origin":      {"address": f"{from_place}, Bengaluru, Karnataka"},
        "destination": {"address": f"{to_place}, Bengaluru, Karnataka"},
        "travelMode":  "TRANSIT",
        "computeAlternativeRoutes": True,
        "regionCode":  "IN",
        "languageCode": google_lang,
        "transitPreferences": {
            "routingPreference": "LESS_WALKING"
        },
    }

    headers = {
        "Content-Type":    "application/json",
        "X-Goog-Api-Key":  GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    try:
        resp = requests.post(ROUTES_API_URL, json=body, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None

    raw_routes = data.get("routes", [])
    if not raw_routes:
        return None

    # estimate total distance from first route
    total_dist_m = raw_routes[0].get("distanceMeters", 10000)

    options = _build_options(raw_routes, total_dist_m)
    if not options:
        return None

    # fill auto leg from/to
    for opt in options:
        for leg in opt["legs"]:
            if leg["type"] == "auto":
                if not leg["from"]: leg["from"] = from_place
                if not leg["to"]:   leg["to"]   = to_place

    result = {
        "source":      "google",
        "origin":      from_place,
        "destination": to_place,
        "options":     options,
        "fallback_note": None,
    }
    _cache_set(cache_key, result)
    return result
