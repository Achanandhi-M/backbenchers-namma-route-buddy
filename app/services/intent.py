"""
app/services/intent.py – Extract structured travel intent from free text.

Uses Granite via Ollama to parse "from", "to", and "preference" out of a
natural-language query, using recent conversation history for follow-ups.
"""

from __future__ import annotations
import json, re
from typing import Optional

from app.config import cfg
from app.llm import ollama


def extract(text: str, history: list) -> dict:
    """
    Returns {"from": str, "to": str, "preference": "cheapest|fastest|any"}.
    Returns {} when the model output cannot be parsed.
    """
    history_text = "\n".join(
        f"User: {h['user']}\nAssistant: {h['assistant']}"
        for h in history[-3:]
    ) if history else ""

    messages = [
        {"role": "system", "content": (
            "You are a Bengaluru transit assistant. "
            "Extract travel intent and return ONLY valid JSON with keys: "
            '"from", "to", "preference" (cheapest|fastest|any). '
            "Use lowercase place names from the user message. "
            'If preference is not stated use "any". '
            "Return JSON only — no explanation, no markdown."
        )},
        {"role": "user", "content": (
            f"Conversation history:\n{history_text}\n\nUser message: {text}"
        )},
    ]

    for _ in range(2):
        try:
            raw   = ollama.chat(messages, cfg.INTENT_MODEL, json_mode=True)
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
    return {}
