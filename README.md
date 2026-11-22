# Namma Route Buddy

A voice-first, multilingual transit assistant for newcomers to Bengaluru.
Built for the IBM hackathon by **Team Backbenchers**.

> **v0.1 release (hackathon build):** [github.com/Achanandhi-M/backbenchers-namma-route-buddy/releases/tag/v0.1](https://github.com/Achanandhi-M/backbenchers-namma-route-buddy/releases/tag/v0.1)

---

## Overview

Finding your way around Bengaluru's growing metro network is non-trivial for new residents.
Station names are unfamiliar, interchanges are unmarked, and last-mile connectivity
(getting from a station to your actual destination) is rarely explained in one place.

Namma Route Buddy solves this by letting users ask in plain language — spoken or typed,
in English, Kannada, Hindi, or Tamil — and receiving a complete, step-by-step journey plan:
first-mile guidance, metro legs with line and direction, interchange instructions, and
last-mile auto or walking directions, with fare and time estimates throughout.

---

## Problem

- Newcomers to Bengaluru do not know station names or metro line colours.
- Voice and text queries use colloquial names ("Silk Board", "EC", "KBS") that differ from
  official station names.
- No single tool combines metro route computation, multilingual narration, and voice I/O
  in a lightweight, offline-capable package.

---

## Solution

**v2 (current):** Namma Route Buddy calls the Google Maps Routes API for real-world transit
routing across metro, bus, and rail — returning up to 3 options (Fastest, Cheapest, Fewest
transfers) plus a Direct Auto estimate. IBM Granite narrates the chosen option in the user's
language. If the Google call fails or the key is not configured, the app falls back to the
built-in Namma Metro graph engine transparently, showing a "metro-only route" note.

**v0.1 (hackathon build):** Metro graph engine only (Dijkstra over `data/metro.json`),
covering the full Purple, Green, and Yellow lines with interchange penalties and last-mile
guidance for approximately 40 Bengaluru areas.

---

## Features

| Feature                  | Description                                                                 |
|--------------------------|-----------------------------------------------------------------------------|
| Google Maps routing      | Real-world transit routes via Google Maps Routes API (metro, bus, rail)     |
| Option cards             | Fastest, Cheapest, Fewest transfers, Direct Auto — click to view timeline   |
| Metro graph fallback     | Dijkstra engine over full Purple/Green/Yellow network when Google is absent |
| Last-mile guidance       | Walk or auto directions for ~40 Bengaluru areas and landmarks               |
| Fuzzy name matching      | Handles voice typos and colloquial names via difflib                        |
| Multilingual narration   | Directions in English, Kannada, Hindi, or Tamil via IBM Granite             |
| Voice input              | Web Speech API with language-aware recognition                              |
| Voice output             | Auto-spoken replies via speechSynthesis with a Listen button                |
| Autocomplete             | Station and area name suggestions from the known network                    |
| Response caching         | 10-minute in-memory cache per (from, to, language) to reduce API calls      |
| IBM Carbon design        | Clean, accessible UI following IBM Carbon design principles                 |

---

## Architecture

```
User speaks or types a query
         |
         v
  Web Speech API (browser)
         |  text + language + history
         v
  POST /ask or POST /route  (FastAPI)
         |
         +---> IBM Granite 3.3 (intent extraction)
         |       Extracts {"from", "to", "preference"}
         |       json_mode=True, temperature=0, retry once on parse failure
         |
         +---> routing_google.py  (primary)
         |       POST routes.googleapis.com/directions/v2:computeRoutes
         |       Maps transit steps → internal leg format
         |       Labels options: Fastest / Cheapest / Fewest transfers
         |       Appends Direct Auto estimate
         |       In-memory cache 10 min per (from, to, lang)
         |       On failure / timeout / no routes → falls back to engine.py
         |
         +---> engine.py  (fallback)
         |       Dijkstra over data/metro.json (Purple/Green/Yellow lines)
         |       Alias normalisation + difflib fuzzy matching
         |       Interchange penalty, first/last-mile from data/areas.json
         |
         +---> IBM Granite 3.3 (multilingual narration)
                 "Reply ONLY in {language}. Use only the given route data."
                 Returns friendly step-by-step in the user's language
                         |
                         v
               JSON { reply, result { options [ {label, legs, ...} ] } }
                         |
                         v
               Option cards row (click to switch timeline view)
               Step timeline with per-mode coloured left borders
               speechSynthesis auto-speaks the reply
```

---

## Tech Stack

| Component              | Technology                                            |
|------------------------|-------------------------------------------------------|
| Backend                | Python 3.11, FastAPI, Uvicorn                         |
| Primary routing        | Google Maps Routes API v2 (`routing_google.py`)       |
| Fallback routing       | Pure Python Dijkstra, heapq, difflib (`engine.py`)    |
| LLM                    | IBM Granite 3.3 2B via Ollama (local)                 |
| LLM client             | Custom `llm.py` over Ollama `/api/chat` REST          |
| Frontend               | Vanilla JavaScript, IBM Plex Sans                     |
| Speech I/O             | Web Speech API (SpeechRecognition + synthesis)        |
| Data                   | `data/metro.json` (network), `data/areas.json`        |
| Configuration          | python-dotenv                                         |

---

## How IBM Bob Was Used

IBM Bob (the AI coding assistant) was the primary development accelerator throughout this project:

- **Full-stack scaffolding** — Bob generated the initial FastAPI backend, single-file frontend,
  and data schemas in one pass from a detailed specification, producing working code with no
  manual editing required to run.
- **Route engine design** — Bob designed and implemented `engine.py` from scratch: the weighted
  graph construction from `metro.json`, Dijkstra with interchange penalty, segment merging into
  multi-leg routes, last-mile resolution from `areas.json`, and the `route_to_dict` serialiser.
- **Data authoring** — Bob authored `data/metro.json` (full Namma Metro network with all three
  lines, interchange definitions, and fare slabs) and `data/areas.json` (approximately 40 areas
  with nearest station mappings, last-mile mode, and fare estimates).
- **Iterative stack migration** — When the backend was migrated from IBM watsonx.ai to local
  Ollama, Bob extracted `llm.py`, rewrote all LLM call sites from prompt strings to chat
  messages, and updated all configuration files in a single targeted diff set.
- **UI rebuild** — Bob replaced the original chat-bubble UI with the IBM Carbon-style two-column
  layout, implementing the step timeline with per-mode coloured left borders, the autocomplete
  widget, the summary row, and the newcomer tip notification — all in vanilla JavaScript without
  any framework dependency.
- **Quality assurance** — Bob ran syntax checks on all Python files, validated JSON data files,
  and confirmed no legacy watsonx references remained across the codebase after migration.

---

## Getting Started

### Prerequisites

- Python 3.9 or later
- [Ollama](https://ollama.com) installed and running
- A Google Cloud project with **Routes API** enabled (optional — the app works without it)

### Google Cloud Setup (optional but recommended)

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Create a project (or select an existing one).
3. Enable the **Routes API** (search "Routes API" in the API Library).
4. Under **APIs & Services > Credentials**, create an API Key.
5. Restrict the key: Application restrictions → HTTP referrers or IP addresses.
   API restrictions → Routes API (and optionally Places API New for future use).
6. Copy the key into your `.env` file as `GOOGLE_MAPS_API_KEY=<your key>`.

### 1. Pull the model

```
ollama serve
ollama pull granite3.3:2b
```

### 2. Enter the project directory

```
cd namma-route-buddy
```

### 3. Create a virtual environment and install dependencies

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure environment

```
cp .env.example .env
```

Default `.env` values work with a local Ollama instance:

```
OLLAMA_URL=http://localhost:11434
INTENT_MODEL=granite3.3:2b
REPLY_MODEL=granite3.3:2b
```

### 5. Start the server

```
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` in Chrome or Edge (required for Web Speech API).

---

## Example Queries

Structured form:
- From: "Indiranagar" / To: "Electronic City"
- From: "Manyata Tech Park" / To: "MG Road"
- From: "Airport" / To: "Whitefield"

Natural language (via the question box or microphone):
- "How do I get from Majestic to Whitefield?"
- "Fastest route from Yeshwanthpur to Cubbon Park"
- "How to reach Koramangala from Baiyappanahalli?"

---

## Roadmap

| Priority | Item                                                                 |
|----------|----------------------------------------------------------------------|
| High     | BMTC bus network integration for areas without metro access          |
| High     | Live metro arrival times via BMRCL GTFS-R feed                       |
| Medium   | Additional Indian languages: Telugu, Malayalam                        |
| Medium   | WhatsApp and IVR interface for feature-phone accessibility            |
| Low      | Offline mode with pre-cached route responses                         |
| Low      | Accessibility improvements: screen reader annotations on timeline     |

---

## Team

**Backbenchers**

Built with IBM Bob, IBM Granite, Ollama, FastAPI, and a lot of Bengaluru transit knowledge.
