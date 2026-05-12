// Markdown rendering with DOMPurify + marked + (optional) highlight.js.
// Centralised so the security knobs (gfm/breaks/sanitization) live in one
// place and can't drift across call-sites.

// Render markdown text into a target element. Safe-by-default (sanitised).
// On parser failure, falls back to text content — never throws.
export function renderMarkdown(target, text) {
  if (!target) return;
  try {
    const html = DOMPurify.sanitize(marked.parse(text || '', { gfm: true, breaks: true }));
    target.innerHTML = html;
    if (window.hljs) {
      target.querySelectorAll('pre code').forEach((el) => {
        try { hljs.highlightElement(el); } catch (_) { /* highlight failure is non-fatal */ }
      });
    }
  } catch (_) {
    target.textContent = text || '';
  }
}

// Sanitise a single string of markdown to safe HTML without injecting into DOM.
// Useful when constructing innerHTML manually elsewhere.
export function markdownToSafeHtml(text) {
  try {
    return DOMPurify.sanitize(marked.parse(text || '', { gfm: true, breaks: true }));
  } catch (_) {
    return '';
  }
}
