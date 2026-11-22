"""
eval_routes.py – Manual evaluation script for Namma Route Buddy routing engine.

Usage (from the project root with the server running):
    python tests/eval_routes.py [--url http://localhost:8000] [--time HH:MM]

Options:
    --url   Server base URL (default: http://localhost:8000)
    --time  Departure time as HH:MM in IST (e.g. "10:00" = next 10 AM IST).
            Defaults to now.

Runs 5 reference corridors through /route and prints a summary table.
No assertions — human review required. Exit code 0 always.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# IST = UTC+05:30
_IST = timezone(timedelta(hours=5, minutes=30))


def _parse_time_arg(hhmm: str) -> str:
    """
    Convert "HH:MM" (IST) to an RFC 3339 UTC string for the next occurrence of
    that time (today if still in the future, tomorrow otherwise).
    """
    h, m = int(hhmm.split(":")[0]), int(hhmm.split(":")[1])
    now_ist = datetime.now(_IST)
    candidate = now_ist.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_ist:
        candidate += timedelta(days=1)
    # Convert to UTC RFC 3339
    utc_dt = candidate.astimezone(timezone.utc)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


CORRIDORS = [
    {
        "id":   "hsr_silk",
        "from": "HSR Layout",
        "to":   "Silk Board",
        "note": "Short hop, should offer Walk / Auto / Bus",
    },
    {
        "id":   "silk_indiranagar",
        "from": "Silk Board",
        "to":   "Indiranagar",
        "note": "Purple Line metro segment",
    },
    {
        "id":   "majestic_whitefield",
        "from": "Majestic",
        "to":   "Whitefield",
        "note": "Classic Purple Line end-to-end",
    },
    {
        "id":   "koramangala_mg_road",
        "from": "Koramangala",
        "to":   "MG Road",
        "note": "Bus-heavy corridor, no direct metro",
    },
    {
        "id":   "airport_majestic",
        "from": "Kempegowda International Airport",
        "to":   "Majestic",
        "note": "Airport connector – Yellow Line / bus mix",
    },
]


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def eval_corridor(base_url: str, c: dict, departure_time: str) -> dict:
    url = f"{base_url}/route"
    payload = {
        "from_place":     c["from"],
        "to_place":       c["to"],
        "lang":           "en-IN",
        "departure_time": departure_time,
    }

    t0 = time.time()
    try:
        data = _post(url, payload)
    except urllib.error.URLError as e:
        return {"id": c["id"], "error": str(e), "latency_ms": 0}
    latency = round((time.time() - t0) * 1000)

    result  = data.get("result") or {}
    options = result.get("options", [])

    return {
        "id":           c["id"],
        "from":         c["from"],
        "to":           c["to"],
        "note":         c["note"],
        "latency_ms":   latency,
        "source":       result.get("source", "—"),
        "origin":       result.get("origin", "—"),
        "destination":  result.get("destination", "—"),
        "ambiguous":    result.get("ambiguous", False),
        "not_found":    result.get("not_found"),
        "option_count": len(options),
        "options": [
            {
                "label":      o.get("label"),
                "time_min":   o.get("total_time_min"),
                "fare":       o.get("fare_label") or o.get("total_fare_inr"),
                "transfers":  o.get("interchange_count"),
                "modes":      list({l["type"] for l in o.get("legs", [])}),
            }
            for o in options
        ],
        "reply_snippet": (data.get("reply") or "")[:120],
    }


def main():
    parser = argparse.ArgumentParser(description="Namma Route Buddy – route eval")
    parser.add_argument("--url",  default="http://localhost:8000", help="Server base URL")
    parser.add_argument("--time", default=None, metavar="HH:MM",
                        help="Departure time in IST (e.g. 10:00). Defaults to now.")
    args = parser.parse_args()

    if args.time:
        departure_time = _parse_time_arg(args.time)
        # Display in IST for the header
        utc_dt   = datetime.strptime(departure_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ist_disp = utc_dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M IST")
    else:
        departure_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ist_disp = datetime.now(_IST).strftime("%Y-%m-%d %H:%M IST (now)")

    print(f"\n{'='*72}")
    print(f"  Namma Route Buddy – Route Evaluation  ({args.url})")
    print(f"  Departure : {ist_disp}")
    print(f"{'='*72}\n")

    results = []
    for c in CORRIDORS:
        sys.stdout.write(f"  [{c['id']}] {c['from']} → {c['to']} … ")
        sys.stdout.flush()
        r = eval_corridor(args.url, c, departure_time)
        results.append(r)
        status = "✗ ERROR" if r.get("error") else ("⚠ AMBIGUOUS" if r.get("ambiguous") else f"✓ {r['option_count']} options")
        print(f"{status}  ({r['latency_ms']} ms)")

    print(f"\n{'─'*72}")
    for r in results:
        print(f"\n▸ {r['id']}: {r.get('from','?')} → {r.get('to','?')}")
        print(f"  Note    : {r.get('note','')}")
        print(f"  Source  : {r.get('source','—')}  |  Latency: {r['latency_ms']} ms")
        if r.get("error"):
            print(f"  ERROR   : {r['error']}")
            continue
        if r.get("ambiguous"):
            print(f"  AMBIGUOUS – field: {r.get('field','?')}")
            continue
        if r.get("not_found"):
            print(f"  NOT FOUND: {r['not_found']}")
        print(f"  Resolved: {r.get('origin','?')} → {r.get('destination','?')}")
        for o in r.get("options", []):
            modes = "+".join(sorted(o["modes"]))
            print(f"    [{o['label']:<26}] {str(o['time_min'])+'min':>8}  {str(o['fare']):>22}  {o['transfers']} xfer  modes={modes}")
        print(f"  Reply   : {r.get('reply_snippet','')!r}")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    main()
