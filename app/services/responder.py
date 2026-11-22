"""
app/services/responder.py – Generate the spoken / text reply in the user's language.

Uses Granite via Ollama to narrate a chosen route option.  The system prompt
enforces a direct, step-by-step style with no filler phrases.
"""

from __future__ import annotations
from typing import Optional

from app.config import cfg
from app.llm import ollama

_LANG_NAME = {
    "kn-IN": "Kannada",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "en-IN": "English",
}


def _leg_text(i: int, leg: dict) -> str:
    t        = leg.get("type", "")
    fare_str = leg.get("fare_label") or (
        f"₹{leg['fare_inr']}" if leg.get("fare_inr") else ""
    )
    # Boarding-point vocabulary based on vehicle type
    pt = "metro station" if t == "metro" else "bus stop" if t == "bus" else ""
    frm = f"{leg.get('from', '')} {pt}".strip() if pt else leg.get("from", "")
    to_ = f"{leg.get('to',   '')} {pt}".strip() if pt else leg.get("to",   "")

    fare_part = f", {fare_str}" if fare_str else ""
    if t == "metro":
        line = leg.get("line", "")
        direction = leg.get("direction", "")
        stops = leg.get("stops", 0)
        mins  = leg.get("time_min", 0)
        txt = (f"Step {i}: Board {line} Line {direction} at {frm}, "
               f"alight at {to_} ({stops} stops, ~{mins} min{fare_part})")
        if leg.get("is_interchange"):
            txt += f"\n  Change lines at {leg.get('interchange_at', '')}."
        return txt
    if t == "bus":
        line = leg.get("line_display") or leg.get("line", "")
        mins = leg.get("time_min", 0)
        return (f"Step {i}: Board bus {line} at {frm}, "
                f"alight at {to_} (~{mins} min{fare_part})")
    if t == "walk":
        km   = leg.get("walk_km", 0)
        mins = leg.get("time_min", 0)
        return f"Step {i}: Walk {km} km from {leg.get('from', '')} to {leg.get('to', '')} (~{mins} min)"
    if t == "auto":
        mins = leg.get("time_min", 0)
        return f"Step {i}: Take auto from {leg.get('from', '')} to {leg.get('to', '')} (~{mins} min{fare_part})"
    if t == "cab":
        mins = leg.get("time_min", 0)
        return f"Step {i}: Take a cab from {leg.get('from', '')} to {leg.get('to', '')} (~{mins} min{fare_part})"
    return f"Step {i}: {leg.get('instruction', '')}"


def narrate(
    option: dict,
    origin: str,
    destination: str,
    lang: str,
    fallback_note: Optional[str] = None,
) -> str:
    """Call Granite to produce a step-by-step reply for the given option."""
    lang_name = _LANG_NAME.get(lang, "English")

    legs_text = [_leg_text(i + 1, leg) for i, leg in enumerate(option.get("legs", []))]

    fare_summary = option.get("fare_label") or (
        f"₹{option['total_fare_inr']}" if option.get("total_fare_inr") else "—"
    )
    summary = (
        f"Route: {origin} → {destination} ({option.get('label', '')})\n"
        f"Total: ~{option.get('total_time_min', 0)} min, ~{fare_summary}, "
        f"{option.get('interchange_count', 0)} transfer(s)\n\n"
        + "\n".join(legs_text)
    )
    if fallback_note:
        summary += f"\n\nNote: {fallback_note}"

    messages = [
        {"role": "system", "content": (
            f"You are Namma Route Buddy, a Bengaluru transit assistant. "
            f"Reply ONLY in {lang_name}. "
            "Start directly with the first step — no greeting, no 'Sure', "
            "no 'Here are', no markdown bold. "
            "Give at most 5 short numbered steps. "
            "Use ONLY the route data provided — never invent routes or fares."
        )},
        {"role": "user", "content": f"Give step-by-step directions:\n\n{summary}"},
    ]
    return ollama.chat(messages, cfg.REPLY_MODEL)


def not_found_reply(message: str, lang: str) -> str:
    lang_name = _LANG_NAME.get(lang, "English")
    messages = [
        {"role": "system", "content": (
            f"You are Namma Route Buddy. Reply ONLY in {lang_name}. Keep it to 2 sentences."
        )},
        {"role": "user", "content": message},
    ]
    return ollama.chat(messages, cfg.REPLY_MODEL)


def ask_for_route(user_text: str, lang: str) -> str:
    lang_name = _LANG_NAME.get(lang, "English")
    messages = [
        {"role": "system", "content": f"You are Namma Route Buddy. Reply ONLY in {lang_name}."},
        {"role": "user",   "content": (
            f"The user said: '{user_text}'. Ask them where they want to go from and to."
        )},
    ]
    return ollama.chat(messages, cfg.REPLY_MODEL)
