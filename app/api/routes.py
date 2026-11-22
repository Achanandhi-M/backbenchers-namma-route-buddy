"""
app/api/routes.py – All HTTP endpoints.

Each endpoint validates input, calls the appropriate service, and returns JSON.
No business logic lives here.
"""

from __future__ import annotations
import os
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.config import cfg
from app.models import RouteRequest, AskRequest
from app.services import intent as intent_svc
from app.services import planner
from app.services import responder
from app.engines import metro_graph, google_routes

router = APIRouter()


# ── /health ───────────────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


# ── /route ────────────────────────────────────────────────────────────────────
@router.post("/route")
async def route(req: RouteRequest):
    preference = (req.preference or "any").lower()
    result     = planner.get_options(req.from_place, req.to_place, req.lang,
                                     req.departure_time)

    if result.get("ambiguous"):
        field = result.get("field", "from")
        query = result.get("query",
                           req.from_place if field == "from" else req.to_place)
        suggs = result.get("suggestions", [])
        names = [s["name"] for s in suggs[:3]]
        reply = (f"I found multiple places matching \"{query}\". "
                 f"Did you mean: {', '.join(names) or query}?")
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    if result.get("not_found"):
        reply = responder.not_found_reply(result["not_found"], req.lang)
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    options = result.get("options", [])
    rec_idx = planner._pick_recommended(options, preference)
    result["recommended_idx"] = rec_idx

    best  = options[rec_idx] if options else {}
    reply = responder.narrate(best, result["origin"], result["destination"],
                               req.lang, result.get("fallback_note"))
    return JSONResponse({"reply": reply, "result": result, "lang": req.lang})


# ── /ask ──────────────────────────────────────────────────────────────────────
@router.post("/ask")
async def ask(req: AskRequest):
    parsed = intent_svc.extract(req.text, req.history or [])
    from_q = parsed.get("from", "")
    to_q   = parsed.get("to",   "")

    if not from_q or not to_q:
        reply = responder.ask_for_route(req.text, req.lang)
        return JSONResponse({"reply": reply, "result": None, "lang": req.lang})

    result = planner.get_options(from_q, to_q, req.lang, req.departure_time)

    if result.get("ambiguous"):
        field = result.get("field", "from")
        query = result.get("query", from_q if field == "from" else to_q)
        suggs = result.get("suggestions", [])
        names = [s["name"] for s in suggs[:3]]
        reply = (f"I found multiple places matching \"{query}\". "
                 f"Did you mean: {', '.join(names) or query}?")
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    if result.get("not_found"):
        reply = responder.not_found_reply(result["not_found"], req.lang)
        return JSONResponse({"reply": reply, "result": result, "lang": req.lang})

    options    = result.get("options", [])
    preference = parsed.get("preference", "any") or "any"
    rec_idx    = planner._pick_recommended(options, preference)
    result["recommended_idx"] = rec_idx

    best  = options[rec_idx] if options else {}
    reply = responder.narrate(best, result.get("origin", from_q),
                               result.get("destination", to_q),
                               req.lang, result.get("fallback_note"))
    return JSONResponse({"reply": reply, "result": result, "lang": req.lang})


# ── /places ───────────────────────────────────────────────────────────────────
@router.get("/places")
async def places():
    return JSONResponse({"places": metro_graph.autocomplete_list()})


# ── /autocomplete ─────────────────────────────────────────────────────────────
_AUTOCOMPLETE_URL   = "https://places.googleapis.com/v1/places:autocomplete"
_AUTOCOMPLETE_MASK  = "suggestions.placePrediction.text,suggestions.placePrediction.placeId"

@router.get("/autocomplete")
async def autocomplete(q: str = Query("", min_length=1)):
    import requests as _req
    all_places = metro_graph.autocomplete_list()
    ql = q.strip().lower()

    if not cfg.GOOGLE_MAPS_API_KEY or not q.strip():
        return JSONResponse({"suggestions": [p for p in all_places if ql in p.lower()][:5],
                              "source": "local"})
    body = {
        "input": q,
        "locationBias": {"circle": {
            "center": {"latitude": cfg.BLARU_LAT, "longitude": cfg.BLARU_LNG},
            "radius": float(cfg.BLARU_RADIUS_M),
        }},
        "includedRegionCodes": ["in"],
        "languageCode": "en",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": cfg.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": _AUTOCOMPLETE_MASK,
    }
    try:
        resp = _req.post(_AUTOCOMPLETE_URL, json=body, headers=headers, timeout=5)
        resp.raise_for_status()
        suggestions = [
            s["placePrediction"]["text"]["text"]
            for s in resp.json().get("suggestions", [])
            if "placePrediction" in s
        ][:5]
        return JSONResponse({"suggestions": suggestions, "source": "google"})
    except Exception:
        return JSONResponse({"suggestions": [p for p in all_places if ql in p.lower()][:5],
                              "source": "local"})


# ── /debug/route ──────────────────────────────────────────────────────────────
@router.get("/debug/route")
async def debug_route_endpoint(
    from_place:     str = Query(..., alias="from"),
    to_place:       str = Query(..., alias="to"),
    departure_time: str = Query(None),
):
    if not cfg.DEBUG:
        return JSONResponse(
            {"error": "Debug mode is disabled. Set DEBUG=true in .env."},
            status_code=403,
        )
    result = google_routes.debug_route(from_place, to_place,
                                       departure_time=departure_time or None)
    return JSONResponse(result)
