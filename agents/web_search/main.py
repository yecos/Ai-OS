"""
Agente: Web Search
==================
Busca en internet sin API key usando DuckDuckGo.

Setup:
    pip install duckduckgo-search
Arranque:
    cd agents/web_search
    uvicorn main:app --port 8020 --reload
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [web_search] %(message)s")
logger = logging.getLogger("web_search")

app = FastAPI(title="Web Search Agent", version="1.0")

# Intentar importar duckduckgo_search (paquete), con fallback a scraping HTML
try:
    from duckduckgo_search import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    _DDGS_AVAILABLE = False
    logger.warning("duckduckgo-search no instalado. Usando fallback HTML (menos fiable).")


class ActionRequest(BaseModel):
    action: str
    parameters: dict[str, Any] = {}


@app.post("/execute")
async def execute_action(req: ActionRequest):
    try:
        if req.action == "search":
            return await search(
                req.parameters.get("query", ""),
                int(req.parameters.get("max_results", 5)),
            )
        if req.action == "fetch_page":
            return await fetch_page(
                req.parameters.get("url", ""),
                int(req.parameters.get("max_chars", 8000)),
            )
        if req.action == "search_code":
            return await search_code(
                req.parameters.get("query", ""),
                int(req.parameters.get("max_results", 5)),
            )
        return {"status": "error", "message": f"Acción '{req.action}' no soportada."}
    except Exception as exc:
        logger.exception("Error en web_search")
        return {"status": "error", "message": str(exc)}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "agent": "web_search",
        "backend": "duckduckgo_search" if _DDGS_AVAILABLE else "html_fallback",
    }


# --------------------------------------------------------------------
async def search(query: str, max_results: int) -> dict:
    if not query:
        return {"status": "error", "message": "Falta 'query'."}
    max_results = max(1, min(max_results, 20))
    logger.info("Buscando: %s", query)

    if _DDGS_AVAILABLE:
        try:
            with DDGS() as ddgs:
                results = []
                for r in ddgs.text(query, max_results=max_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href") or r.get("link", ""),
                        "snippet": r.get("body") or r.get("snippet", ""),
                    })
                return {"status": "success", "query": query, "results": results, "count": len(results)}
        except Exception as exc:
            logger.warning("DDGS fallo, intentando fallback: %s", exc)

    # Fallback: scraping HTML de DuckDuckGo
    return await _search_html_fallback(query, max_results)


async def _search_html_fallback(query: str, max_results: int) -> dict:
    """Scraping basico del HTML de DuckDuckGo cuando el paquete no esta."""
    url = "https://html.duckduckgo.com/html/"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        resp = await client.post(url, data={"q": query})
        resp.raise_for_status()
        html = resp.text

    # Parsear resultados con regex (no es perfecto pero funciona)
    results = []
    # Los resultados estan en <a class="result__a" href="...">titulo</a>
    title_pattern = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>')
    snippet_pattern = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)

    titles = title_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (href, title) in enumerate(titles[:max_results]):
        # Limpiar URL (DuckDuckGo usa redirect)
        clean_url = href
        if "uddg=" in href:
            import urllib.parse
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            clean_url = parsed.get("uddg", [href])[0]
        snippet = re.sub(r"<[^>]+>", "", snippets[i]) if i < len(snippets) else ""
        results.append({
            "title": title.strip(),
            "url": clean_url,
            "snippet": snippet.strip()[:300],
        })

    return {"status": "success", "query": query, "results": results, "count": len(results), "backend": "html_fallback"}


async def fetch_page(url: str, max_chars: int) -> dict:
    if not url:
        return {"status": "error", "message": "Falta 'url'."}
    max_chars = max(500, min(max_chars, 50000))
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AI-OS-Bot)"})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return {"status": "error", "message": f"No se pudo descargar: {exc}", "url": url}

    content_type = resp.headers.get("content-type", "")
    if "html" in content_type:
        # Limpiar HTML: quitar scripts, estilos, tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = resp.text

    return {
        "status": "success",
        "url": url,
        "title": _extract_title(resp.text) if "html" in content_type else url,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "total_chars": len(text),
    }


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return match.group(1).strip() if match else ""


async def search_code(query: str, max_results: int) -> dict:
    """Busca codigo anadiendo site:stackoverflow.com o site:github.com."""
    if not query:
        return {"status": "error", "message": "Falta 'query'."}
    # Buscar en Stack Overflow y GitHub
    so_query = f"{query} site:stackoverflow.com"
    gh_query = f"{query} site:github.com"
    so_results = await search(so_query, max_results=max_results)
    gh_results = await search(gh_query, max_results=max_results)
    return {
        "status": "success",
        "query": query,
        "stackoverflow": so_results.get("results", []),
        "github": gh_results.get("results", []),
    }
