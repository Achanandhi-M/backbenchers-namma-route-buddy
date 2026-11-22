// web/js/voice.js – Web Speech API: microphone input and speech synthesis.
// Imports doQuestion from app.js via the callback pattern to avoid circular deps.

let _speaking    = false;
let _recognition = null;
let _isListening     = false;
let _recRunning      = false;
let _fullTranscript  = '';
let _silenceTimer    = null;
let _maxTimer        = null;

const SILENCE_MS = 2500;
const MAX_MS     = 20000;

const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;

// ── Speech synthesis ─────────────────────────────────────────────────────────
export function speakText(text, lang) {
  if (!window.speechSynthesis) return;
  if (_speaking) window.speechSynthesis.cancel();
  const utt  = new SpeechSynthesisUtterance(text);
  utt.lang   = lang;
  utt.rate   = 0.95;
  _speaking  = true;
  utt.onend  = () => { _speaking = false; };
  window.speechSynthesis.speak(utt);
}

// ── Microphone ───────────────────────────────────────────────────────────────
export function initMic({ micBtn, micStatus, questionInput, langSelect, onTranscript }) {
  if (!SpeechRec) {
    micBtn.disabled = true;
    micBtn.title = 'Speech recognition not supported. Use Chrome or Edge.';
    micStatus.textContent = 'Speech not supported. Please use Chrome or Edge.';
    return;
  }

  micBtn.addEventListener('click', () => {
    if (_isListening) _stop(); else _start();
  });

  function _start() {
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    _isListening    = true;
    _recRunning     = false;
    _fullTranscript = '';
    micBtn.classList.add('listening');
    micStatus.textContent = 'Listening\u2026';
    questionInput.value   = '';
    _maxTimer = setTimeout(_stop, MAX_MS);
    _launch();
  }

  function _launch() {
    if (!_isListening) return;
    _recognition                  = new SpeechRec();
    _recognition.lang             = langSelect.value;
    _recognition.continuous       = true;
    _recognition.interimResults   = true;

    _recognition.onstart = () => { _recRunning = true; };

    _recognition.onresult = e => {
      clearTimeout(_silenceTimer);
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) {
          _fullTranscript += (_fullTranscript ? ' ' : '') + t.trim();
        } else {
          interim += t;
        }
      }
      const display = _fullTranscript + (interim ? ' ' + interim : '');
      questionInput.value   = display;
      micStatus.textContent = display || 'Listening\u2026';
      _silenceTimer = setTimeout(_stop, SILENCE_MS);
    };

    _recognition.onerror = e => {
      _recRunning = false;
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      micStatus.textContent = `Microphone error: ${e.error}`;
      _stop();
    };

    _recognition.onend = () => {
      _recRunning = false;
      if (_isListening) {
        try { _recognition.start(); _recRunning = true; } catch {}
      }
    };

    try { _recognition.start(); _recRunning = true; } catch {}
  }

  function _stop() {
    if (!_isListening) return;
    _isListening = false;
    clearTimeout(_silenceTimer);
    clearTimeout(_maxTimer);
    _silenceTimer = null;
    _maxTimer     = null;
    micBtn.classList.remove('listening');
    micStatus.textContent = '';
    if (_recRunning) {
      try { _recognition.onend = null; _recognition.stop(); } catch {}
      _recRunning = false;
    }
    const text = _fullTranscript.trim();
    if (text) {
      questionInput.value = text;
      onTranscript(text);
    }
  }
}
