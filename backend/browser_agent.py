"""Headless-browser PDF fetcher using Playwright.

Final-tier strategy for the Search Strategist's "Browser agent" import mode.
When PMC, Unpaywall, direct, meta-tag, AND Firecrawl have all missed (or
returned 403s that survive even browser headers), we boot a real Chromium
session, navigate to the landing page, and try to coerce a PDF download.

Why this works when ``httpx`` + Firecrawl-extracted-URLs don't: many
publishers issue cookies during the landing-page render that gate the
subsequent PDF request. A real browser session preserves those cookies
across the click → download chain. ``httpx`` doesn't.

Strategy (all heuristic — no LLM in the loop yet):

1. Open the landing URL with Playwright. Wait for ``networkidle``.
2. Read ``citation_pdf_url`` from the rendered DOM (publishers like BMJ,
   Springer, NEJM emit this meta tag — and Playwright sees the post-JS
   version which is often more complete than what ``httpx`` got).
3. If found, navigate to that URL within the same browser context so
   cookies + Referer headers tag along.
4. Capture the resulting PDF response body. If Chromium chooses to render
   the PDF inline (its default), grab the response bytes.
5. If no ``citation_pdf_url``, scan visible links for "Download PDF",
   ``[href$=".pdf"]``, ``[type="application/pdf"]`` and try the first
   plausible match.

Returns ``{sha256, filename, storage_path}`` on success, ``None`` on miss.

Graceful degrade: if Playwright isn't installed (e.g. local dev without
the browser), return ``None`` immediately. The caller treats this as a
metadata-only fallback like every other miss.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from . import paper_files

logger = logging.getLogger("rubricgen")

PDF_MAGIC = b"%PDF"
NAVIGATION_TIMEOUT_MS = 30_000  # 30s landing-page render budget
DOWNLOAD_TIMEOUT_MS = 30_000

# Selectors for "this is the PDF link" — most-confident first.
PDF_LINK_SELECTORS = [
    'a[href*="/pdf/"]',
    'a[href$=".pdf"]',
    'a[type="application/pdf"]',
    'a:has-text("Download PDF")',
    'a:has-text("Full text PDF")',
    'a:has-text("PDF")',
    'button:has-text("Download PDF")',
]

CITATION_META_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _store_pdf_bytes(data: bytes) -> dict | None:
    if not data or data[:4] != PDF_MAGIC:
        return None
    import hashlib
    sha256 = hashlib.sha256(data).hexdigest()
    filename = f"{sha256}.pdf"
    try:
        storage_path = paper_files.write_paper_file(data, filename)
    except Exception as e:
        logger.error("Storage write failed for browser-agent PDF: %s", e)
        return None
    return {"sha256": sha256, "filename": filename, "storage_path": storage_path}


async def _resolve_pdf_url(page, landing_url: str) -> str | None:
    """Pull a PDF URL out of the rendered page. Tries citation_pdf_url meta
    tag first, then visible link selectors."""
    # citation_pdf_url meta tag
    try:
        meta = await page.locator('meta[name="citation_pdf_url"]').first.get_attribute(
            "content", timeout=2000
        )
        if meta:
            return meta
    except Exception:
        pass

    # Fall back to plain HTML scrape (some pages use weird casing)
    try:
        html = await page.content()
        m = CITATION_META_RE.search(html or "")
        if m:
            return m.group(1)
    except Exception:
        pass

    # Visible link selectors
    for selector in PDF_LINK_SELECTORS:
        try:
            href = await page.locator(selector).first.get_attribute(
                "href", timeout=1500
            )
            if href:
                return href
        except Exception:
            continue
    return None


async def _normalize_url(landing_url: str, candidate: str) -> str:
    if candidate.startswith("http"):
        return candidate
    if candidate.startswith("//"):
        return "https:" + candidate
    if candidate.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(landing_url)
        return f"{u.scheme}://{u.netloc}{candidate}"
    return candidate


async def _fetch_pdf_async(landing_url: str) -> dict | None:
    """Async core: open Playwright, navigate, find + download PDF."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — browser-agent mode disabled")
        return None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",  # /dev/shm is tiny on Render
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        except Exception as e:
            logger.error("Failed to launch Chromium: %s", e)
            return None

        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            try:
                await page.goto(
                    landing_url, wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                # Give JS-driven publishers (Springer, Wiley) a moment to render.
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass

                pdf_url = await _resolve_pdf_url(page, landing_url)
                if not pdf_url:
                    logger.info("Browser agent: no PDF link found on %s", landing_url)
                    return None
                pdf_url = await _normalize_url(landing_url, pdf_url)

                # Try same-context fetch first — cookies + Referer come along.
                try:
                    response = await context.request.get(
                        pdf_url, headers={"Referer": landing_url},
                        timeout=DOWNLOAD_TIMEOUT_MS,
                    )
                    if response.ok:
                        body = await response.body()
                        out = _store_pdf_bytes(body)
                        if out:
                            return out
                except Exception as e:
                    logger.info("Browser agent: same-context fetch failed: %s", e)

                # Fallback: navigate the page itself to the PDF URL and capture
                # the response (some publishers gate on browser navigation,
                # not just cookies).
                try:
                    response = await page.goto(
                        pdf_url, timeout=DOWNLOAD_TIMEOUT_MS,
                        wait_until="domcontentloaded",
                    )
                    if response and response.ok:
                        body = await response.body()
                        out = _store_pdf_bytes(body)
                        if out:
                            return out
                except Exception as e:
                    logger.info("Browser agent: navigate-to-PDF failed: %s", e)

                return None
            finally:
                await context.close()
        finally:
            await browser.close()


def fetch_pdf_via_browser(landing_url: str) -> dict | None:
    """Sync wrapper — fire up an event loop to drive the async Playwright
    session. Returns ``{sha256, filename, storage_path}`` or ``None``.
    """
    if not landing_url:
        return None
    try:
        return asyncio.run(_fetch_pdf_async(landing_url))
    except Exception as e:
        logger.exception("Browser agent crashed for %s: %s", landing_url, e)
        return None
