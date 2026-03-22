"""Local search proxy — wraps Google/Bing searches for Skuld.

Runs on localhost:8080, returns SearXNG-compatible JSON.
Uses httpx to fetch Google search results and parse them.
No API key needed — uses public search pages.
"""

import asyncio
import json
import logging
import re
from urllib.parse import quote_plus, urlencode

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
import uvicorn

log = logging.getLogger("local_search")
app = FastAPI(title="Skuld Local Search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_google(html: str) -> list[dict]:
    """Extract search results from Google HTML."""
    results = []
    # Match <a href="/url?q=URL&..."><h3>TITLE</h3></a> patterns
    # and nearby snippet text
    blocks = re.findall(
        r'<a[^>]+href="/url\?q=([^&"]+)[^"]*"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
        html, re.DOTALL
    )
    for url, title in blocks:
        title_clean = re.sub(r'<[^>]+>', '', title).strip()
        url_clean = url.split('&')[0]
        if not url_clean.startswith('http'):
            continue
        results.append({
            "url": url_clean,
            "title": title_clean,
            "content": "",
            "engine": "google",
        })

    # Try to extract snippets
    snippets = re.findall(
        r'<div[^>]+class="[^"]*VwiC3b[^"]*"[^>]*>(.*?)</div>',
        html, re.DOTALL
    )
    for i, snippet in enumerate(snippets):
        text = re.sub(r'<[^>]+>', '', snippet).strip()
        if i < len(results):
            results[i]["content"] = text

    return results


def _parse_bing(html: str) -> list[dict]:
    """Extract search results from Bing HTML."""
    results = []
    blocks = re.findall(
        r'<li[^>]+class="b_algo"[^>]*>(.*?)</li>',
        html, re.DOTALL
    )
    for block in blocks:
        url_match = re.search(r'<a[^>]+href="(https?://[^"]+)"', block)
        title_match = re.search(r'<a[^>]+>(.*?)</a>', block)
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)

        if url_match and title_match:
            title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            url = url_match.group(1)
            content = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip() if snippet_match else ""
            results.append({
                "url": url,
                "title": title,
                "content": content,
                "engine": "bing",
            })
    return results


async def _search_google(query: str, count: int = 10) -> list[dict]:
    """Search Google and parse results."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={count}&hl=en"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            if resp.status_code == 200:
                return _parse_google(resp.text)
            log.warning("Google returned %d", resp.status_code)
    except Exception as e:
        log.warning("Google search failed: %s", e)
    return []


async def _search_bing(query: str, count: int = 10) -> list[dict]:
    """Search Bing and parse results."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={count}&setlang=en"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            if resp.status_code == 200:
                return _parse_bing(resp.text)
            log.warning("Bing returned %d", resp.status_code)
    except Exception as e:
        log.warning("Bing search failed: %s", e)
    return []


@app.get("/search")
async def search(
    q: str = Query(...),
    format: str = Query(default="json"),
    language: str = Query(default="en"),
):
    """SearXNG-compatible search endpoint."""
    # Run both engines in parallel
    google_task = asyncio.create_task(_search_google(q))
    bing_task = asyncio.create_task(_search_bing(q))

    google_results = await google_task
    bing_results = await bing_task

    # Merge and deduplicate by URL
    seen_urls = set()
    merged = []
    for r in google_results + bing_results:
        url = r["url"].rstrip("/").lower()
        if url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    return JSONResponse({
        "query": q,
        "number_of_results": len(merged),
        "results": merged,
    })


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
