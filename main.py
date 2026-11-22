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

# ── LLM-narrated reply ────────────────────────────────────────────────────────
def narrate_route(route: dict, lang: str) -> str:
    lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                 "ta-IN": "Tamil",   "en-IN": "English"}.get(lang, "English")

    if route.get("not_found"):
        messages = [
            {"role": "system", "content": (
                f"You are Namma Route Buddy, a helpful Bengaluru transit assistant. "
                f"Reply ONLY in {lang_name}. Keep it to 2 sentences."
            )},
            {"role": "user", "content": route["not_found"]},
        ]
        return llm.chat(messages, REPLY_MODEL)

    legs_text = []
    for i, leg in enumerate(route["legs"], 1):
        if leg["type"] == "metro":
            legs_text.append(
                f"Step {i}: Board {leg['line']} Line {leg['direction']} from {leg['from']} "
                f"to {leg['to']} ({leg['stops']} stops, ~{leg['time_min']} min, ₹{leg['fare_inr']})"
            )
            if leg.get("is_interchange"):
                legs_text.append(f"  Change lines at {leg['interchange_at']}.")
        elif leg["type"] == "walk":
            legs_text.append(
                f"Step {i}: Walk {leg['walk_km']} km from {leg['from']} to {leg['to']} (~{leg['time_min']} min)"
            )
        elif leg["type"] == "auto":
            legs_text.append(
                f"Step {i}: Take auto from {leg['from']} to {leg['to']} (~{leg['time_min']} min, ~₹{leg['fare_inr']})"
            )

    summary = (
        f"Route: {route['origin']} → {route['destination']}\n"
        f"Total: ~{route['total_time_min']} min, ~₹{route['total_fare_inr']}, "
        f"{route['interchange_count']} interchange(s)\n\n"
        + "\n".join(legs_text)
        + f"\n\nNewcomer tip: {route['newcomer_tip']}"
    )

    messages = [
        {"role": "system", "content": (
            f"You are Namma Route Buddy, a warm Bengaluru transit assistant for newcomers. "
            f"Reply ONLY in {lang_name}. Be short, friendly, and step-by-step. "
            "Use ONLY the route data provided — never invent routes or fares."
        )},
        {"role": "user", "content": (
            f"Give friendly step-by-step directions for this route:\n\n{summary}"
        )},
    ]
    return llm.chat(messages, REPLY_MODEL)

# ── /ask endpoint (voice/text, natural language) ──────────────────────────────
@app.post("/ask")
async def ask(req: AskRequest):
    intent = extract_intent(req.text, req.history or [])
    from_q = intent.get("from", "")
    to_q   = intent.get("to",   "")

    if not from_q or not to_q:
        lang_name = {"kn-IN": "Kannada", "hi-IN": "Hindi",
                     "ta-IN": "Tamil",   "en-IN": "English"}.get(req.lang, "English")
        messages = [
            {"role": "system", "content": (
                f"You are Namma Route Buddy. Reply ONLY in {lang_name}."
            )},
            {"role": "user", "content": (
                f"The user said: '{req.text}'. Ask them where they want to go from and to."
            )},
        ]
        reply = llm.chat(messages, REPLY_MODEL)
        return JSONResponse({"reply": reply, "route": None, "lang": req.lang})

    result = compute_route(from_q, to_q)
    route_dict = route_to_dict(result)
    reply = narrate_route(route_dict, req.lang)

    return JSONResponse({
        "reply": reply,
        "route": route_dict,
        "lang": req.lang,
    })

# ── /route endpoint (structured, from UI form) ────────────────────────────────
@app.post("/route")
async def route(req: RouteRequest):
    result = compute_route(req.from_place, req.to_place)
    route_dict = route_to_dict(result)
    reply = narrate_route(route_dict, req.lang)
    return JSONResponse({"reply": reply, "route": route_dict, "lang": req.lang})

# ── /places endpoint (autocomplete) ──────────────────────────────────────────
@app.get("/places")
async def places():
    return JSONResponse({"places": autocomplete_list()})

# ── Serve frontend ─────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(Path("index.html").read_text())
