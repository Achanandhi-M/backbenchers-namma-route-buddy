"""
app/models.py – Pydantic request / response models.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


# ── Requests ──────────────────────────────────────────────────────────────────

class RouteRequest(BaseModel):
    from_place: str
    to_place: str
    lang: Optional[str] = "en-IN"
    departure_time: Optional[str] = None   # RFC 3339; None = now
    preference: Optional[str] = "any"      # "cheapest" | "fastest" | "any"


class AskRequest(BaseModel):
    text: str
    lang: Optional[str] = "en-IN"
    history: Optional[list] = []
    departure_time: Optional[str] = None   # RFC 3339; None = now
