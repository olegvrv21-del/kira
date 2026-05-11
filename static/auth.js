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

export function installFetchInterceptor() {
  const origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const tok = getToken();
    if (!tok) return origFetch(input, init);
    let url = input;
    try { url = (typeof input === 'string') ? input : (input && input.url) || ''; } catch (_) {}
    const sameOrigin = (typeof url === 'string') && (
      url.startsWith('/') || url.startsWith(location.origin) || !url.includes('://')
    );
    if (!sameOrigin) return origFetch(input, init);
    const opts = Object.assign({}, init || {});
    const hdrSrc = opts.headers || (typeof input === 'object' && input ? input.headers : undefined) || {};
    const headers = new Headers(hdrSrc);
    if (!headers.has('authorization')) headers.set('authorization', 'Bearer ' + tok);
    opts.headers = headers;
    return origFetch(input, opts).then((r) => {
      if (r && r.status === 401) console.warn('[kira] 401: token rejected for', url);
      return r;
    });
  };
  // Re-export to window for backward compat / console use.
  window.kiraAuth = { get: getToken, set: setToken, clear: clearToken, prompt: promptToken };
}
