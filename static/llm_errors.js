// Friendly LLM/error mapping. Turns raw upstream 4xx/5xx bodies and SSE
// `{error}` payloads into short human messages, keeping the raw text as an
// optional collapsible detail. Bilingual (ru/en).
//
// Used by app.js /chat rendering. Intentionally dependency-free.

const RU = {
  balance:  'Баланс LLM-провайдера исчерпан. Кира временно не может отвечать — пополните счёт или переключитесь на другого провайдера.',
  rate:     'Провайдер перегружен (слишком много запросов). Подождите минуту и повторите.',
  model:    'Выбранная модель недоступна. Переключитесь на другую модель в списке.',
  auth:     'Нужен auth-токен. Откройте настройки и введите токен.',
  upstream: 'Провайдер вернул ошибку. Попробуйте ещё раз или смените модель.',
  generic:  'Что-то пошло не так при обращении к модели.',
  details:  'подробности',
};
const EN = {
  balance:  'The LLM provider balance is exhausted. Kira can’t answer right now — top up or switch provider.',
  rate:     'Provider is overloaded (too many requests). Wait a minute and retry.',
  model:    'The selected model is unavailable. Pick another model from the list.',
  auth:     'An auth token is required. Open settings and enter your token.',
  upstream: 'The provider returned an error. Retry or switch models.',
  generic:  'Something went wrong talking to the model.',
  details:  'details',
};

function pick(lang) { return lang === 'en' ? EN : RU; }

/** Classify an error into a category string. */
export function classifyLlmError(status, raw) {
  const s = Number(status) || 0;
  const txt = String(raw || '').toLowerCase();
  if (s === 401 || /unauthorized|missing authentication|invalid.*token/.test(txt)) return 'auth';
  if (s === 402 || /insufficient|balance|no credit|out of credit|quota|exhaust|payment|top ?up|billing/.test(txt)) return 'balance';
  if (s === 429 || /rate.?limit|too many requests|overloaded|capacity/.test(txt)) return 'rate';
  if (s === 404 || /model.*(not found|unavailable|does not exist|unknown)|(unknown|no such|invalid|unsupported)\s+model/.test(txt)) return 'model';
  if (s >= 500 || /upstream|bad gateway|gateway timeout|service unavailable/.test(txt)) return 'upstream';
  return 'generic';
}

/**
 * Map a raw error into {text, detail, category}.
 * @param {number} status HTTP status (0 if unknown / SSE payload)
 * @param {string} raw    raw upstream body or error string
 * @param {string} lang   'ru' | 'en'
 */
export function friendlyLlmError(status, raw, lang = 'ru') {
  const L = pick(lang);
  const cat = classifyLlmError(status, raw);
  const text = L[cat] || L.generic;
  const rawStr = (typeof raw === 'string') ? raw : JSON.stringify(raw, null, 2);
  // Only attach detail when it adds info beyond the friendly line.
  const detail = (rawStr && rawStr.trim() && cat !== 'auth') ? rawStr.trim() : '';
  return { text, detail, category: cat, detailsLabel: L.details };
}

/**
 * Render a friendly error into a target element (replaces its content).
 * Adds a <details> block with the raw body when available.
 */
export function renderLlmError(el, status, raw, lang = 'ru') {
  const { text, detail, detailsLabel } = friendlyLlmError(status, raw, lang);
  el.textContent = '';
  const p = document.createElement('div');
  p.textContent = text;
  el.appendChild(p);
  if (detail) {
    const d = document.createElement('details');
    d.style.cssText = 'margin-top:6px;opacity:.7;font-size:12px';
    const sm = document.createElement('summary');
    sm.style.cursor = 'pointer';
    sm.textContent = detailsLabel;
    const pre = document.createElement('pre');
    pre.style.cssText = 'white-space:pre-wrap;word-break:break-word;margin:6px 0 0';
    pre.textContent = detail.slice(0, 2000);
    d.appendChild(sm); d.appendChild(pre);
    el.appendChild(d);
  }
}
