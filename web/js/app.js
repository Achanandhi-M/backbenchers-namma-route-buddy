// web/js/app.js – UI logic: rendering, event wiring, autocomplete.
// Uses api.js for backend calls and voice.js for speech.

import { fetchPlaces, fetchAutocomplete, fetchRoute, fetchAsk } from './api.js';
import { speakText, initMic } from './voice.js';

// ── State ─────────────────────────────────────────────────────────────────────
let allPlaces       = [];
let conversationHistory = [];
let _currentResult  = null;
let _currentReply   = '';
let _activeOptIdx   = 0;

// ── Boot ──────────────────────────────────────────────────────────────────────
(async () => {
  try { allPlaces = await fetchPlaces(); } catch {}
})();

// ── DOM refs ──────────────────────────────────────────────────────────────────
const fromInput     = document.getElementById('fromInput');
const toInput       = document.getElementById('toInput');
const fromList      = document.getElementById('fromList');
const toList        = document.getElementById('toList');
const swapBtn       = document.getElementById('swapBtn');
const searchBtn     = document.getElementById('searchBtn');
const micBtn        = document.getElementById('micBtn');
const micStatus     = document.getElementById('micStatus');
const questionInput = document.getElementById('questionInput');
const questionBtn   = document.getElementById('questionBtn');
const langSelect    = document.getElementById('langSelect');
const emptyState    = document.getElementById('emptyState');
const loadingState  = document.getElementById('loadingState');
const resultPanel   = document.getElementById('resultPanel');
const fallbackNote  = document.getElementById('fallbackNote');
const disambigCard  = document.getElementById('disambigCard');
const optionsRow    = document.getElementById('optionsRow');
const replyText     = document.getElementById('replyText');
const listenBtn     = document.getElementById('listenBtn');
const tipBox        = document.getElementById('tipBox');
const tipText       = document.getElementById('tipText');
const timeline      = document.getElementById('timeline');
const errorBox      = document.getElementById('errorBox');
const fromResolved  = document.getElementById('fromResolved');
const toResolved    = document.getElementById('toResolved');

// ── Swap ──────────────────────────────────────────────────────────────────────
swapBtn.addEventListener('click', () => {
  [fromInput.value, toInput.value] = [toInput.value, fromInput.value];
});

// ── Autocomplete ──────────────────────────────────────────────────────────────
function setupAutocomplete(input, list) {
  let activeIdx = -1;
  let debounceTimer = null;

  async function getSuggestions(q) {
    if (q.length <= 2) {
      const local = allPlaces.filter(p => p.toLowerCase().includes(q.toLowerCase())).slice(0, 5);
      if (local.length) return local;
    }
    try {
      return await fetchAutocomplete(q);
    } catch {
      return allPlaces.filter(p => p.toLowerCase().includes(q.toLowerCase())).slice(0, 5);
    }
  }

  function renderList(names) {
    list.innerHTML = ''; activeIdx = -1;
    if (!names.length) { list.classList.remove('open'); return; }
    names.forEach(name => {
      const item = document.createElement('div');
      item.className = 'autocomplete-item';
      item.setAttribute('role', 'option');
      item.textContent = name;
      item.addEventListener('mousedown', e => {
        e.preventDefault(); input.value = name; list.classList.remove('open');
      });
      list.appendChild(item);
    });
    list.classList.add('open');
  }

  input.addEventListener('input', () => {
    const q = input.value.trim();
    list.innerHTML = ''; activeIdx = -1;
    if (q.length < 1) { list.classList.remove('open'); return; }
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => { renderList(await getSuggestions(q)); }, 300);
  });

  input.addEventListener('keydown', e => {
    const items = list.querySelectorAll('.autocomplete-item');
    if (!items.length) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, items.length - 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, -1); }
    else if (e.key === 'Enter' && activeIdx >= 0) {
      input.value = items[activeIdx].textContent; list.classList.remove('open'); return;
    } else if (e.key === 'Escape') { list.classList.remove('open'); return; }
    items.forEach((el, i) => el.classList.toggle('active', i === activeIdx));
  });

  input.addEventListener('blur', () => { setTimeout(() => list.classList.remove('open'), 150); });
}
setupAutocomplete(fromInput, fromList);
setupAutocomplete(toInput,   toList);

// ── UI state helpers ──────────────────────────────────────────────────────────
function showLoading() {
  emptyState.style.display = 'none';
  loadingState.classList.add('visible');
  resultPanel.classList.remove('visible');
  errorBox.style.display = 'none';
}
function showResult() {
  emptyState.style.display = 'none';
  loadingState.classList.remove('visible');
  resultPanel.classList.add('visible');
}

// ── Mode styling ──────────────────────────────────────────────────────────────
function modeColor(type, line) {
  if (type === 'metro') {
    const c = { Purple: '#8a3ffc', PurpleEast: '#8a3ffc', Green: '#198038', Yellow: '#f1c21b' };
    return c[line] || '#8a3ffc';
  }
  if (type === 'bus')   return '#198038';
  if (type === 'train') return '#6929c4';
  if (type === 'walk')  return '#8d8d8d';
  if (type === 'auto')  return '#ff832b';
  if (type === 'cab')   return '#e8740c';
  return '#8d8d8d';
}

function modeLabel(leg) {
  const t = leg.type;
  if (t === 'metro') return (leg.line === 'PurpleEast' ? 'Purple' : (leg.line || 'Metro')) + ' Line';
  if (t === 'bus')   return leg.line_display ? `Bus ${leg.line_display}` : 'Bus';
  if (t === 'train') return 'Train';
  if (t === 'walk')  return 'Walk';
  if (t === 'auto')  return 'Auto';
  if (t === 'cab')   return 'Cab';
  return t;
}

function svgForMode(type) {
  if (type === 'metro' || type === 'train')
    return `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><rect x="1" y="2" width="10" height="7" rx="2" stroke="currentColor" stroke-width="1.3"/><circle cx="3.5" cy="8.5" r="1" fill="currentColor"/><circle cx="8.5" cy="8.5" r="1" fill="currentColor"/><path d="M1 6h10" stroke="currentColor" stroke-width="1.2"/></svg>`;
  if (type === 'bus')
    return `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><rect x="1" y="2" width="10" height="7" rx="1" stroke="currentColor" stroke-width="1.3"/><circle cx="3" cy="9" r="1" fill="currentColor"/><circle cx="9" cy="9" r="1" fill="currentColor"/><path d="M1 5h10M5 2v3M7 2v3" stroke="currentColor" stroke-width="1.1"/></svg>`;
  if (type === 'walk')
    return `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="2" r="1.2" fill="currentColor"/><path d="M4 5l1.5-2 2 2.5-1 2 2 2.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M5.5 7L4 10" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>`;
  if (type === 'auto' || type === 'cab')
    return `<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M2 8V5l2-3h4l2 3v3H2z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><circle cx="3.5" cy="8.5" r="1" fill="currentColor"/><circle cx="8.5" cy="8.5" r="1" fill="currentColor"/></svg>`;
  return '';
}

// ── Option cards ──────────────────────────────────────────────────────────────
function renderOptionCards(options, activeIdx, onSelect) {
  optionsRow.innerHTML = '';
  options.forEach((opt, i) => {
    const isAutoStyle = opt.legs && opt.legs.some(l => l.type === 'auto' || l.type === 'cab');
    const card = document.createElement('div');
    card.className = 'option-card' + (isAutoStyle ? ' auto-card' : '') + (i === activeIdx ? ' active' : '');
    card.setAttribute('role', 'button');
    card.setAttribute('tabindex', '0');
    const fareStr = opt.fare_label || (opt.total_fare_inr ? `\u20B9${opt.total_fare_inr}` : '\u2014');
    const xferStr = opt.interchange_count === 0 ? 'Direct'
      : `${opt.interchange_count} change${opt.interchange_count > 1 ? 's' : ''}`;
    const badge  = opt.badge || '';
    const rname  = opt.route_name || opt.label || '';
    const labelHtml = badge
      ? `<div class="option-badge">${badge}</div><div class="option-label">${rname}</div>`
      : `<div class="option-label">${rname}</div>`;
    card.innerHTML = `
      ${labelHtml}
      <div class="option-time">${opt.total_time_min} min</div>
      <div class="option-meta">${fareStr} &nbsp;\u00B7&nbsp; ${xferStr}</div>
      ${isAutoStyle ? '<div class="auto-approx">APPROX</div>' : ''}`;
    card.addEventListener('click', () => onSelect(i));
    card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') onSelect(i); });
    optionsRow.appendChild(card);
  });
}

// ── Timeline ──────────────────────────────────────────────────────────────────
function renderTimeline(option) {
  timeline.innerHTML = '';
  const legs = option.legs || [];
  legs.forEach((leg, i) => {
    const isLast  = i === legs.length - 1;
    const color   = modeColor(leg.type, leg.line);
    const label   = modeLabel(leg);
    const icon    = svgForMode(leg.type);
    const lineNum = (leg.type === 'bus' && leg.line_display)
      ? `<span class="tl-line-num">${leg.line_display}</span>` : '';

    const metaParts = [`${leg.time_min} min`];
    if (leg.fare_label)    metaParts.push(leg.fare_label);
    else if (leg.fare_inr) metaParts.push(`\u20B9${leg.fare_inr}`);
    if (leg.type === 'metro' || leg.type === 'bus') metaParts.push(`${leg.stops} stops`);
    if (leg.walk_km) metaParts.push(`${leg.walk_km} km`);

    const desc = (leg.from && leg.to)
      ? `${leg.from} &rarr; ${leg.to}${leg.direction ? ` <span style="color:#525252">(${leg.direction})</span>` : ''}`
      : (leg.instruction || '');

    const step = document.createElement('div');
    step.className = 'tl-step';
    step.innerHTML = `
      <div class="tl-line-wrap">
        <div class="tl-dot" style="border-color:${color};background:${color}"></div>
        ${!isLast ? `<div class="tl-connector" style="background:${color}33"></div>` : ''}
      </div>
      <div class="tl-body">
        <div class="tl-mode-badge" style="border-left-color:${color};color:${color}">
          ${icon} ${label} ${lineNum}
        </div>
        <div class="tl-desc">${desc}</div>
        <div class="tl-meta">${metaParts.join(' \u00B7 ')}</div>
        ${leg.is_interchange
          ? `<div class="tl-interchange"><svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M2 6h8M7 3l3 3-3 3" stroke="#0f62fe" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg> Change at ${leg.interchange_at}</div>`
          : ''}
      </div>`;
    timeline.appendChild(step);
  });
}

// ── Full render ───────────────────────────────────────────────────────────────
function renderResult(result, reply) {
  _currentResult = result;
  _currentReply  = reply;
  _activeOptIdx  = (typeof result.recommended_idx === 'number') ? result.recommended_idx : 0;

  const rf = result.resolved_from;
  const rt = result.resolved_to;
  fromResolved.textContent = (rf && rf.display_name && rf.display_name !== fromInput.value) ? rf.display_name : '';
  toResolved.textContent   = (rt && rt.display_name && rt.display_name !== toInput.value)   ? rt.display_name : '';

  disambigCard.style.display = 'none';

  if (result.fallback_note) {
    fallbackNote.textContent = result.fallback_note;
    fallbackNote.style.display = 'block';
  } else {
    fallbackNote.style.display = 'none';
  }

  renderOptionCards(result.options, _activeOptIdx, selectOption);
  replyText.textContent = reply;

  if (result.newcomer_tip) {
    tipText.textContent = result.newcomer_tip;
    tipBox.style.display = 'block';
  } else {
    tipBox.style.display = 'none';
  }

  if (result.options && result.options.length) {
    renderTimeline(result.options[_activeOptIdx]);
  }

  errorBox.style.display = 'none';
  showResult();
}

function selectOption(idx) {
  if (!_currentResult || !_currentResult.options[idx]) return;
  _activeOptIdx = idx;
  renderOptionCards(_currentResult.options, idx, selectOption);
  renderTimeline(_currentResult.options[idx]);
}

function renderDisambig(result, reply, field) {
  const suggs = result.suggestions || [];
  disambigCard.innerHTML = `
    <div class="disambig-title">\u26A0\uFE0F ${reply}</div>
    ${suggs.slice(0, 3).map(s =>
      `<span class="disambig-suggestion" data-name="${s.name}">${s.name}${s.address
        ? ` <span style="color:#525252;font-size:0.75rem">\u2013 ${s.address.replace(/, India$/, '')}</span>`
        : ''}</span>`
    ).join('')}`;
  disambigCard.querySelectorAll('.disambig-suggestion').forEach(el => {
    el.addEventListener('click', () => {
      const name = el.dataset.name;
      if (field === 'from') { fromInput.value = name; fromResolved.textContent = ''; }
      else                  { toInput.value   = name; toResolved.textContent   = ''; }
      disambigCard.style.display = 'none';
    });
  });
  disambigCard.style.display = 'block';
  fallbackNote.style.display = 'none';
  optionsRow.innerHTML  = '';
  replyText.textContent = reply;
  tipBox.style.display  = 'none';
  timeline.innerHTML    = '';
  errorBox.style.display = 'none';
  showResult();
}

function renderError(msg) {
  errorBox.textContent = msg;
  errorBox.style.display = 'block';
  optionsRow.innerHTML  = '';
  replyText.textContent = '';
  tipBox.style.display  = 'none';
  timeline.innerHTML    = '';
  fallbackNote.style.display = 'none';
  disambigCard.style.display = 'none';
  fromResolved.textContent = '';
  toResolved.textContent   = '';
  showResult();
}

// ── Route search ──────────────────────────────────────────────────────────────
async function doRoute() {
  const from = fromInput.value.trim();
  const to   = toInput.value.trim();
  if (!from || !to) { micStatus.textContent = 'Enter both a start and end location.'; return; }

  micStatus.textContent = '';
  searchBtn.disabled = true;
  showLoading();

  try {
    const data = await fetchRoute(from, to, langSelect.value);
    searchBtn.disabled = false;

    if (data.result && data.result.ambiguous) {
      renderDisambig(data.result, data.reply, data.result.field);
      speakText(data.reply, langSelect.value);
    } else if (data.result && data.result.not_found) {
      renderError(data.result.not_found);
      speakText(data.reply || data.result.not_found, langSelect.value);
    } else if (data.result && data.result.options && data.result.options.length) {
      renderResult(data.result, data.reply);
      speakText(data.reply, langSelect.value);
    } else {
      renderError('Could not compute a route. Please try different locations.');
    }
  } catch {
    searchBtn.disabled = false;
    renderError('Could not reach the server. Please check that the app is running.');
  }
}

// ── Free-text question ────────────────────────────────────────────────────────
export async function doQuestion(text) {
  if (!text.trim()) return;
  micStatus.textContent = '';
  searchBtn.disabled = true;
  showLoading();

  try {
    const data = await fetchAsk(text, langSelect.value, conversationHistory);
    searchBtn.disabled = false;
    conversationHistory.push({ user: text, assistant: data.reply || '' });
    if (conversationHistory.length > 6) conversationHistory = conversationHistory.slice(-6);

    if (data.result && data.result.ambiguous) {
      renderDisambig(data.result, data.reply, data.result.field);
      speakText(data.reply, langSelect.value);
    } else if (data.result && data.result.not_found) {
      renderError(data.result.not_found);
      speakText(data.reply || '', langSelect.value);
    } else if (data.result && data.result.options && data.result.options.length) {
      fromInput.value = data.result.origin || '';
      toInput.value   = data.result.destination || '';
      renderResult(data.result, data.reply);
      speakText(data.reply, langSelect.value);
    } else {
      renderError(data.reply || 'Could not understand the question.');
      speakText(data.reply || '', langSelect.value);
    }
  } catch {
    searchBtn.disabled = false;
    renderError('Could not reach the server.');
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────
searchBtn.addEventListener('click', doRoute);
fromInput.addEventListener('keydown', e => { if (e.key === 'Enter') doRoute(); });
toInput.addEventListener('keydown',   e => { if (e.key === 'Enter') doRoute(); });
questionBtn.addEventListener('click', () => { doQuestion(questionInput.value); questionInput.value = ''; });
questionInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') { doQuestion(questionInput.value); questionInput.value = ''; }
});
listenBtn.addEventListener('click', () => speakText(replyText.textContent, langSelect.value));

// ── Init voice ────────────────────────────────────────────────────────────────
initMic({ micBtn, micStatus, questionInput, langSelect, onTranscript: doQuestion });
