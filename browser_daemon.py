"""Long-lived headless Chromium daemon for the agent sandbox.

Listens on 127.0.0.1:9000 inside the container. Holds a single Page object
between requests so navigation history and DOM state persist.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI
from playwright.async_api import Browser, Page, async_playwright
from pydantic import BaseModel


class NavReq(BaseModel):
    url: str
    wait_until: str = "domcontentloaded"
    timeout_ms: int = 30000


class EvalReq(BaseModel):
    expression: str
    timeout_ms: int = 15000


class ClickReq(BaseModel):
    selector: str
    timeout_ms: int = 15000


class TypeReq(BaseModel):
    selector: str
    text: str
    timeout_ms: int = 15000


class WaitReq(BaseModel):
    selector: str | None = None
    ms: int | None = None
    timeout_ms: int = 15000


state = {"page": None, "browser": None, "pw": None, "console_logs": [], "network_log": [], "network_recording": False}
_lock = asyncio.Lock()

_DEVICES = {
    # name -> (width, height, dpr, mobile, touch, ua_hint)
    "iPhone 14": (390, 844, 3, True, True, "iphone"),
    "iPhone SE": (375, 667, 2, True, True, "iphone"),
    "Pixel 7": (412, 915, 2.625, True, True, "android"),
    "iPad": (820, 1180, 2, True, True, "ipad"),
    "Desktop": (1280, 800, 1, False, False, ""),
    "Desktop 1080p": (1920, 1080, 1, False, False, ""),
    "Desktop 4K": (3840, 2160, 2, False, False, ""),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    pw = await async_playwright().start()
    browser: Browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
    page: Page = await ctx.new_page()

    # Console capture (ring buffer of 500).
    def on_console(msg):
        try:
            state["console_logs"].append(
                {
                    "type": msg.type,
                    "text": msg.text,
                    "url": page.url,
                }
            )
            if len(state["console_logs"]) > 500:
                del state["console_logs"][:-500]
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", lambda e: state["console_logs"].append({"type": "pageerror", "text": str(e), "url": page.url}))

    def on_request_finished(req):
        if not state["network_recording"]:
            return
        try:
            r = req.response()
            state["network_log"].append(
                {
                    "url": req.url,
                    "method": req.method,
                    "status": (r.status if r else None),
                    "resource_type": req.resource_type,
                }
            )
            if len(state["network_log"]) > 2000:
                del state["network_log"][:-2000]
        except Exception:
            pass

    page.on("requestfinished", on_request_finished)
    page.on(
        "requestfailed",
        lambda r: (
            state["network_recording"]
            and state["network_log"].append(
                {
                    "url": r.url,
                    "method": r.method,
                    "status": None,
                    "failure": (r.failure or ""),
                    "resource_type": r.resource_type,
                }
            )
        ),
    )
    state["pw"] = pw
    state["browser"] = browser
    state["page"] = page
    yield
    try:
        await browser.close()
        await pw.stop()
    except Exception:
        pass


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/navigate")
async def navigate(req: NavReq):
    async with _lock:
        page: Page = state["page"]
        resp = await page.goto(req.url, wait_until=req.wait_until, timeout=req.timeout_ms)
        return {
            "url": page.url,
            "status": resp.status if resp else None,
            "title": await page.title(),
        }


@app.post("/eval")
async def eval_expr(req: EvalReq):
    async with _lock:
        page: Page = state["page"]
        # wrap in IIFE so users can pass either an expression or a statement block
        wrapped = f"(async () => {{ return ({req.expression}); }})()"
        try:
            result = await asyncio.wait_for(page.evaluate(wrapped), timeout=req.timeout_ms / 1000)
            try:
                import json

                serialized = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                serialized = repr(result)
            return {"result": serialized}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


@app.post("/screenshot")
async def screenshot():
    async with _lock:
        page: Page = state["page"]
        png = await page.screenshot(type="png", full_page=False)
        return {"png_b64": base64.b64encode(png).decode("ascii"), "url": page.url, "title": await page.title()}


@app.post("/text")
async def text():
    async with _lock:
        page: Page = state["page"]
        body = await page.evaluate("() => document.body ? document.body.innerText : ''")
        return {"text": body[:200_000], "url": page.url, "title": await page.title()}


@app.post("/click")
async def click(req: ClickReq):
    async with _lock:
        page: Page = state["page"]
        try:
            await page.click(req.selector, timeout=req.timeout_ms)
            return {"ok": True, "url": page.url}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


@app.post("/type")
async def type_in(req: TypeReq):
    async with _lock:
        page: Page = state["page"]
        try:
            await page.fill(req.selector, req.text, timeout=req.timeout_ms)
            return {"ok": True}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


@app.post("/wait")
async def wait(req: WaitReq):
    async with _lock:
        page: Page = state["page"]
        try:
            if req.selector:
                await page.wait_for_selector(req.selector, timeout=req.timeout_ms)
            elif req.ms:
                await page.wait_for_timeout(req.ms)
            return {"ok": True}
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}


@app.post("/console_logs")
async def console_logs(req: dict | None = None):
    limit = 100
    if isinstance(req, dict):
        limit = int(req.get("limit", 100))
    logs = state["console_logs"][-limit:]
    return {"logs": logs}


@app.post("/clear_console")
async def clear_console():
    state["console_logs"].clear()
    return {"ok": True}


@app.post("/network/start")
async def network_start():
    state["network_log"].clear()
    state["network_recording"] = True
    return {"ok": True}


@app.post("/network/stop")
async def network_stop():
    state["network_recording"] = False
    return {"ok": True, "count": len(state["network_log"])}


@app.post("/network/log")
async def network_log(req: dict | None = None):
    limit = 200
    flt = ""
    if isinstance(req, dict):
        limit = int(req.get("limit", 200))
        flt = (req.get("filter") or "").lower()
    logs = state["network_log"]
    if flt:
        logs = [r for r in logs if flt in (r.get("url") or "").lower()]
    return {"logs": logs[-limit:], "total": len(state["network_log"]), "recording": state["network_recording"]}


@app.post("/accessibility")
async def accessibility(req: dict | None = None):
    async with _lock:
        page: Page = state["page"]
        interesting_only = True
        root = None
        if isinstance(req, dict):
            interesting_only = bool(req.get("interesting_only", True))
            root = req.get("root")
        try:
            kwargs = {"interesting_only": interesting_only}
            if root:
                el = await page.query_selector(root)
                if el:
                    kwargs["root"] = el
            snap = await page.accessibility.snapshot(**kwargs)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return {"tree": snap, "url": page.url}


class EmulateReq(BaseModel):
    device: str | None = None
    width: int | None = None
    height: int | None = None
    device_scale_factor: float | None = None
    mobile: bool | None = None
    dark_mode: bool | None = None
    media: str | None = None  # 'screen', 'print'


@app.post("/emulate")
async def emulate(req: EmulateReq):
    async with _lock:
        page: Page = state["page"]
        applied = {}
        try:
            if req.device:
                if req.device not in _DEVICES:
                    return {"error": f"unknown device {req.device!r}; available: {list(_DEVICES)}"}
                w, h, dpr, mobile, touch, _ = _DEVICES[req.device]
                await page.set_viewport_size({"width": w, "height": h})
                applied["device"] = req.device
                applied["viewport"] = (w, h)
                applied["dpr"] = dpr
                applied["mobile"] = mobile
            if req.width and req.height:
                await page.set_viewport_size({"width": req.width, "height": req.height})
                applied["viewport"] = (req.width, req.height)
            if req.dark_mode is not None:
                await page.emulate_media(color_scheme="dark" if req.dark_mode else "light")
                applied["color_scheme"] = "dark" if req.dark_mode else "light"
            if req.media:
                await page.emulate_media(media=req.media)
                applied["media"] = req.media
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
        return {"ok": True, "applied": applied, "url": page.url}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
