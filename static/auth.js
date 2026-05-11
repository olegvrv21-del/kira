// Auth + fetch interceptor module.
// Reads/writes 'kira_auth_token' in localStorage and injects
// Authorization: Bearer <token> into same-origin fetches.

const KEY = 'kira_auth_token';

export function getToken() {
  try { return localStorage.getItem(KEY) || ''; } catch (_) { return ''; }
}
export function setToken(t) {
  try { localStorage.setItem(KEY, t || ''); } catch (_) {}
}
export function clearToken() {
  try { localStorage.removeItem(KEY); } catch (_) {}
}
export function promptToken() {
  const cur = getToken();
  const v = window.prompt('Kira auth token (empty to clear):', cur);
  if (v === null) return cur;
  setToken(v.trim());
  return v.trim();
}

function isSameOrigin(input) {
  let url = input;
  try { url = (typeof input === 'string') ? input : (input && input.url) || ''; } catch (_) {}
  return (typeof url === 'string') && (
    url.startsWith('/') || url.startsWith(location.origin) || !url.includes('://')
  );
}

function withAuthHeader(input, init, token) {
  const opts = Object.assign({}, init || {});
  const hdrSrc = opts.headers || (typeof input === 'object' && input ? input.headers : undefined) || {};
  const headers = new Headers(hdrSrc);
  if (token) headers.set('authorization', 'Bearer ' + token);
  else headers.delete('authorization');
  opts.headers = headers;
  return opts;
}

// Lock so we only show one prompt at a time even with concurrent 401s.
let _authPromptInFlight = null;
function askForTokenOnce() {
  if (_authPromptInFlight) return _authPromptInFlight;
  _authPromptInFlight = new Promise((resolve) => {
    // Defer to next tick so the current fetch unwinds cleanly.
    setTimeout(() => {
      const v = window.prompt(
        'Kira: требуется auth-токен. Введи токен (пусто чтобы отменить):',
        getToken()
      );
      if (v && v.trim()) setToken(v.trim());
      _authPromptInFlight = null;
      resolve(v && v.trim() ? v.trim() : '');
    }, 0);
  });
  return _authPromptInFlight;
}

export function installFetchInterceptor() {
  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    if (!isSameOrigin(input)) return origFetch(input, init);
    let tok = getToken();
    let r = await origFetch(input, tok ? withAuthHeader(input, init, tok) : init);
    if (r.status === 401) {
      console.warn('[kira] 401 — prompting for token');
      const newTok = await askForTokenOnce();
      if (!newTok) return r;
      r = await origFetch(input, withAuthHeader(input, init, newTok));
    }
    return r;
  };
  // Re-export to window for backward compat / console use.
  window.kiraAuth = { get: getToken, set: setToken, clear: clearToken, prompt: promptToken };
}
