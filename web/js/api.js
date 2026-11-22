// web/js/api.js – All backend fetch calls.
// Exports: fetchPlaces, fetchAutocomplete, fetchRoute, fetchAsk

export async function fetchPlaces() {
  const r = await fetch('/places');
  const d = await r.json();
  return d.places || [];
}

export async function fetchAutocomplete(q) {
  const r = await fetch(`/autocomplete?q=${encodeURIComponent(q)}`);
  const d = await r.json();
  return (d.suggestions || []).slice(0, 5);
}

export async function fetchRoute(from, to, lang) {
  const r = await fetch('/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ from_place: from, to_place: to, lang }),
  });
  return r.json();
}

export async function fetchAsk(text, lang, history) {
  const r = await fetch('/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, lang, history }),
  });
  return r.json();
}
