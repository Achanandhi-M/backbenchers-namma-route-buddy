"""
app/llm/ollama.py – Ollama chat client.

Calls POST /api/chat and returns the assistant message content.
"""

import requests
from app.config import cfg


def chat(messages: list[dict], model: str, json_mode: bool = False) -> str:
    """POST to Ollama /api/chat and return the assistant content string."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0},
    }
    if json_mode:
        payload["format"] = "json"

    resp = requests.post(
        f"{cfg.OLLAMA_URL}/api/chat",
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]
