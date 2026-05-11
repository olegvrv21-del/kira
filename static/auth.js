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

// Lock so we only fire one auth-required event at a time even with concurrent 401s.
let _authWaiter = null;
let _authResolve = null;

/** Returns a promise that resolves with the new token once the user provides one
 *  via the settings UI (which calls window.kiraAuth.resolveAuthWait(token)).
 *  Resolves with '' if the user cancels. */
function waitForToken() {
  if (_authWaiter) return _authWaiter;
  _authWaiter = new Promise((resolve) => {
    _authResolve = resolve;
    // Dispatch event so the UI can react (open settings modal).
    window.dispatchEvent(new CustomEvent('kira:auth-required'));
  });
  return _authWaiter;
}

function resolveAuthWait(token) {
  if (_authResolve) {
    const r = _authResolve;
    _authResolve = null;
    _authWaiter = null;
    r(token || '');
  }
}

export function installFetchInterceptor() {
  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    if (!isSameOrigin(input)) return origFetch(input, init);
    let tok = getToken();
    let r = await origFetch(input, tok ? withAuthHeader(input, init, tok) : init);
    if (r.status === 401) {
      console.warn('[kira] 401 — waiting for token via settings UI');
      const newTok = await waitForToken();
      if (!newTok) return r;
      r = await origFetch(input, withAuthHeader(input, init, newTok));
    }
    return r;
  };
  // Re-export to window for backward compat / console use.
  window.kiraAuth = {
    get: getToken,
    set: setToken,
    clear: clearToken,
    prompt: promptToken,
    resolveAuthWait,
  };
}
