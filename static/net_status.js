// Network-status banner. Used by the SSE drivers (agent_sse.js, app.js) when
// a stream dies with `TypeError: Failed to fetch` or `NetworkError` mid-flight
// — typically Wi-Fi blip / reverse-proxy reset / VM restart.
//
// Behaviour:
//   - Renders a single sticky banner above `messagesEl` ("⚠️ соединение потеряно, проверяю...").
//   - Polls /healthz with exponential backoff (1s → 2s → 4s → 8s → 8s, ~23s).
//   - On success: swaps to "✅ соединение восстановлено", auto-removes after 3s.
//   - On failure: red "❌ не удалось переподключиться — обновите страницу".
//   - Listens for `window.online` to short-circuit the backoff.
//
// Does NOT auto-resend the user's prompt: history is already persisted to the
// DB under session_id; the user just retypes / re-sends after the banner says
// online.

const DELAYS_MS = [1000, 2000, 4000, 8000, 8000];

export function isNetworkError(err) {
  if (!err) return false;
  // Browsers vary: Chrome throws TypeError "Failed to fetch"; Firefox throws
  // TypeError "NetworkError when attempting to fetch resource."; Safari uses
  // "Load failed". Reader.read() after a mid-stream reset also surfaces as
  // TypeError.
  if (err.name === 'TypeError') return true;
  const msg = (err.message || '').toLowerCase();
  return msg.includes('failed to fetch')
      || msg.includes('networkerror')
      || msg.includes('load failed')
      || msg.includes('network request failed');
}

export async function waitForConnection(messagesEl, { lang = 'en' } = {}) {
  const ru = lang === 'ru';
  const banner = document.createElement('div');
  banner.className = 'agent-stats net-banner';
  banner.style.cssText = 'color:var(--orange);font-weight:600;';
  banner.textContent = ru
    ? '⚠️ соединение потеряно, проверяю…'
    : '⚠️ connection lost, retrying…';
  messagesEl.appendChild(banner);
  messagesEl.parentElement.scrollTop = messagesEl.parentElement.scrollHeight;

  let attempt = 0;
  let onlineEv = null;
  const onlinePromise = new Promise((resolve) => {
    onlineEv = () => resolve('online-event');
    window.addEventListener('online', onlineEv, { once: true });
  });

  try {
    for (const delay of DELAYS_MS) {
      attempt += 1;
      banner.textContent = ru
        ? `⚠️ соединение потеряно, проверяю… (попытка ${attempt})`
        : `⚠️ connection lost, retrying… (attempt ${attempt})`;
      // Race timer vs online-event so we wake up immediately when the browser
      // sees connectivity again.
      await Promise.race([
        new Promise((r) => setTimeout(r, delay)),
        onlinePromise,
      ]);
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 5000);
        const r = await fetch('/healthz', { signal: ctrl.signal, cache: 'no-store' });
        clearTimeout(timer);
        if (r.ok) {
          banner.style.color = '#6fdd8b';
          banner.textContent = ru
            ? '✅ соединение восстановлено'
            : '✅ connection restored';
          setTimeout(() => banner.remove(), 3000);
          return true;
        }
      } catch (_) {
        // still down; loop
      }
    }
  } finally {
    if (onlineEv) window.removeEventListener('online', onlineEv);
  }

  banner.style.color = '#ff6b6b';
  banner.textContent = ru
    ? '❌ не удалось переподключиться — обновите страницу'
    : '❌ could not reconnect — reload the page';
  return false;
}
