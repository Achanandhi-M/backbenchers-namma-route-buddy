"""
engine.py – Metro route engine for Namma Route Buddy.

Builds a weighted graph from data/metro.json and computes optimal paths
(BFS/Dijkstra) between any two metro stations or areas.
Fuzzy name matching via difflib handles voice typos.
"""

from __future__ import annotations
import json, heapq
from pathlib import Path
from difflib import get_close_matches
from dataclasses import dataclass, field
from typing import Optional

# ── Load data ─────────────────────────────────────────────────────────────────
_DATA = Path(__file__).parent / "data"
_METRO = json.loads((_DATA / "metro.json").read_text())
_AREAS = json.loads((_DATA / "areas.json").read_text())

# ── Constants ─────────────────────────────────────────────────────────────────
TIME_PER_STOP_MIN    = 2.5   # average time between adjacent stations
INTERCHANGE_MIN      = 3     # extra minutes at an interchange
WALK_SPEED_KPH       = 5.0
AUTO_MIN_CHARGE_INR  = 30

# ── Build station→line index ─────────────────────────────────────────────────
# station_lines[station_name] = set of line keys that serve it
station_lines: dict[str, set[str]] = {}
# ordered list of stations per line
line_stations: dict[str, list[str]] = {}

for line_key, line_data in _METRO["lines"].items():
    stations = line_data["stations"]
    line_stations[line_key] = stations
    for s in stations:
        station_lines.setdefault(s, set()).add(line_key)

# interchange set
interchange_names: set[str] = {ic["name"] for ic in _METRO["interchanges"]}
interchange_map: dict[str, dict] = {ic["name"]: ic for ic in _METRO["interchanges"]}

# ── Fare slabs ────────────────────────────────────────────────────────────────
def metro_fare(stop_count: int) -> int:
    for slab in _METRO["fare_slabs"]:
        if stop_count <= slab["max_stops"]:
            return slab["fare_inr"]
    return _METRO["fare_slabs"][-1]["fare_inr"]

# ── Graph edges ───────────────────────────────────────────────────────────────
# Graph: station → list of (neighbor_station, line_key, weight_min)
graph: dict[str, list[tuple[str, str, float]]] = {s: [] for s in station_lines}

for line_key, stations in line_stations.items():
    for i in range(len(stations) - 1):
        a, b = stations[i], stations[i + 1]
        w = TIME_PER_STOP_MIN
        graph[a].append((b, line_key, w))
        graph[b].append((a, line_key, w))

# Add zero-cost interchange edges (same station, different lines)
for ic in _METRO["interchanges"]:
    name = ic["name"]
    # interchange edges are implicit: a station node is shared across lines,
    # but we add a small penalty when switching lines (handled in path reconstruction)

# ── All known station names (for fuzzy matching) ──────────────────────────────
ALL_STATION_NAMES: list[str] = sorted(station_lines.keys())

# alias table: lowercase alias → canonical area or station name
_alias_to_canonical: dict[str, str] = {}

# index area aliases
for area in _AREAS["areas"]:
    for alias in area["aliases"]:
        _alias_to_canonical[alias.lower()] = area["name"]
    _alias_to_canonical[area["name"].lower()] = area["name"]

# index station names directly
for sname in ALL_STATION_NAMES:
    _alias_to_canonical[sname.lower()] = sname

# canonical name → area entry (or None if it's a bare station)
_area_by_name: dict[str, dict] = {a["name"]: a for a in _AREAS["areas"]}

# ── Fuzzy name resolution ─────────────────────────────────────────────────────
def resolve_place(query: str) -> Optional[tuple[str, str]]:
    """
    Returns (canonical_name, nearest_station) or None if unresolvable.
    canonical_name is the display name; nearest_station is the metro station key.
    """
    q = query.strip().lower()
    if not q:
        return None

    # exact alias lookup
    if q in _alias_to_canonical:
        canonical = _alias_to_canonical[q]
    else:
        # fuzzy match against all aliases
        all_aliases = list(_alias_to_canonical.keys())
        matches = get_close_matches(q, all_aliases, n=1, cutoff=0.55)
        if not matches:
            return None
        canonical = _alias_to_canonical[matches[0]]

    # resolve to station
    if canonical in _area_by_name:
        area = _area_by_name[canonical]
        return (canonical, area["nearest_station"])
    elif canonical in station_lines:
        return (canonical, canonical)
    return None

def fuzzy_suggestions(query: str, n: int = 3) -> list[str]:
    """Return up to n candidate display names for a query string."""
    q = query.strip().lower()
    all_aliases = list(_alias_to_canonical.keys())
    matches = get_close_matches(q, all_aliases, n=n, cutoff=0.45)
    seen, out = set(), []
    for m in matches:
        c = _alias_to_canonical[m]
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out

# ── Dijkstra ──────────────────────────────────────────────────────────────────
@dataclass(order=True)
class _State:
    cost: float
    station: str = field(compare=False)
    prev_line: str = field(compare=False)  # line we arrived on
    path: list = field(compare=False, default_factory=list)
    # path items: (from_station, to_station, line_key, travel_min, stops)

def dijkstra(src: str, dst: str) -> Optional[list]:
    """
    Returns the cheapest-time path as a list of segments:
      [(from, to, line_key, travel_min, stop_count), ...]
    or None if unreachable.
    """
    if src not in graph or dst not in graph:
        return None
    if src == dst:
        return []

    dist = {src: 0.0}
    pq   = [_State(0.0, src, "", [])]

    while pq:
        state = heapq.heappop(pq)
        cost, cur, prev_line, path = state.cost, state.station, state.prev_line, state.path

        if cur == dst:
            return path

        if cost > dist.get(cur, float("inf")):
            continue

        for (nxt, line_key, edge_w) in graph[cur]:
            # interchange penalty: if we switch lines at an interchange station
            xfer_pen = 0.0
            if prev_line and line_key != prev_line and cur in interchange_names:
                ic = interchange_map[cur]
                xfer_pen = ic.get("transfer_time_min", INTERCHANGE_MIN)

            new_cost = cost + edge_w + xfer_pen
            if new_cost < dist.get(nxt, float("inf")):
                dist[nxt] = new_cost
                # compute stop delta for this edge (always 1)
                new_path = path + [(cur, nxt, line_key, edge_w + xfer_pen, 1)]
                heapq.heappush(pq, _State(new_cost, nxt, line_key, new_path))

    return None

# ── Segment aggregation ───────────────────────────────────────────────────────
@dataclass
class MetroLeg:
    line: str
    color: str
    from_station: str
    to_station: str
    direction: str         # "towards <terminus>"
    stops: int
    time_min: float
    fare_inr: int
    is_interchange: bool = False
    interchange_at: str = ""

@dataclass
class LastMile:
    mode: str              # "walk" | "auto"
    from_place: str
    to_place: str
    walk_km: float
    time_min: float
    fare_inr: int

@dataclass
class RouteResult:
    origin_display: str
    destination_display: str
    total_time_min: float
    total_fare_inr: int
    interchange_count: int
    legs: list             # list of LastMile | MetroLeg
    newcomer_tip: str
    not_found: Optional[str] = None  # set if a place could not be resolved

def _terminus_direction(line_key: str, from_station: str, to_station: str) -> str:
    """Return the 'towards X' terminus string for the direction of travel."""
    stations = line_stations.get(line_key, [])
    if not stations:
        return ""
    try:
        fi = stations.index(from_station)
        ti = stations.index(to_station)
    except ValueError:
        return ""
    terminus = stations[-1] if ti > fi else stations[0]
    return f"towards {terminus}"

def _line_color(line_key: str) -> str:
    return _METRO["lines"].get(line_key, {}).get("color", "#8d8d8d")

def _merge_path_segments(raw_path: list) -> list[MetroLeg]:
    """Merge consecutive same-line hops into single legs."""
    if not raw_path:
        return []
    legs: list[MetroLeg] = []
    cur_from, _, cur_line, cur_time, cur_stops = raw_path[0]
    cur_to = raw_path[0][1]

    for i in range(1, len(raw_path)):
        frm, to, line, t, stops = raw_path[i]
        if line == cur_line:
            cur_to = to
            cur_time += t
            cur_stops += stops
        else:
            # close current leg
            direction = _terminus_direction(cur_line, cur_from, cur_to)
            legs.append(MetroLeg(
                line=cur_line, color=_line_color(cur_line),
                from_station=cur_from, to_station=cur_to,
                direction=direction,
                stops=cur_stops, time_min=round(cur_time, 1),
                fare_inr=metro_fare(cur_stops),
                is_interchange=frm in interchange_names,
                interchange_at=frm if frm in interchange_names else "",
            ))
            cur_from, cur_to, cur_line, cur_time, cur_stops = frm, to, line, t, stops
    # last leg
    direction = _terminus_direction(cur_line, cur_from, cur_to)
    legs.append(MetroLeg(
        line=cur_line, color=_line_color(cur_line),
        from_station=cur_from, to_station=cur_to,
        direction=direction,
        stops=cur_stops, time_min=round(cur_time, 1),
        fare_inr=metro_fare(cur_stops),
    ))
    return legs

def _last_mile_time(area: dict) -> float:
    lm = area["last_mile"]
    if lm["mode"] == "walk":
        return round((lm["walk_km"] / WALK_SPEED_KPH) * 60, 1)
    else:
        return round(lm["walk_km"] * 3.5, 1)   # rough auto estimate

def _build_last_mile(area_or_station: str, station: str, side: str) -> Optional[LastMile]:
    """Build a LastMile leg if the area is different from its metro station."""
    if area_or_station == station:
        return None
    if area_or_station not in _area_by_name:
        return None
    area = _area_by_name[area_or_station]
    lm = area["last_mile"]
    t = _last_mile_time(area)
    fare = lm["auto_fare_inr"] if lm["mode"] == "auto" else 0
    if side == "first":
        return LastMile(mode=lm["mode"], from_place=area_or_station,
                        to_place=station, walk_km=lm["walk_km"],
                        time_min=t, fare_inr=fare)
    else:
        return LastMile(mode=lm["mode"], from_place=station,
                        to_place=area_or_station, walk_km=lm["walk_km"],
                        time_min=t, fare_inr=fare)

# ── Public API ────────────────────────────────────────────────────────────────
def compute_route(from_query: str, to_query: str) -> RouteResult:
    """
    Main entry point. Returns a RouteResult.
    Sets result.not_found if either place can't be resolved.
    """
    from_resolved = resolve_place(from_query)
    to_resolved   = resolve_place(to_query)

    if not from_resolved:
        return RouteResult(
            origin_display=from_query, destination_display=to_query,
            total_time_min=0, total_fare_inr=0, interchange_count=0, legs=[],
            newcomer_tip="",
            not_found=f"I couldn't find a station near '{from_query}'. "
                       "Try a nearby landmark or metro station name.",
        )
    if not to_resolved:
        return RouteResult(
            origin_display=from_query, destination_display=to_query,
            total_time_min=0, total_fare_inr=0, interchange_count=0, legs=[],
            newcomer_tip="",
            not_found=f"I couldn't find a station near '{to_query}'. "
                       "Try a nearby landmark or metro station name.",
        )

    from_name, from_station = from_resolved
    to_name,   to_station   = to_resolved

    if from_station == to_station:
        # same station – only last mile
        pass

    raw_path = dijkstra(from_station, to_station)
    if raw_path is None:
        return RouteResult(
            origin_display=from_name, destination_display=to_name,
            total_time_min=0, total_fare_inr=0, interchange_count=0, legs=[],
            newcomer_tip="",
            not_found=f"No metro route found between {from_name} and {to_name}.",
        )

    # Build legs
    legs: list = []
    total_time  = 0.0
    total_fare  = 0

    # First-mile
    first_lm = _build_last_mile(from_name, from_station, "first")
    if first_lm:
        legs.append(first_lm)
        total_time += first_lm.time_min
        total_fare += first_lm.fare_inr

    # Metro legs
    metro_legs = _merge_path_segments(raw_path)
    xfer_count = 0
    for ml in metro_legs:
        legs.append(ml)
        total_time += ml.time_min
        total_fare += ml.fare_inr
        if ml.is_interchange:
            xfer_count += 1

    # Last-mile
    last_lm = _build_last_mile(to_name, to_station, "last")
    if last_lm:
        legs.append(last_lm)
        total_time += last_lm.time_min
        total_fare += last_lm.fare_inr

    # Newcomer tip
    tip = _build_tip(from_name, to_name, metro_legs, first_lm, last_lm)

    return RouteResult(
        origin_display=from_name,
        destination_display=to_name,
        total_time_min=round(total_time, 1),
        total_fare_inr=total_fare,
        interchange_count=xfer_count,
        legs=legs,
        newcomer_tip=tip,
    )

def _build_tip(origin: str, dest: str, metro_legs: list, first_lm, last_lm) -> str:
    tips = []
    if first_lm and first_lm.mode == "auto":
        tips.append(f"At {origin}, take an auto to {first_lm.to_place} metro station. Insist on meter or negotiate ₹{first_lm.fare_inr}.")
    if any(ml.is_interchange for ml in metro_legs):
        for ml in metro_legs:
            if ml.is_interchange:
                tips.append(f"Change lines at {ml.interchange_at}. Follow the interchange signs inside the station.")
    if last_lm and last_lm.mode == "auto":
        tips.append(f"From {last_lm.from_place} station, take an auto to {dest}. Typical fare ₹{last_lm.fare_inr}.")
    if not tips:
        tips.append("Store a Namma Metro Smart Card for faster boarding — it also gives a small discount on fares.")
    return " ".join(tips)

# ── Serialization for JSON responses ─────────────────────────────────────────
def route_to_dict(r: RouteResult) -> dict:
    def leg_dict(leg):
        if isinstance(leg, MetroLeg):
            return {
                "type": "metro",
                "line": leg.line,
                "color": leg.color,
                "from": leg.from_station,
                "to": leg.to_station,
                "direction": leg.direction,
                "stops": leg.stops,
                "time_min": leg.time_min,
                "fare_inr": leg.fare_inr,
                "is_interchange": leg.is_interchange,
                "interchange_at": leg.interchange_at,
            }
        else:  # LastMile
            return {
                "type": leg.mode,  # "walk" or "auto"
                "from": leg.from_place,
                "to": leg.to_place,
                "walk_km": leg.walk_km,
                "time_min": leg.time_min,
                "fare_inr": leg.fare_inr,
            }
    return {
        "origin": r.origin_display,
        "destination": r.destination_display,
        "total_time_min": r.total_time_min,
        "total_fare_inr": r.total_fare_inr,
        "interchange_count": r.interchange_count,
        "legs": [leg_dict(l) for l in r.legs],
        "newcomer_tip": r.newcomer_tip,
        "not_found": r.not_found,
    }

# ── Autocomplete list for the UI ──────────────────────────────────────────────
def autocomplete_list() -> list[str]:
    """Return display names for all known areas + bare stations (deduplicated)."""
    names = {a["name"] for a in _AREAS["areas"]}
    names.update(ALL_STATION_NAMES)
    return sorted(names)
