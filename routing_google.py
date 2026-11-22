"""
routing_google.py – Google Maps Routes API v2 integration (v3).

Improvements over v2:
- Place resolution via Places API (New) Text Search → placeId for accurate routing
- Ambiguity detection: returns suggestions when match is weak
- Local transit filtering (BMTC/Namma Metro); excludes intercity operators
- Accurate transfer count (transit_steps - 1)
- Walk option for < 2 km; Auto/Cab from real DRIVE route
- Proper fare handling: "Fare not available" when absent, BMTC approx marked
- Walk instructions stored as summary + sub-steps list (never concatenated)
- Agency info in field mask and step objects
- departure_time support (RFC 3339); defaults to now
- Route deduplication and multi-label assignment
- Correct vehicle type → mode mapping for Namma Metro
"""

from __future__ import annotations
import os, math, time, re
from datetime import datetime, timezone
import requests
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
DEBUG_MODE          = os.getenv("DEBUG", "false").lower() == "true"

ROUTES_API_URL       = "https://routes.googleapis.com/directions/v2:computeRoutes"
PLACES_SEARCH_URL    = "https://places.googleapis.com/v1/places:searchText"

# Location bias: Bengaluru city centre
BLARU_LAT, BLARU_LNG, BLARU_RADIUS_M = 12.97, 77.59, 30_000

FIELD_MASK = (
    "routes.duration,"
    "routes.distanceMeters,"
    "routes.travelAdvisory.transitFare,"
    "routes.legs.steps.travelMode,"
    "routes.legs.steps.staticDuration,"
    "routes.legs.steps.distanceMeters,"
    "routes.legs.steps.navigationInstruction,"
    "routes.legs.steps.transitDetails,"
    "routes.legs.steps.transitDetails.transitLine.agencies"
)

# Intercity operators to exclude when local options exist
EXCLUDED_AGENCIES: set[str] = {
    "apsrtc", "ksrtc", "tnstc", "kerala srtc", "msrtc",
    "gsrtc", "rsrtc", "upsrtc", "tsrtc",
    # private sleeper / intercity keywords
    "sleeper", "express travels", "redbus", "orange tours",
}

# BMTC approximate fare per distance bucket (₹)
def _bmtc_fare(dist_m: int) -> int:
    km = dist_m / 1000
    if km <= 3:   return 10
    if km <= 7:   return 15
    if km <= 12:  return 20
    if km <= 20:  return 28
    return 35

_LANG_MAP = {"en-IN": "en", "kn-IN": "kn", "hi-IN": "hi", "ta-IN": "ta"}

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL = 600

def _cache_get(key: tuple) -> Optional[dict]:
    e = _cache.get(key)
    return e[1] if e and time.time() < e[0] else None

def _cache_set(key: tuple, val: dict) -> None:
    _cache[key] = (time.time() + CACHE_TTL, val)

# ── Straight-line distance (Haversine) ────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ── Place resolution ──────────────────────────────────────────────────────────
def resolve_place(query: str) -> dict:
    """
    Returns:
      {"ok": True,  "place_id": ..., "display_name": ..., "lat": ..., "lng": ...}
    or
      {"ok": False, "suggestions": [{"name":..., "place_id":...}, ...]}
    """
    if not GOOGLE_MAPS_API_KEY:
        return {"ok": True, "place_id": None, "display_name": query, "lat": None, "lng": None}

    body = {
        "textQuery": f"{query}, Bengaluru, Karnataka",
        "locationBias": {
            "circle": {
                "center": {"latitude": BLARU_LAT, "longitude": BLARU_LNG},
                "radius": float(BLARU_RADIUS_M),
            }
        },
        "includedType": "",
        "languageCode": "en",
        "regionCode": "in",
        "maxResultCount": 5,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location",
    }
    try:
        resp = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=5)
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except Exception:
        return {"ok": True, "place_id": None, "display_name": query, "lat": None, "lng": None}

    if not places:
        return {"ok": False, "suggestions": [], "query": query}

    top = places[0]
    top_name = top.get("displayName", {}).get("text", query)
    top_id   = top.get("id", "")
    top_loc  = top.get("location", {})
    top_lat  = top_loc.get("latitude")
    top_lng  = top_loc.get("longitude")

    # Ambiguity: if second result exists and has a meaningfully different name, offer suggestions
    suggestions = []
    for p in places[:3]:
        pname = p.get("displayName", {}).get("text", "")
        paddr = p.get("formattedAddress", "")
        suggestions.append({
            "name":      pname,
            "address":   paddr,
            "place_id":  p.get("id", ""),
            "lat":       p.get("location", {}).get("latitude"),
            "lng":       p.get("location", {}).get("longitude"),
        })

    # Simple ambiguity heuristic: if top 2 names are very different, ask for clarification
    if len(places) >= 2:
        name2 = places[1].get("displayName", {}).get("text", "")
        # consider ambiguous if neither name contains the other
        a_lc, b_lc = top_name.lower(), name2.lower()
        is_ambiguous = (a_lc not in b_lc) and (b_lc not in a_lc) and (
            # and they don't share significant tokens
            not set(re.split(r'\W+', a_lc)) & set(re.split(r'\W+', b_lc)) - {'', 'bengaluru', 'karnataka', 'road', 'nagar', 'layout'}
        )
        if is_ambiguous:
            return {"ok": False, "suggestions": suggestions, "query": query}

    return {
        "ok":           True,
        "place_id":     top_id,
        "display_name": top_name,
        "address":      top.get("formattedAddress", ""),
        "lat":          top_lat,
        "lng":          top_lng,
    }

# ── Auto / cab fare (Bengaluru approximate meter rates) ───────────────────────
def _auto_fare(distance_m: int) -> int:
    km = distance_m / 1000
    if km <= 2:  return 30
    return int(30 + (km - 2) * 15)

# ── Mode / vehicle mapping ────────────────────────────────────────────────────
# HEAVY_RAIL, LIGHT_RAIL, METRO_RAIL, SUBWAY → metro (covers Namma Metro)
# RAIL alone → train (long-distance / suburban)
_VEHICLE_MODE_MAP = {
    "SUBWAY":       "metro",
    "METRO_RAIL":   "metro",
    "HEAVY_RAIL":   "metro",   # Namma Metro sometimes reported as HEAVY_RAIL
    "LIGHT_RAIL":   "metro",
    "RAIL":         "train",
    "BUS":          "bus",
    "INTERCITY_BUS":"bus",
    "TROLLEYBUS":   "bus",
    "SHARE_TAXI":   "auto",
    "FERRY":        "train",
    "CABLE_CAR":    "metro",
    "GONDOLA_LIFT": "metro",
    "FUNICULAR":    "metro",
    "OTHER":        "bus",
}

def _parse_duration(s: str) -> float:
    if not s: return 0.0
    return round(int(s.rstrip("s")) / 60, 1)

def _normalise_metro_line(name: str) -> str:
    n = name.lower()
    if "purple" in n: return "PurpleEast"
    if "green"  in n: return "Green"
    if "yellow" in n: return "Yellow"
    return name

def _metro_color(name: str) -> str:
    n = name.lower()
    if "purple" in n: return "#8a3ffc"
    if "green"  in n: return "#198038"
    if "yellow" in n: return "#f1c21b"
    if not name:      return "#8d8d8d"
    return "#198038"

def _agency_names(line_info: dict) -> list[str]:
    return [a.get("name", "").strip().lower() for a in line_info.get("agencies", [])]

def _is_intercity(agencies: list[str]) -> bool:
    return any(
        any(excl in ag for excl in EXCLUDED_AGENCIES)
        for ag in agencies
    )

# ── Walk step processing ──────────────────────────────────────────────────────
def _build_walk_leg(steps_buf: list) -> dict:
    """
    Given a list of raw WALK sub-steps, produce a single walk leg with:
    - summary: "Walk X m to <destination>"
    - sub_steps: list of individual instruction strings
    """
    total_dist = sum(s.get("distanceMeters", 0) for s in steps_buf)
    total_dur  = sum(_parse_duration(s.get("staticDuration", "0s")) for s in steps_buf)
    sub_steps  = [
        s.get("navigationInstruction", {}).get("instructions", "").strip()
        for s in steps_buf
        if s.get("navigationInstruction", {}).get("instructions", "").strip()
    ]
    dist_str = f"{total_dist} m" if total_dist < 1000 else f"{total_dist/1000:.1f} km"
    summary  = f"Walk {dist_str}"
    if sub_steps:
        last_step = sub_steps[-1]
        # Try to extract destination hint from last step
        dest_match = re.search(r'(?:toward|to|onto)\s+(.+)', last_step, re.I)
        if dest_match:
            summary = f"Walk {dist_str} {dest_match.group(0)}"
    return {
        "type":        "walk",
        "from":        "",
        "to":          "",
        "walk_km":     round(total_dist / 1000, 2),
        "time_min":    round(total_dur, 1),
        "fare_inr":    0,
        "fare_label":  None,
        "instruction": summary,
        "sub_steps":   sub_steps,
        "line":        "",
        "direction":   "",
        "stops":       0,
        "is_interchange": False,
        "interchange_at": "",
        "line_display":   "",
        "agencies":    [],
    }

# ── Step mapping ──────────────────────────────────────────────────────────────
def _map_steps(raw_steps: list, route_dist_m: int) -> list[dict]:
    legs      = []
    walk_buf: list = []

    def flush_walk():
        if walk_buf:
            legs.append(_build_walk_leg(walk_buf))
            walk_buf.clear()

    for step in raw_steps:
        mode = step.get("travelMode", "WALK")

        if mode == "WALK":
            walk_buf.append(step)
            continue

        flush_walk()

        dur_min = _parse_duration(step.get("staticDuration", "0s"))
        dist_m  = step.get("distanceMeters", 0)
        td      = step.get("transitDetails", {})
        line_info = td.get("transitLine", {})
        vehicle   = line_info.get("vehicle", {})
        v_type    = vehicle.get("type", "BUS")
        leg_mode  = _VEHICLE_MODE_MAP.get(v_type, "bus")

        line_name  = line_info.get("nameShort") or line_info.get("name") or ""
        agencies   = _agency_names(line_info)
        dep_stop   = td.get("departureStop", {}).get("name", "")
        arr_stop   = td.get("arrivalStop",   {}).get("name", "")
        headsign   = td.get("headsign", "")
        stop_count = td.get("stopCount", 0)

        # Debug: log raw vehicle type so we can verify Namma Metro detection
        if DEBUG_MODE:
            print(f"[debug] step v_type={v_type!r} → {leg_mode!r}  line={line_name!r}  agencies={agencies}")

        # Fare label per step (filled in later at route level)
        fare_label = None
        if leg_mode == "bus" and agencies:
            if any("bmtc" in ag for ag in agencies):
                approx = _bmtc_fare(route_dist_m)
                fare_label = f"₹{approx} approx (BMTC)"

        legs.append({
            "type":           leg_mode,
            "line":           _normalise_metro_line(line_name),
            "color":          _metro_color(line_name),
            "from":           dep_stop,
            "to":             arr_stop,
            "direction":      f"towards {headsign}" if headsign else "",
            "stops":          stop_count,
            "time_min":       dur_min,
            "fare_inr":       0,       # set at route level
            "fare_label":     fare_label,
            "instruction":    f"Board {line_name or leg_mode} from {dep_stop} to {arr_stop}",
            "sub_steps":      [],
            "line_display":   line_name,
            "is_interchange": False,
            "interchange_at": "",
            "walk_km":        0,
            "agencies":       agencies,
            "_v_type":        v_type if DEBUG_MODE else None,
        })

    flush_walk()
    return legs

# ── Transfer count ────────────────────────────────────────────────────────────
def _transfer_count(legs: list[dict]) -> int:
    transit = [l for l in legs if l["type"] in ("metro", "bus", "train")]
    return max(0, len(transit) - 1)

# ── Intercity filter ──────────────────────────────────────────────────────────
def _has_intercity(legs: list[dict]) -> bool:
    return any(_is_intercity(l.get("agencies", [])) for l in legs)

def _has_local(legs: list[dict]) -> bool:
    for l in legs:
        ags = l.get("agencies", [])
        if any("bmtc" in a or "metro" in a or "nmrc" in a or "bmrcl" in a for a in ags):
            return True
    return False

# ── Route fingerprint for deduplication ───────────────────────────────────────
def _route_fingerprint(legs: list[dict]) -> tuple:
    """
    Key = sequence of (type, line, dep_stop) for transit legs.
    Including dep_stop catches routes that use the same line but board at different stations.
    """
    return tuple(
        (l["type"], l["line"], l["from"])
        for l in legs
        if l["type"] in ("metro", "bus", "train")
    )

# ── Meaningful option label from legs ─────────────────────────────────────────
def _option_label_from_legs(legs: list[dict]) -> str:
    """
    Derive a human label from the transit legs of a route.
    Examples: "Purple Line", "Bus 500K", "Purple Line + Bus 201", "KIA-9"
    Falls back to mode name when line_display is absent.
    """
    parts = []
    for l in legs:
        if l["type"] not in ("metro", "bus", "train"):
            continue
        display = l.get("line_display") or l.get("line") or ""
        if l["type"] == "metro":
            # e.g. "PurpleEast" → "Purple Line", "Green" → "Green Line"
            norm = l.get("line", display)
            if norm == "PurpleEast":
                name = "Purple Line"
            elif norm in ("Green", "Yellow"):
                name = f"{norm} Line"
            elif display:
                name = display
            else:
                name = "Metro"
        elif l["type"] == "bus":
            name = f"Bus {display}" if display else "Bus"
        else:
            name = display or "Train"
        if name not in parts:
            parts.append(name)
    if not parts:
        # Walk or auto only — caller handles these specially
        for l in legs:
            if l["type"] == "auto":
                return "Direct Auto"
            if l["type"] == "walk":
                return "Walk"
        return "Route"
    return " + ".join(parts[:3])

# ── Build all options ─────────────────────────────────────────────────────────
def _build_options(
    raw_routes: list,
    total_dist_m: int,
    from_display: str,
    to_display: str,
    drive_option: Optional[dict] = None,   # pre-built auto/cab leg from DRIVE call
) -> list[dict]:
    parsed = []
    has_any_local = False
    seen_fingerprints: set[tuple] = set()

    for route in raw_routes[:4]:   # allow 4 candidates before dedup
        raw_steps = []
        for leg in route.get("legs", []):
            raw_steps.extend(leg.get("steps", []))

        legs    = _map_steps(raw_steps, total_dist_m)
        fp      = _route_fingerprint(legs)

        # Skip duplicate routes (same sequence of transit lines)
        if fp in seen_fingerprints:
            continue
        seen_fingerprints.add(fp)

        dur_s   = int(route.get("duration", "0s").rstrip("s"))
        dur_min = round(dur_s / 60, 1)
        dist_m  = route.get("distanceMeters", total_dist_m)

        # Fare from travelAdvisory
        fare_struct = route.get("travelAdvisory", {}).get("transitFare", {})
        fare_inr    = None   # None = not available
        fare_label  = "Fare not available"
        if fare_struct:
            units = int(fare_struct.get("units", 0))
            nanos = int(fare_struct.get("nanos", 0))
            fare_inr   = units + round(nanos / 1e9)
            fare_label = f"₹{fare_inr}"

        # Distribute fare across transit legs
        transit_legs = [l for l in legs if l["type"] in ("metro", "bus", "train")]
        if fare_inr and transit_legs:
            per_leg = fare_inr // len(transit_legs)
            for l in transit_legs:
                l["fare_inr"]   = per_leg
                l["fare_label"] = f"₹{per_leg}"
        elif not fare_inr:
            # Apply BMTC approx fare per bus leg if no route-level fare
            for l in transit_legs:
                if l["type"] == "bus" and l["fare_label"]:
                    pass   # already set in _map_steps
                elif l["type"] == "bus" and not l["fare_label"]:
                    l["fare_label"] = f"₹{_bmtc_fare(dist_m)} approx (BMTC)"

        transfers = _transfer_count(legs)
        is_intercity = _has_intercity(legs)
        is_local     = _has_local(legs)
        if is_local:
            has_any_local = True

        parsed.append({
            "label":             "",
            "total_time_min":    dur_min,
            "total_fare_inr":    fare_inr or 0,
            "fare_label":        fare_label,
            "interchange_count": transfers,
            "legs":              legs,
            "distance_m":        dist_m,
            "is_intercity":      is_intercity,
            "is_local":          is_local,
        })

    # Filter intercity if any local option exists
    if has_any_local:
        parsed = [p for p in parsed if not p["is_intercity"]]

    if not parsed:
        return []

    # ── Multi-label assignment ─────────────────────────────────────────────────
    # Fastest = strictly minimum duration
    # Cheapest = only if ≥2 options have a known fare (fare_inr > 0)
    # Fewest transfers = only if it differs from the Fastest option

    fastest_idx   = min(range(len(parsed)), key=lambda i: parsed[i]["total_time_min"])
    fastest_time  = parsed[fastest_idx]["total_time_min"]

    options_with_fare = [i for i, o in enumerate(parsed) if o["total_fare_inr"] > 0]
    cheapest_idx: Optional[int] = None
    if len(options_with_fare) >= 2:
        cheapest_idx = min(options_with_fare, key=lambda i: parsed[i]["total_fare_inr"])

    fewest_xfer_idx = min(range(len(parsed)), key=lambda i: parsed[i]["interchange_count"])
    fewest_xfer_min = parsed[fewest_xfer_idx]["interchange_count"]
    # Only assign if it results in a different option than Fastest
    assign_fewest   = (fewest_xfer_min < parsed[fastest_idx]["interchange_count"])

    for i, o in enumerate(parsed):
        badge_parts = []
        if i == fastest_idx:
            badge_parts.append("Fastest")
        if cheapest_idx is not None and i == cheapest_idx:
            badge_parts.append("Cheapest")
        if assign_fewest and i == fewest_xfer_idx:
            badge_parts.append("Fewest transfers")

        # Always derive a meaningful route name from the legs
        route_name = _option_label_from_legs(o["legs"])
        badge = " · ".join(badge_parts)
        if badge:
            o["label"]      = f"{badge} — {route_name}"
            o["badge"]      = badge        # for UI highlight / reply selection
            o["route_name"] = route_name
        else:
            o["label"]      = route_name
            o["badge"]      = ""
            o["route_name"] = route_name

    # Walk option for very short trips (< 2 km)
    if total_dist_m < 2000:
        walk_time = round((total_dist_m / 1000) / 5.0 * 60, 1)
        parsed.insert(0, {
            "label":             "Walk",
            "badge":             "",
            "route_name":        "Walk",
            "total_time_min":    walk_time,
            "total_fare_inr":    0,
            "fare_label":        "Free",
            "interchange_count": 0,
            "legs": [{
                "type":        "walk",
                "from":        from_display,
                "to":          to_display,
                "walk_km":     round(total_dist_m / 1000, 2),
                "time_min":    walk_time,
                "fare_inr":    0,
                "fare_label":  "Free",
                "instruction": f"Walk {total_dist_m} m from {from_display} to {to_display}.",
                "sub_steps":   [],
                "line": "", "direction": "", "stops": 0,
                "is_interchange": False, "interchange_at": "",
                "line_display": "", "agencies": [],
            }],
            "distance_m": total_dist_m,
        })

    # Auto / Direct Cab option from real DRIVE route (or fallback estimate)
    if drive_option:
        drive_option.setdefault("badge", "")
        drive_option.setdefault("route_name", drive_option["label"])
        parsed.append(drive_option)
    else:
        best_dist = min(
            (o["distance_m"] for o in parsed if o.get("route_name") != "Walk"),
            default=total_dist_m,
        )
        auto_fare = _auto_fare(best_dist)
        auto_time = round((best_dist / 1000) / 17 * 60, 1)
        is_cab    = best_dist > 25_000
        lbl       = "Direct Cab" if is_cab else "Direct Auto"
        leg_type  = "cab" if is_cab else "auto"
        instr     = (
            f"Take a cab directly. Approx fare ₹{auto_fare}."
            if is_cab else
            f"Take an auto-rickshaw directly. Approx Bengaluru meter fare ₹{auto_fare}."
        )
        parsed.append({
            "label":             lbl,
            "badge":             "",
            "route_name":        lbl,
            "total_time_min":    auto_time,
            "total_fare_inr":    auto_fare,
            "fare_label":        f"₹{auto_fare} approx",
            "interchange_count": 0,
            "legs": [{
                "type":        leg_type,
                "from":        from_display,
                "to":          to_display,
                "walk_km":     round(best_dist / 1000, 1),
                "time_min":    auto_time,
                "fare_inr":    auto_fare,
                "fare_label":  f"₹{auto_fare} approx",
                "instruction": instr,
                "sub_steps":   [],
                "line": "", "direction": "", "stops": 0,
                "is_interchange": False, "interchange_at": "",
                "line_display": "", "agencies": [],
            }],
            "distance_m":       best_dist,
            "is_auto_estimate": True,
        })

    # ── Sort: recommended first, then by duration; short-trip: auto first ──────
    # Find the recommended option (Fastest badge if no cheapest override)
    def _sort_key(o):
        badge = o.get("badge", "")
        rname = o.get("route_name", o["label"])
        if rname == "Walk":    return (0, o["total_time_min"])
        # Short-trip: auto/cab surface first
        if any(l["type"] in ("auto", "cab") for l in o["legs"]): return (1, o["total_time_min"])
        if "Fastest" in badge: return (2, o["total_time_min"])
        if "Cheapest" in badge: return (3, o["total_time_min"])
        return (4, o["total_time_min"])

    best_dist_overall = min(
        (o["distance_m"] for o in parsed if o.get("route_name") != "Walk"),
        default=total_dist_m,
    )
    if best_dist_overall < 5000:
        parsed.sort(key=_sort_key)
    else:
        # Recommended option first, rest by duration
        parsed.sort(key=lambda o: (
            0 if "Fastest" in o.get("badge", "") else
            0 if "Cheapest" in o.get("badge", "") else
            1 if any(l["type"] in ("auto", "cab") for l in o["legs"]) else
            2,
            o["total_time_min"]
        ))

    return parsed


# ── DRIVE call for real auto/cab option ───────────────────────────────────────
def _get_drive_option(
    origin: dict,
    destination: dict,
    from_display: str,
    to_display: str,
    departure_time: Optional[str],
) -> Optional[dict]:
    """
    Calls Routes API with travelMode=DRIVE to get realistic duration + distance.
    Returns a fully-formed option dict or None on failure.
    """
    body: dict = {
        "origin":      origin,
        "destination": destination,
        "travelMode":  "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "regionCode":  "IN",
    }
    if departure_time:
        body["departureTime"] = departure_time

    headers = {
        "Content-Type":   "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
    }
    try:
        resp = requests.post(ROUTES_API_URL, json=body, headers=headers, timeout=8)
        resp.raise_for_status()
        routes = resp.json().get("routes", [])
        if not routes:
            return None
        r = routes[0]
        dur_s  = int(r.get("duration", "0s").rstrip("s"))
        dist_m = r.get("distanceMeters", 0)
    except Exception:
        return None

    fare     = _auto_fare(dist_m)
    dur_min  = round(dur_s / 60, 1)
    is_cab   = dist_m > 25_000
    label    = "Direct Cab" if is_cab else "Direct Auto"
    leg_type = "cab" if is_cab else "auto"
    instr    = (
        f"Take a cab directly. Approx fare ₹{fare}."
        if is_cab else
        f"Take an auto-rickshaw directly. Approx Bengaluru meter fare ₹{fare}."
    )

    return {
        "label":             label,
        "badge":             "",
        "route_name":        label,
        "total_time_min":    dur_min,
        "total_fare_inr":    fare,
        "fare_label":        f"₹{fare} approx",
        "interchange_count": 0,
        "legs": [{
            "type":        leg_type,
            "from":        from_display,
            "to":          to_display,
            "walk_km":     round(dist_m / 1000, 1),
            "time_min":    dur_min,
            "fare_inr":    fare,
            "fare_label":  f"₹{fare} approx",
            "instruction": instr,
            "sub_steps":   [],
            "line": "", "direction": "", "stops": 0,
            "is_interchange": False, "interchange_at": "",
            "line_display": "", "agencies": [],
        }],
        "distance_m":       dist_m,
        "is_auto_estimate": True,
    }


# ── Public: get routes ────────────────────────────────────────────────────────
def get_google_routes(
    from_place: str,
    to_place: str,
    lang: str = "en-IN",
    departure_time: Optional[str] = None,   # RFC 3339; None = now
) -> Optional[dict]:
    """
    Returns dict with: options, source, origin, destination, resolved_from, resolved_to.
    Returns None on hard failure (triggers engine.py fallback).
    Returns dict with ambiguous=True when place resolution is ambiguous.
    """
    if not GOOGLE_MAPS_API_KEY:
        return None

    # Default departure_time to now (UTC, RFC 3339)
    if not departure_time:
        departure_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cache_key = (from_place.lower(), to_place.lower(), lang, departure_time)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # ── Resolve places ────────────────────────────────────────────────────────
    from_res = resolve_place(from_place)
    to_res   = resolve_place(to_place)

    if not from_res["ok"]:
        return {
            "ambiguous":    True,
            "field":        "from",
            "query":        from_place,
            "suggestions":  from_res.get("suggestions", []),
            "source":       "google",
        }
    if not to_res["ok"]:
        return {
            "ambiguous":    True,
            "field":        "to",
            "query":        to_place,
            "suggestions":  to_res.get("suggestions", []),
            "source":       "google",
        }

    # ── Build origin/destination for Routes API ───────────────────────────────
    if from_res["place_id"]:
        origin = {"placeId": from_res["place_id"]}
    else:
        origin = {"address": f"{from_place}, Bengaluru, Karnataka"}

    if to_res["place_id"]:
        destination = {"placeId": to_res["place_id"]}
    else:
        destination = {"address": f"{to_place}, Bengaluru, Karnataka"}

    # Straight-line distance for short-trip logic
    sl_dist = None
    if all(x is not None for x in [from_res.get("lat"), from_res.get("lng"),
                                     to_res.get("lat"),   to_res.get("lng")]):
        sl_dist = _haversine(from_res["lat"], from_res["lng"],
                              to_res["lat"],   to_res["lng"])

    google_lang = _LANG_MAP.get(lang, "en")

    body = {
        "origin":        origin,
        "destination":   destination,
        "travelMode":    "TRANSIT",
        "departureTime": departure_time,
        "computeAlternativeRoutes": True,
        "regionCode":    "IN",
        "languageCode":  google_lang,
        "transitPreferences": {
            "routingPreference":  "LESS_WALKING",
            "allowedTravelModes": ["BUS", "SUBWAY", "RAIL", "LIGHT_RAIL"],
        },
    }

    headers = {
        "Content-Type":     "application/json",
        "X-Goog-Api-Key":   GOOGLE_MAPS_API_KEY,
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

    total_dist_m = int(sl_dist) if sl_dist else raw_routes[0].get("distanceMeters", 10_000)
    from_display = from_res["display_name"]
    to_display   = to_res["display_name"]

    # ── DRIVE call for real auto/cab option ───────────────────────────────────
    drive_option = _get_drive_option(
        origin, destination, from_display, to_display, departure_time
    )

    options = _build_options(raw_routes, total_dist_m, from_display, to_display, drive_option)
    if not options:
        return None

    result = {
        "source":          "google",
        "origin":          from_display,
        "destination":     to_display,
        "resolved_from":   from_res,
        "resolved_to":     to_res,
        "options":         options,
        "fallback_note":   None,
        "departure_time":  departure_time,
        "raw":             data if DEBUG_MODE else None,
    }
    _cache_set(cache_key, result)
    return result


# ── Debug helper ──────────────────────────────────────────────────────────────
def debug_route(from_place: str, to_place: str, departure_time: Optional[str] = None) -> dict:
    """Full diagnostic response for /debug/route."""
    from_res = resolve_place(from_place)
    to_res   = resolve_place(to_place)

    maps_link = lambda r: (
        f"https://maps.google.com/?q={r['lat']},{r['lng']}"
        if r.get("lat") else None
    )

    result = get_google_routes(from_place, to_place, departure_time=departure_time)

    opts = result.get("options", []) if result else []
    return {
        "resolved_from":       {**from_res, "maps_link": maps_link(from_res)},
        "resolved_to":         {**to_res,   "maps_link": maps_link(to_res)},
        "departure_time":      result.get("departure_time") if result else None,
        "raw_routes_response": result.get("raw") if result else None,
        "filtered_options": [
            {
                "label":             o["label"],
                "total_time_min":    o["total_time_min"],
                "fare_label":        o["fare_label"],
                "interchange_count": o["interchange_count"],
                "modes":             [l["type"] for l in o["legs"]],
                "agencies":          list({a for l in o["legs"] for a in l.get("agencies", [])}),
                "vehicle_types":     [l.get("_v_type") for l in o["legs"] if l.get("_v_type")],
            }
            for o in opts
        ],
    }
