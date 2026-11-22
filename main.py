import os, json, re
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
import requests as _requests
import llm
import engine
from engine import compute_route, route_to_dict, autocomplete_list
import routing_google

load_dotenv()

app = FastAPI()

INTENT_MODEL = os.getenv("INTENT_MODEL", "granite3.3:2b")
REPLY_MODEL  = os.getenv("REPLY_MODEL",  "granite3.3:2b")

# ── Schema ────────────────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    text: str
    lang: Optional[str] = "en-IN"
    history: Optional[list] = []
    departure_time: Optional[str] = None   # RFC 3339; None = now

class RouteRequest(BaseModel):
    from_place: str
    to_place: str
    lang: Optional[str] = "en-IN"
    departure_time: Optional[str] = None   # RFC 3339; None = now
    preference: Optional[str] = "any"      # "cheapest" | "fastest" | "any"

# ── Intent extraction ─────────────────────────────────────────────────────────
def extract_intent(text: str, history: list) -> dict:
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
            "If preference is not stated use \"any\". "
            "Return JSON only — no explanation, no markdown."
        )},
        {"role": "user", "content": (
            f"Conversation history:\n{history_text}\n\nUser message: {text}"
        )},
    ]

    for _ in range(2):
        try:
            raw = llm.chat(messages, INTENT_MODEL, json_mode=True)
            match = re.search(r'\{.*?\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass
    return {}

# ── Narrate a single route option with Granite ────────────────────────────────
def narrate_option(option: dict, origin: str, destination: str, lang: str, fallback_note: Optional[str]) -> str:
    lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                 "ta-IN": "Tamil",   "en-IN": "English"}.get(lang, "English")

    legs_text = []
    for i, leg in enumerate(option.get("legs", []), 1):
        t = leg.get("type", "")
        # Use fare_label if present, fall back to fare_inr
        fare_str = leg.get("fare_label") or (f"₹{leg.get('fare_inr',0)}" if leg.get("fare_inr") else "")
        # Boarding-point vocabulary based on vehicle type
        from_pt = "metro station" if t == "metro" else "bus stop" if t == "bus" else ""
        from_str = f"{leg.get('from','')} {from_pt}".strip() if from_pt else leg.get("from", "")
        to_str   = f"{leg.get('to','')} {from_pt}".strip()   if from_pt else leg.get("to",   "")
        if t == "metro":
            legs_text.append(
                f"Step {i}: Board {leg.get('line','')} Line {leg.get('direction','')} "
                f"at {from_str}, alight at {to_str} "
                f"({leg.get('stops',0)} stops, ~{leg.get('time_min',0)} min"
                + (f", {fare_str}" if fare_str else "") + ")"
            )
            if leg.get("is_interchange"):
                legs_text.append(f"  Change lines at {leg.get('interchange_at','')}.")
        elif t == "bus":
            legs_text.append(
                f"Step {i}: Board bus {leg.get('line_display', leg.get('line',''))} "
                f"at {from_str}, alight at {to_str} "
                f"(~{leg.get('time_min',0)} min"
                + (f", {fare_str}" if fare_str else "") + ")"
            )
        elif t == "walk":
            legs_text.append(
                f"Step {i}: Walk {leg.get('walk_km',0)} km from {leg.get('from','')} "
                f"to {leg.get('to','')} (~{leg.get('time_min',0)} min)"
            )
        elif t == "auto":
            legs_text.append(
                f"Step {i}: Take auto from {leg.get('from','')} to {leg.get('to','')} "
                f"(~{leg.get('time_min',0)} min"
                + (f", {fare_str}" if fare_str else "") + ")"
            )
        elif t == "cab":
            legs_text.append(
                f"Step {i}: Take a cab from {leg.get('from','')} to {leg.get('to','')} "
                f"(~{leg.get('time_min',0)} min"
                + (f", {fare_str}" if fare_str else "") + ")"
            )

    # Use fare_label at option level if available
    fare_summary = option.get("fare_label") or (f"₹{option.get('total_fare_inr',0)}" if option.get("total_fare_inr") else "—")
    summary = (
        f"Route: {origin} → {destination} ({option.get('label','')})\n"
        f"Total: ~{option.get('total_time_min',0)} min, ~{fare_summary}, "
        f"{option.get('interchange_count',0)} transfer(s)\n\n"
        + "\n".join(legs_text)
    )
    if fallback_note:
        summary += f"\n\nNote: {fallback_note}"

    messages = [
        {"role": "system", "content": (
            f"You are Namma Route Buddy, a Bengaluru transit assistant. "
            f"Reply ONLY in {lang_name}. "
            "Start directly with the first step — no greeting, no 'Sure', no 'Here are', no markdown bold. "
            "Give at most 5 short numbered steps. "
            "Use ONLY the route data provided — never invent routes or fares."
        )},
        {"role": "user", "content": f"Give step-by-step directions:\n\n{summary}"},
    ]
    return llm.chat(messages, REPLY_MODEL)

def _pick_recommended(options: list, preference: str) -> int:
    """
    Return the index of the option to narrate and show selected in the UI.
    - preference="cheapest": index of first option whose badge contains "Cheapest"
    - otherwise: index of first option whose badge contains "Fastest"
    - Falls back to index 0 if neither is found.
    """
    target_badge = "Cheapest" if preference == "cheapest" else "Fastest"
    for i, o in enumerate(options):
        if target_badge in o.get("badge", ""):
            return i
    # No badged option found (e.g. engine fallback with single option)
    return 0

# ── Core routing logic ────────────────────────────────────────────────────────
def _get_options(from_place: str, to_place: str, lang: str, departure_time: Optional[str] = None) -> dict:
    """
    Try Google Maps first; fall back to metro engine.
    Returns: {source, origin, destination, options, fallback_note, newcomer_tip}
      or:    {ambiguous, field, query, suggestions, source}
    """
    google = routing_google.get_google_routes(from_place, to_place, lang, departure_time=departure_time)

    # Pass ambiguity response straight through
    if google and google.get("ambiguous"):
        return google

    if google:
        google.setdefault("newcomer_tip", "")
        google.setdefault("fallback_note", None)
        return google

    # Fallback to metro graph engine
    result = compute_route(from_place, to_place)
    rd = route_to_dict(result)

    if rd.get("not_found"):
        return {
            "source": "engine",
            "origin": from_place,
            "destination": to_place,
            "options": [],
            "fallback_note": None,
            "not_found": rd["not_found"],
            "newcomer_tip": "",
        }

    # Wrap single engine result as a one-option list
    option = {
        "label":             "Metro route",
        "total_time_min":    rd["total_time_min"],
        "total_fare_inr":    rd["total_fare_inr"],
        "interchange_count": rd["interchange_count"],
        "legs":              rd["legs"],
        "distance_m":        0,
    }
    return {
        "source":       "engine",
        "origin":       rd["origin"],
        "destination":  rd["destination"],
        "options":      [option],
        "fallback_note": "Showing metro-only route (Google Maps unavailable).",
        "newcomer_tip":  rd.get("newcomer_tip", ""),
        "not_found":     None,
    }

# ── /route endpoint (structured) ─────────────────────────────────────────────
@app.post("/route")
async def route(req: RouteRequest):
    preference = (req.preference or "any").lower()
    result = _get_options(req.from_place, req.to_place, req.lang, req.departure_time)

    # Ambiguous place — ask user to clarify
    if result.get("ambiguous"):
        field   = result.get("field", "from")
        query   = result.get("query", req.from_place if field == "from" else req.to_place)
        suggs   = result.get("suggestions", [])
        names   = [s["name"] for s in suggs[:3]]
        reply   = f"I found multiple places matching \"{query}\". Did you mean: {', '.join(names) or query}?"
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    if result.get("not_found"):
        lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                     "ta-IN": "Tamil",   "en-IN": "English"}.get(req.lang, "English")
        messages = [
            {"role": "system", "content": f"You are Namma Route Buddy. Reply ONLY in {lang_name}. Keep it to 2 sentences."},
            {"role": "user",   "content": result["not_found"]},
        ]
        reply = llm.chat(messages, REPLY_MODEL)
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    options = result.get("options", [])
    rec_idx = _pick_recommended(options, preference)
    result["recommended_idx"] = rec_idx

    best  = options[rec_idx] if options else {}
    reply = narrate_option(best, result["origin"], result["destination"],
                            req.lang, result.get("fallback_note"))

    return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

# ── /ask endpoint (natural language) ─────────────────────────────────────────
@app.post("/ask")
async def ask(req: AskRequest):
    intent = extract_intent(req.text, req.history or [])
    from_q = intent.get("from", "")
    to_q   = intent.get("to",   "")

    if not from_q or not to_q:
        lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                     "ta-IN": "Tamil",   "en-IN": "English"}.get(req.lang, "English")
        messages = [
            {"role": "system", "content": f"You are Namma Route Buddy. Reply ONLY in {lang_name}."},
            {"role": "user",   "content": f"The user said: '{req.text}'. Ask them where they want to go from and to."},
        ]
        reply = llm.chat(messages, REPLY_MODEL)
        return JSONResponse({"reply": reply, "result": None, "lang": req.lang})

    result = _get_options(from_q, to_q, req.lang, req.departure_time)

    # Ambiguous place — surface to user
    if result.get("ambiguous"):
        field = result.get("field", "from")
        query = result.get("query", from_q if field == "from" else to_q)
        suggs = result.get("suggestions", [])
        names = [s["name"] for s in suggs[:3]]
        reply = f"I found multiple places matching \"{query}\". Did you mean: {', '.join(names) or query}?"
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    if result.get("not_found"):
        lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                     "ta-IN": "Tamil",   "en-IN": "English"}.get(req.lang, "English")
        messages = [
            {"role": "system", "content": f"You are Namma Route Buddy. Reply ONLY in {lang_name}. Keep it to 2 sentences."},
            {"role": "user",   "content": result["not_found"]},
        ]
        reply = llm.chat(messages, REPLY_MODEL)
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    options = result.get("options", [])
    preference = intent.get("preference", "any") or "any"
    rec_idx = _pick_recommended(options, preference)
    result["recommended_idx"] = rec_idx

    best  = options[rec_idx] if options else {}
    reply = narrate_option(best, result.get("origin", from_q), result.get("destination", to_q),
                            req.lang, result.get("fallback_note"))

    return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

# ── /debug/route endpoint (only active when DEBUG=true) ───────────────────────
_DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

@app.get("/debug/route")
async def debug_route_endpoint(
    from_place:     str = Query(..., alias="from"),
    to_place:       str = Query(..., alias="to"),
    departure_time: str = Query(None),
):
    if not _DEBUG_MODE:
        return JSONResponse({"error": "Debug mode is disabled. Set DEBUG=true in .env."}, status_code=403)
    result = routing_google.debug_route(from_place, to_place, departure_time=departure_time or None)
    return JSONResponse(result)

# ── /places endpoint (static list for initial load) ──────────────────────────
@app.get("/places")
async def places():
    return JSONResponse({"places": autocomplete_list()})

# ── /autocomplete endpoint (Places API New proxy) ─────────────────────────────
_PLACES_URL = "https://places.googleapis.com/v1/places:autocomplete"
_PLACES_FIELD_MASK = "suggestions.placePrediction.text,suggestions.placePrediction.placeId"

@app.get("/autocomplete")
async def autocomplete(q: str = Query("", min_length=1)):
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key or not q.strip():
        # Fall back to local fuzzy list
        from difflib import get_close_matches
        all_places = autocomplete_list()
        ql = q.strip().lower()
        matches = [p for p in all_places if ql in p.lower()][:5]
        return JSONResponse({"suggestions": matches, "source": "local"})

    body = {
        "input": q,
        "locationBias": {
            "circle": {
                "center": {"latitude": 12.97, "longitude": 77.59},
                "radius": 30000.0,
            }
        },
        "includedRegionCodes": ["in"],
        "languageCode": "en",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": _PLACES_FIELD_MASK,
    }
    try:
        resp = _requests.post(_PLACES_URL, json=body, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        suggestions = [
            s["placePrediction"]["text"]["text"]
            for s in data.get("suggestions", [])
            if "placePrediction" in s
        ][:5]
        return JSONResponse({"suggestions": suggestions, "source": "google"})
    except Exception:
        # Graceful fallback to local list
        all_places = autocomplete_list()
        ql = q.strip().lower()
        matches = [p for p in all_places if ql in p.lower()][:5]
        return JSONResponse({"suggestions": matches, "source": "local"})

# ── Serve frontend ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(Path("index.html").read_text())
