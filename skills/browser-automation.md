---
name: browser-automation
description: Use when the user wants to scrape a web page, fill a form, take a screenshot, or otherwise automate browser actions.
---

The sandbox includes a headless Chromium (Playwright) accessible via these tools:

- `browser_navigate(url)` — open a URL, wait for DOM ready. Returns final URL, HTTP status, page title.
- `browser_text()` — extract visible text + title + URL of the current page.
- `browser_eval(expression)` — run JavaScript in the page context, get the return value as a string.
- `browser_click(selector)` — click a CSS selector.
- `browser_type(selector, text)` — type into an input.
- `browser_screenshot()` — take a PNG screenshot. The image is fed back as vision input on the next turn AND saved to `/workspace/screenshot.png`.

## Typical patterns

### Read an article
```
browser_navigate("https://example.com")
browser_text()
```

### Click a button by visible text
```
browser_eval("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Sign in')?.click()")
```

### See the current page
Call `browser_screenshot()` and reason about what's on screen using vision.

## Tips

- The browser session persists between tool calls in the same agent session.
- After `browser_navigate`, the page may need a moment for SPA content. If `browser_text()` looks empty, try `browser_eval("new Promise(r => setTimeout(() => r(document.body.innerText), 1500))")`.
- Don't `fs_read mode=Image` on a screenshot you just took — the screenshot is already attached as vision input.
