"""
app/llm/rule_based.py – Offline fallback when Ollama is unavailable.

Provides simple template replies so the app degrades gracefully without a model.
"""

from __future__ import annotations

_LANG_NAME = {
    "en-IN": "English",
    "kn-IN": "Kannada",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
}

# Simple English-only templates; returned when Ollama raises an exception.
_TEMPLATES = {
    "ask_origin_destination": (
        "Where would you like to go, and from where?"
    ),
    "not_found": (
        "Sorry, I couldn't find a route for that journey. "
        "Please try a nearby landmark or metro station name."
    ),
    "generic_error": (
        "I wasn't able to compute a route right now. Please try again."
    ),
}


def fallback_reply(key: str) -> str:
    return _TEMPLATES.get(key, _TEMPLATES["generic_error"])
