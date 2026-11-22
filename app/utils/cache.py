"""
app/utils/cache.py – Simple in-process TTL cache for route results.
"""

from __future__ import annotations
import time
from typing import Optional
from app.config import cfg


_store: dict[tuple, tuple[float, dict]] = {}


def get(key: tuple) -> Optional[dict]:
    entry = _store.get(key)
    if entry and time.time() < entry[0]:
        return entry[1]
    return None


def set(key: tuple, value: dict) -> None:
    _store[key] = (time.time() + cfg.CACHE_TTL_SECONDS, value)
