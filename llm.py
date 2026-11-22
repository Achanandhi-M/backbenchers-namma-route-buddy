import os, json, requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")


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
        f"{OLLAMA_URL}/api/chat",
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]
