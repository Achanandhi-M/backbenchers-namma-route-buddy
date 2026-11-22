# Namma Route Buddy

A voice-first, multilingual transit assistant for Bengaluru newcomers. Ask your route in English, Kannada, Hindi, or Tamil; get spoken, step-by-step directions powered by IBM Granite and real-time Google Maps.

[![Watch the demo](docs/screenshot.png)](https://youtu.be/hOUCWMeTD_w)

Built for the IBM Granite Hackathon 2026.

---

## Features

- **Voice input** via the Web Speech API — speak your journey, hear the answer back
- **Multilingual** — English, Kannada, Hindi, Tamil (kn-IN, hi-IN, ta-IN, en-IN)
- **Free-text questions** — "How do I get from Silk Board to Whitefield cheapest?"
- **Follow-up context** — conversation history passed to the LLM for multi-turn queries
- **Route options** — fastest, cheapest, and recommended options with fare and time
- **Step timeline** — per-leg breakdown: metro, bus, walk, auto — colour-coded by mode
- **Newcomer tips** — practical city advice attached to each corridor
- **Dual routing engine** — built-in metro graph (offline-capable) or Google Maps (live)
- **Autocomplete** — station and area suggestions as you type
- **IBM Granite LLM** — Granite 3.3:2b via Ollama for intent extraction and reply generation

---

## How It Works

```
User speaks / types
       │
       ▼
  POST /ask  ──► intent.extract()      (LLM call 1 — JSON: from, to, preference)
       │
       ▼
  planner.get_options()                (metro graph or Google Maps)
       │
       ▼
  responder.narrate()                  (LLM call 2 — friendly directions in chosen language)
       │
       ▼
  {reply, result}  ──► browser speaks the reply aloud
```

The LLM never invents route data. It only extracts structured intent and reformats verified route data into natural language.

---

## Getting Started

**Prerequisites**

- Python 3.10+
- [Ollama](https://ollama.ai) running locally with a Granite model pulled:
  ```
  ollama pull granite3.3:2b
  ```
- (Optional) A Google Maps Platform API key with Directions and Places enabled

**Install**

```bash
git clone <repo-url>
cd namma-route-buddy
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Configure**

```bash
cp .env.example .env
# Edit .env — set OLLAMA_URL and optionally GOOGLE_MAPS_API_KEY
```

**Run**

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000` in Chrome or Edge (required for Web Speech API).

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server base URL |
| `INTENT_MODEL` | `granite3.3:2b` | Model used for intent extraction (LLM call 1) |
| `REPLY_MODEL` | `granite3.3:2b` | Model used for reply generation (LLM call 2) |
| `GOOGLE_MAPS_API_KEY` | _(empty)_ | Enables live Google Maps routing; leave blank for metro graph |
| `CACHE_TTL_SECONDS` | `300` | How long to cache route results (0 = no cache) |
| `DEBUG` | `false` | Enables `GET /debug/route` diagnostic endpoint |

---

## Route Evaluation

Run the evaluation script against a live server to validate the routing engine across five reference corridors:

```bash
# Default: runs against http://localhost:8000, departure = now
python tests/eval_routes.py

# Custom server and departure time (IST)
python tests/eval_routes.py --url http://localhost:8000 --time 09:00
```

No assertions are made — output is a human-readable table for manual review. Exit code is always 0.

---

## Project Structure

```
namma-route-buddy/
├── app/
│   ├── main.py               FastAPI factory; mounts /css, /js; serves web/index.html
│   ├── config.py             All settings read from environment
│   ├── models.py             Pydantic request models
│   ├── api/
│   │   └── routes.py         All API endpoints: /route, /ask, /places, /autocomplete
│   ├── engines/
│   │   ├── metro_graph.py    Dijkstra over metro.json — offline, no API key needed
│   │   └── google_routes.py  Google Maps Directions API — live, requires API key
│   ├── llm/
│   │   ├── ollama.py         Ollama chat client
│   │   └── rule_based.py     Template fallback when Ollama is unavailable
│   ├── services/
│   │   ├── intent.py         extract() — LLM call 1, returns {from, to, preference}
│   │   ├── planner.py        get_options() — dispatches to the active routing engine
│   │   └── responder.py      narrate() — LLM call 2, formats reply in user's language
│   └── utils/
│       └── cache.py          Simple TTL cache keyed on route parameters
├── web/
│   ├── index.html            Slim HTML shell
│   ├── css/styles.css        All styling
│   └── js/
│       ├── app.js            UI logic, rendering, event wiring (ES module)
│       ├── api.js            Fetch wrappers for all backend endpoints
│       └── voice.js          Web Speech API: mic input + speech synthesis
├── data/
│   ├── metro.json            Bengaluru metro graph (stations, lines, fares)
│   └── areas.json            Neighbourhoods and landmarks for autocomplete
├── tests/
│   └── eval_routes.py        Manual route evaluation script (5 corridors)
├── docs/
│   └── screenshot.png        UI screenshot placeholder
├── requirements.txt
├── .env.example
└── README.md
```

---

## Roadmap

- Real BMTC bus schedule integration (GTFS feed)
- Namma Metro real-time arrivals via official API
- Persistent conversation sessions (Valkey)
- PWA offline mode with cached metro graph
- More Indian languages (Telugu, Malayalam)

---

## Hackathon Background

Namma Route Buddy was built for the **IBM Granite Hackathon 2024**. The goal was to explore whether a small, locally-run LLM (IBM Granite 3.3:2b via Ollama) could provide genuinely useful transit guidance in multiple Indian languages without requiring any cloud API for inference.

The LLM is used only for two narrow tasks — structured intent extraction and friendly reply narration — keeping the routing logic deterministic and verifiable. This architecture means the app degrades gracefully: if Ollama is unavailable, rule-based fallbacks handle both tasks.

---

## Credits

- Routing data: [Google Maps Platform](https://developers.google.com/maps) (live mode) / built-in metro graph (offline mode)
- LLM inference: [IBM Granite](https://www.ibm.com/granite) via [Ollama](https://ollama.ai)
- UI design system inspired by [Carbon Design System](https://carbondesignsystem.com)
- Metro network data: [Namma Metro](https://english.bmrcl.in/)
