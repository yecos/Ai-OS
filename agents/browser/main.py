"""
Agente: Browser
===============
Controla un navegador Chromium real vía Playwright.

Setup:
1. pip install playwright
2. playwright install chromium   # descarga el navegador
3. Levanta este agente:
       cd agents/browser
       uvicorn main:app --port 8004 --reload

El navegador se abre la primera vez que se llama a `navigate` y se mantiene
vivo entre acciones (sesión persistente). `close` lo cierra.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [browser] %(message)s")
logger = logging.getLogger("browser_agent")

app = FastAPI(title="Browser Agent", version="1.0")

HEADLESS = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
USER_DATA_DIR = os.getenv("BROWSER_USER_DATA_DIR", "./data/browser_profile")

# --------------------------------------------------------------------
# Sesión de navegador perezosa (solo se crea cuando se necesita)
# --------------------------------------------------------------------
_playwright = None
_browser = None
_page: Optional[Any] = None


def _ensure_browser():
    """Arranca Playwright + Chromium + una pestaña si no están ya."""
    global _playwright, _browser, _page
    if _page is not None:
        return _page
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright no instalado. Ejecuta: pip install playwright && playwright install chromium") from exc

    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=HEADLESS)
    context = _browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-OS-Bot",
    )
    _page = context.new_page()
    logger.info("Navegador Chromium iniciado (headless=%s)", HEADLESS)
    return _page


def _close_browser():
    global _playwright, _browser, _page
    if _page is not None:
        try:
            _page.close()
        except Exception:  # noqa: BLE001
            pass
    if _browser is not None:
        try:
            _browser.close()
        except Exception:  # noqa: BLE001
            pass
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:  # noqa: BLE001
            pass
    _page = _browser = _playwright = None


# --------------------------------------------------------------------
class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
def execute_action(req: ActionRequest):
    try:
        if req.action == "close":
            _close_browser()
            return {"status": "success", "message": "Navegador cerrado."}

        page = _ensure_browser()

        if req.action == "navigate":
            return navigate(page, req.parameters.get("url", ""))
        if req.action == "click":
            return click(page, req.parameters.get("selector", ""))
        if req.action == "type_text":
            return type_text(
                page,
                req.parameters.get("selector", ""),
                req.parameters.get("text", ""),
                req.parameters.get("submit", False),
            )
        if req.action == "screenshot":
            return screenshot(page, req.parameters.get("output_path", ""))
        if req.action == "get_text":
            return get_text(page, req.parameters.get("selector"))
        if req.action == "extract_links":
            return extract_links(page)
        if req.action == "wait_for":
            return wait_for(
                page,
                req.parameters.get("selector", ""),
                req.parameters.get("timeout_ms", 5000),
            )
        if req.action == "scroll":
            return scroll(
                page,
                req.parameters.get("direction", "down"),
                req.parameters.get("amount", 500),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error en browser agent")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "browser",
        "headless": HEADLESS,
        "browser_open": _page is not None,
    }


# --------------------------------------------------------------------
def navigate(page, url: str) -> dict:
    if not url:
        return {"status": "error", "message": "Falta 'url'."}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    logger.info("Navegando a %s", url)
    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return {
        "status": "success",
        "url": page.url,
        "title": page.title(),
        "status_code": resp.status if resp else None,
    }


def click(page, selector: str) -> dict:
    if not selector:
        return {"status": "error", "message": "Falta 'selector'."}
    # Si parece texto (no es selector CSS), usar get_by_text
    if not any(c in selector for c in ".#[>"):
        loc = page.get_by_text(selector, exact=False).first
    else:
        loc = page.locator(selector).first
    loc.click(timeout=10000)
    return {"status": "success", "clicked": selector, "url": page.url}


def type_text(page, selector: str, text: str, submit: bool) -> dict:
    if not selector or not text:
        return {"status": "error", "message": "Faltan 'selector' y 'text'."}
    loc = page.locator(selector).first
    loc.fill(text)
    if submit:
        loc.press("Enter")
    return {"status": "success", "typed": text, "submitted": submit}


def screenshot(page, output_path: str) -> dict:
    if not output_path:
        return {"status": "error", "message": "Falta 'output_path'."}
    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=output_path, full_page=True)
    return {"status": "success", "path": output_path}


def get_text(page, selector: Optional[str]) -> dict:
    if selector:
        text = page.locator(selector).first.inner_text(timeout=5000)
    else:
        text = page.inner_text("body")
    # Truncar a 5000 chars para no saturar al LLM
    return {"status": "success", "text": text[:5000], "truncated": len(text) > 5000}


def extract_links(page) -> dict:
    links = page.eval_on_selector_all(
        "a",
        """els => els.map(e => ({href: e.href, text: (e.innerText || '').trim().slice(0, 100)})).filter(l => l.href)""",
    )
    return {"status": "success", "links": links[:50], "count": len(links)}


def wait_for(page, selector: str, timeout_ms: int) -> dict:
    if not selector:
        return {"status": "error", "message": "Falta 'selector'."}
    try:
        page.wait_for_selector(selector, timeout=int(timeout_ms))
        return {"status": "success", "selector": selector}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": f"Timeout esperando {selector}: {exc}"}


def scroll(page, direction: str, amount: int) -> dict:
    dy = -int(amount) if direction == "up" else int(amount)
    page.evaluate(f"window.scrollBy(0, {dy})")
    return {"status": "success", "direction": direction, "amount": amount}
