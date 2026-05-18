"""Shared LLM-driven PDF link picker.

Used by both:
- ``backend/browser_agent.py`` — Playwright-based server-side fallback when DOM
  heuristics miss on a publisher landing page.
- ``backend/extension.py`` — `/api/extension/resolve-pdf-url` endpoint that the
  Chrome extension's content script calls when its in-tab heuristics miss.

Single prompt + JSON contract so both code paths get identical behavior. The
LLM is decline-aware: it returns ``null`` for paywalled or login-walled pages
rather than hallucinating a URL.

Cost budget: ~$0.0005 per call (Claude Haiku 4.5, max_tokens=256). Caller is
responsible for any rate-limiting / throttling.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("rubricgen")

LLM_MODEL = "claude-haiku-4-5-20251001"
MAX_PAYLOAD_CHARS = 8000  # truncate the anchor JSON to keep the call cheap
MAX_TOKENS = 256
MIN_CONFIDENCE = 0.5

SYSTEM_PROMPT = (
    "You help a librarian download academic PDFs. Given the visible links on "
    "a publisher's article landing page, identify the URL that downloads the "
    "full-text PDF. Common patterns: href contains '/pdf/' or ends '.pdf'; "
    "text or aria-label contains 'Download PDF', 'Full text PDF', 'View PDF'; "
    "type='application/pdf'. If the page is paywalled, login-walled, or the "
    "PDF link is genuinely missing, return null and decline. Return ONLY a "
    "single JSON object with shape "
    "{\"pdf_url\": <absolute URL string or null>, \"confidence\": <0.0-1.0>, "
    "\"reason\": <one short sentence>}."
)


def pick_pdf_url_from_anchors(landing_url: str, anchors: list[dict]) -> str | None:
    """Sync Claude call. Returns the picked PDF URL or ``None`` on decline / error.

    ``anchors`` is a list of ``{href, text, aria, type}`` dicts harvested from
    the rendered page (cap to ~200 entries — the function truncates the JSON
    payload to ``MAX_PAYLOAD_CHARS`` regardless).

    Callers in async contexts (Playwright) should wrap this in
    ``asyncio.to_thread`` so the event loop isn't blocked by httpx.
    """
    if not anchors:
        return None
    try:
        from . import helpers
    except Exception as e:
        logger.info("pdf_link_picker: helpers import failed: %s", e)
        return None
    payload = json.dumps(anchors, separators=(",", ":"))[:MAX_PAYLOAD_CHARS]
    user = (
        f"Landing URL: {landing_url}\n\n"
        f"Visible anchors (first 200, JSON):\n{payload}\n\n"
        "Respond with the JSON object only."
    )
    try:
        raw = helpers.call_anthropic(
            [{"role": "user", "content": user}],
            SYSTEM_PROMPT,
            max_tokens=MAX_TOKENS,
            model=LLM_MODEL,
        )
    except Exception as e:
        logger.info("pdf_link_picker: LLM call failed: %s", e)
        return None
    try:
        parsed = helpers.parse_json_response(raw)
    except Exception as e:
        logger.info("pdf_link_picker: JSON parse failed: %s | raw=%s",
                    e, str(raw)[:200])
        return None
    return _validate_pick(landing_url, parsed)


def _validate_pick(landing_url: str, parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    pdf_url = parsed.get("pdf_url")
    confidence = parsed.get("confidence") or 0
    reason = (parsed.get("reason") or "")[:200]
    if not pdf_url or pdf_url == "null":
        logger.info("pdf_link_picker: LLM declined for %s — %s",
                    landing_url[:80], reason)
        return None
    if not isinstance(pdf_url, str) or not pdf_url.startswith("http"):
        logger.info("pdf_link_picker: invalid URL '%s'", str(pdf_url)[:120])
        return None
    if confidence < MIN_CONFIDENCE:
        logger.info("pdf_link_picker: low confidence (%s) — %s",
                    confidence, reason)
        return None
    logger.info("pdf_link_picker: picked %s (conf=%s) — %s",
                pdf_url[:120], confidence, reason)
    return pdf_url
