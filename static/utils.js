// Pure helpers reused across the chat UI. Side-effect free — do NOT touch
// document.* or globals here. State-aware helpers (DOM rendering, network,
// session storage) stay in app.js for now; subsequent passes will pull more
// of them out as the boundaries become clearer.

export function pct(x) {
  return x == null ? '—' : (x * 100).toFixed(1) + '%';
}

export function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}

// Collision-safe id for client-side session paths. Uses crypto.getRandomValues
// (not Math.random) — also satisfies CodeQL js/insecure-randomness.
export function makeId() {
  const buf = new Uint8Array(6);
  (self.crypto || self.msCrypto).getRandomValues(buf);
  let s = '';
  for (const b of buf) s += b.toString(36).padStart(2, '0');
  return Date.now().toString(36) + s.slice(0, 8);
}

// Read a File/Blob as a data: URL.
export function fileToDataUrl(f) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(f);
  });
}

// Copy text to clipboard with a textarea fallback for older browsers / non-HTTPS.
export function copyToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } finally { ta.remove(); }
  return Promise.resolve();
}

// Trigger a browser download for an in-memory string blob.
export function downloadFile(name, content, mime = 'text/markdown;charset=utf-8') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 0);
}

// Slugify free-form text into a filename-safe stem.
export function safeFilename(s, maxLen = 60) {
  return (s || '').replace(/[^\w\-]+/g, '_').slice(0, maxLen) || 'file';
}
