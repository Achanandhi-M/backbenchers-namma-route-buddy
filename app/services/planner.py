"""
app/services/planner.py – Resolve places, fetch routes, and return a unified result dict.

Tries google_routes first; falls back to metro_graph when Google Maps is unavailable
or returns no routes.
"""

from __future__ import annotations
from typing import Optional

from app.engines import google_routes, metro_graph


def _pick_recommended(options: list, preference: str) -> int:
    """
    Return the index of the option to narrate and show selected.
    - preference="cheapest"  → first option whose badge contains "Cheapest"
    - otherwise              → first option whose badge contains "Fastest"
    - Falls back to index 0 if no badged option is found.
    """
    target = "Cheapest" if (preference or "any").lower() == "cheapest" else "Fastest"
    for i, o in enumerate(options):
        if target in o.get("badge", ""):
            return i
    return 0


def get_options(
    from_place: str,
    to_place: str,
    lang: str = "en-IN",
    departure_time: Optional[str] = None,
) -> dict:
    """
    Returns one of:
      {source, origin, destination, options, fallback_note, newcomer_tip, ...}
      {ambiguous, field, query, suggestions, source}   – place was ambiguous
    """
    result = google_routes.plan_route(from_place, to_place, lang,
                                      departure_time=departure_time)

    if result and result.get("ambiguous"):
        return result

    if result:
        result.setdefault("newcomer_tip", "")
        result.setdefault("fallback_note", None)
        return result

    # Fallback to offline metro graph
    result = metro_graph.plan_route(from_place, to_place)
    result.setdefault("newcomer_tip", "")
    return result
