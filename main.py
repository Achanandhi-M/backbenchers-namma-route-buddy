import os, json, re
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv
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

class RouteRequest(BaseModel):
    from_place: str
    to_place: str
    lang: Optional[str] = "en-IN"

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
        if t == "metro":
            legs_text.append(
                f"Step {i}: Board {leg.get('line','')} Line {leg.get('direction','')} "
                f"from {leg.get('from','')} to {leg.get('to','')} "
                f"({leg.get('stops',0)} stops, ~{leg.get('time_min',0)} min, ₹{leg.get('fare_inr',0)})"
            )
            if leg.get("is_interchange"):
                legs_text.append(f"  Change lines at {leg.get('interchange_at','')}.")
        elif t == "bus":
            legs_text.append(
                f"Step {i}: Board bus {leg.get('line_display', leg.get('line',''))} "
                f"from {leg.get('from','')} to {leg.get('to','')} (~{leg.get('time_min',0)} min)"
            )
        elif t == "walk":
            legs_text.append(
                f"Step {i}: Walk {leg.get('walk_km',0)} km from {leg.get('from','')} "
                f"to {leg.get('to','')} (~{leg.get('time_min',0)} min)"
            )
        elif t == "auto":
            legs_text.append(
                f"Step {i}: Take auto from {leg.get('from','')} to {leg.get('to','')} "
                f"(~{leg.get('time_min',0)} min, ~₹{leg.get('fare_inr',0)})"
            )

    summary = (
        f"Route: {origin} → {destination} ({option.get('label','')})\n"
        f"Total: ~{option.get('total_time_min',0)} min, ~₹{option.get('total_fare_inr',0)}, "
        f"{option.get('interchange_count',0)} transfer(s)\n\n"
        + "\n".join(legs_text)
    )
    if fallback_note:
        summary += f"\n\nNote: {fallback_note}"

    messages = [
        {"role": "system", "content": (
            f"You are Namma Route Buddy, a warm Bengaluru transit assistant for newcomers. "
            f"Reply ONLY in {lang_name}. Be short, friendly, and step-by-step. "
            "Use ONLY the route data provided — never invent routes or fares."
        )},
        {"role": "user", "content": f"Give friendly step-by-step directions:\n\n{summary}"},
    ]
    return llm.chat(messages, REPLY_MODEL)

# ── Core routing logic ────────────────────────────────────────────────────────
def _get_options(from_place: str, to_place: str, lang: str) -> dict:
    """
    Try Google Maps first; fall back to metro engine.
    Returns: {source, origin, destination, options, fallback_note, newcomer_tip}
    """
    google = routing_google.get_google_routes(from_place, to_place, lang)

    if google:
        google["newcomer_tip"] = ""
        google["fallback_note"] = None
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
    result = _get_options(req.from_place, req.to_place, req.lang)

    if result.get("not_found"):
        lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                     "ta-IN": "Tamil",   "en-IN": "English"}.get(req.lang, "English")
        messages = [
            {"role": "system", "content": f"You are Namma Route Buddy. Reply ONLY in {lang_name}. Keep it to 2 sentences."},
            {"role": "user",   "content": result["not_found"]},
        ]
        reply = llm.chat(messages, REPLY_MODEL)
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    # Narrate the first / "best" option
    best = result["options"][0] if result["options"] else {}
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

    result = _get_options(from_q, to_q, req.lang)
    best = result["options"][0] if result.get("options") else {}
    reply = narrate_option(best, result.get("origin", from_q), result.get("destination", to_q),
                            req.lang, result.get("fallback_note"))

    return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

# ── /places endpoint ──────────────────────────────────────────────────────────
@app.get("/places")
async def places():
    return JSONResponse({"places": autocomplete_list()})

# ── Serve frontend ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(Path("index.html").read_text())
