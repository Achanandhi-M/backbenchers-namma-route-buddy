"""
app/config.py – All settings loaded from environment variables.

Import `cfg` to access any setting. No os.getenv calls anywhere else.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class _Config:
    # ── LLM ───────────────────────────────────────────────────────────────────
    # Base URL of a running Ollama instance
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    # Model used for intent extraction (JSON mode)
    INTENT_MODEL: str = os.getenv("INTENT_MODEL", "granite3.3:2b")
    # Model used for generating the reply in the user's language
    REPLY_MODEL: str = os.getenv("REPLY_MODEL", "granite3.3:2b")

    # ── Google Maps ───────────────────────────────────────────────────────────
    # API key with Routes API v2 and Places API (New) enabled
    GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")

    # ── Route cache ───────────────────────────────────────────────────────────
    # In-memory cache TTL in seconds (10 minutes)
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "600"))

    # ── Debug ─────────────────────────────────────────────────────────────────
    # Set to "true" to enable the /debug/route endpoint and verbose step logging
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # ── Location bias (Bengaluru city centre) ─────────────────────────────────
    BLARU_LAT: float = 12.97
    BLARU_LNG: float = 77.59
    BLARU_RADIUS_M: int = 30_000


cfg = _Config()
