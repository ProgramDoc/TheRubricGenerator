"""Best-effort PDF fetcher for search-result imports.

Used by the Search Strategist's "Get PDF" import mode. Given a search-result
row (PMID / DOI / PMCID / URL), tries multiple strategies in order to locate an
open-access PDF and download it. First hit wins; returns ``None`` on miss so
callers can fall back to a metadata-only paper row.

Strategy order (cascades through tiers):

1. **PMC** (free tier) — if ``pmcid`` is set, reuse :func:`backend.pubmed.download_pmc_pdf`.
2. **Unpaywall** (free tier) — if ``doi`` is set, GET ``api.unpaywall.org/v2/{doi}``
   and try ``best_oa_location.url_for_pdf``. Free, no key, covers most OA papers.
3. **Direct URL / meta-tag** (free tier) — fall through to ``result.url``: try direct
   fetch if it ends in ``.pdf``, scrape the page for ``<meta name="citation_pdf_url">``,
   then a final blind direct GET.
4. **Firecrawl** (firecrawl tier; opt-in via ``use_firecrawl=True``) — JS-render the
   landing page and re-extract ``citation_pdf_url`` after scripts run. Many publishers
   reveal the link only after their script runs. Requires ``FIRECRAWL_API_KEY``.
5. **Browser agent** (browser tier; opt-in via ``use_browser=True``) — final tier:
   launches Playwright/Chromium against the publisher's landing page, picks up real
   session cookies, and tries to coerce the PDF download. Slow + RAM-hungry.

Every download is validated by checking that the first 4 bytes equal
``b"%PDF"`` — rejects HTML disguised as PDFs, paywalls, captcha pages.

The ``on_event`` callback (when provided) receives one event per strategy attempt
with shape ``{strategy, outcome, reason, duration_ms, attempt}``. ``outcome`` is
``"hit" | "miss" | "transient_error" | "permanent_error"``. Strategies that fail
on transient errors (5xx / 429 / connect timeout / read timeout) are retried up
to 2 attempts with 1s/2s backoff. Permanent errors and the browser-agent tier
skip retry.

Return shape on success: ``{sha256, filename, storage_path, tier}`` where
``tier`` is ``"free" | "firecrawl" | "browser"`` — used by callers to compute
the effective per-paper credit charge for the auto import mode.

Adding a new strategy: write a ``_strat_*`` returning ``(result_dict_or_None,
outcome, reason)``, register a call in ``fetch_pdf_for_result`` at the right
tier, and decide whether to wrap it in ``_run_with_retry`` (most strategies
should; metadata-only API calls and the slow browser agent shouldn't).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import paper_files

logger = logging.getLogger("rubricgen")

UNPAYWALL_EMAIL = "tomckingsley@gmail.com"
# We send our polite UA when talking to Unpaywall / NCBI etc. — they're fine
# with bots and Unpaywall actually requires the email contact for rate limits.
POLITE_USER_AGENT = "TheRubricGenerator/1.0 (mailto:tomckingsley@gmail.com)"
# Many academic publishers (BMJ, NEJM, Wiley, Springer) hard-403 anything that
# doesn't look like a real browser. When we're downloading what we believe is
# a PDF, use a Chrome UA so we get through. Not impersonating users — just
# speaking the same TLS/headers a librarian's browser would.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30.0
PDF_MAGIC = b"%PDF"
FIRECRAWL_BASE_URL = os.environ.get("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev")
FIRECRAWL_TIMEOUT = 60.0  # JS rendering can take a while
META_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Retry policy
TRANSIENT_HTTP_STATUSES = {429, 502, 503, 504}
RETRY_BACKOFF_SECONDS = (1.0, 2.0)
RETRY_ATTEMPTS = 2

EventCallback = Callable[[dict], None]


def _emit(on_event: Optional[EventCallback], **payload) -> None:
    """Best-effort event callback. Never raises so a logging bug can't kill a fetch."""
    if not on_event:
        return
    try:
        on_event(payload)
    except Exception:
        logger.exception("pdf_fetcher on_event callback raised; ignoring")


def _is_transient_exc(exc: BaseException) -> bool:
    return isinstance(exc, (
        httpx.ConnectError, httpx.ConnectTimeout,
        httpx.ReadTimeout, httpx.WriteTimeout,
        httpx.RemoteProtocolError, httpx.PoolTimeout,
    ))


def _is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:4] == PDF_MAGIC


def _store_pdf_bytes(data: bytes) -> dict:
    sha256 = hashlib.sha256(data).hexdigest()
    filename = f"{sha256}.pdf"
    storage_path = paper_files.write_paper_file(data, filename)
    return {"sha256": sha256, "filename": filename, "storage_path": storage_path}


def _normalize_pdf_url(landing_url: str, pdf_url: str) -> str:
    if pdf_url.startswith("//"):
        return "https:" + pdf_url
    if pdf_url.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(landing_url)
        return f"{u.scheme}://{u.netloc}{pdf_url}"
    return pdf_url


def _run_with_retry(
    strategy_name: str,
    on_event: Optional[EventCallback],
    fn: Callable[[int], tuple],
    attempts: int = RETRY_ATTEMPTS,
    backoff: tuple = RETRY_BACKOFF_SECONDS,
) -> dict | None:
    """Run a strategy fn, emit per-attempt events, retry on transient failures.

    ``fn(attempt_no)`` must return ``(result_dict_or_None, outcome, reason)``
    where outcome ∈ ``{"hit", "miss", "transient_error", "permanent_error"}``
    and reason is a short telemetry string.

    Returns the hit dict (callers attach the ``tier`` key) or ``None`` if every
    attempt missed.
    """
    for i in range(attempts):
        attempt_no = i + 1
        start = time.monotonic()
        try:
            result, outcome, reason = fn(attempt_no)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            outcome = "transient_error" if _is_transient_exc(e) else "permanent_error"
            reason = f"{type(e).__name__}: {str(e)[:120]}"
            logger.info("pdf_fetcher %s attempt %d raised: %s",
                        strategy_name, attempt_no, reason)
            _emit(on_event, strategy=strategy_name, outcome=outcome,
                  reason=reason, duration_ms=duration_ms, attempt=attempt_no)
            if outcome == "transient_error" and attempt_no < attempts:
                time.sleep(backoff[min(i, len(backoff) - 1)])
                continue
            return None
        duration_ms = int((time.monotonic() - start) * 1000)
        _emit(on_event, strategy=strategy_name, outcome=outcome,
              reason=reason, duration_ms=duration_ms, attempt=attempt_no)
        if outcome == "hit":
            return result
        if outcome == "transient_error" and attempt_no < attempts:
            time.sleep(backoff[min(i, len(backoff) - 1)])
            continue
        return None
    return None


# ---------- individual strategies ----------
# Each returns ``(result_dict_or_None, outcome_str, reason_str_or_None)``.
# Transient failures may be raised (httpx exceptions) or returned via the
# ``"transient_error"`` outcome — either way ``_run_with_retry`` retries them.

def _strat_pmc(pmcid: str, dest_dir: Path, attempt: int) -> tuple:
    from . import pubmed
    out = pubmed.download_pmc_pdf(pmcid, dest_dir)
    if not out:
        return None, "miss", "no_pmc_pdf"
    return (
        {
            "sha256": out["sha256"],
            "filename": out["filename"],
            "storage_path": out.get("storage_path"),
        },
        "hit",
        None,
    )


def _strat_direct(client: httpx.Client, url: str, attempt: int,
                  referer: str | None = None) -> tuple:
    headers = {"Referer": referer} if referer else None
    r = client.get(url, headers=headers)
    if r.status_code in TRANSIENT_HTTP_STATUSES:
        return None, "transient_error", f"http_{r.status_code}"
    if r.status_code != 200:
        return None, "miss", f"http_{r.status_code}"
    ct = (r.headers.get("content-type") or "").lower()
    if "pdf" not in ct and not url.lower().endswith(".pdf"):
        return None, "miss", "not_pdf_content_type"
    if not _is_pdf_bytes(r.content):
        return None, "miss", "not_pdf_magic"
    return _store_pdf_bytes(r.content), "hit", None


def _strat_meta_tag(client: httpx.Client, landing_url: str, attempt: int) -> tuple:
    r = client.get(landing_url)
    if r.status_code in TRANSIENT_HTTP_STATUSES:
        return None, "transient_error", f"http_{r.status_code}"
    if r.status_code != 200:
        return None, "miss", f"http_{r.status_code}"
    ct = (r.headers.get("content-type") or "").lower()
    if "html" not in ct:
        return None, "miss", "not_html"
    m = META_PDF_RE.search(r.text or "")
    if not m:
        return None, "miss", "no_citation_pdf_url"
    pdf_url = _normalize_pdf_url(landing_url, m.group(1))
    return _strat_direct(client, pdf_url, attempt)


def _firecrawl_scrape(landing_url: str, api_key: str) -> dict | None:
    """POST to Firecrawl /v1/scrape. Raises on transient httpx errors so the
    enclosing retry loop catches them. Returns ``None`` on permanent miss."""
    with httpx.Client(timeout=FIRECRAWL_TIMEOUT) as fc:
        r = fc.post(
            f"{FIRECRAWL_BASE_URL.rstrip('/')}/v1/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": landing_url,
                "formats": ["html", "rawHtml"],
                "onlyMainContent": False,
            },
        )
    if r.status_code in TRANSIENT_HTTP_STATUSES:
        # Raise so _run_with_retry classifies as transient + retries.
        r.raise_for_status()
    if r.status_code != 200:
        logger.info("Firecrawl scrape failed %s: %s", r.status_code, r.text[:200])
        return None
    body = r.json()
    if not body.get("success"):
        logger.info("Firecrawl returned success=false: %s", str(body)[:200])
        return None
    data = body.get("data") or {}
    return {
        "html": data.get("rawHtml") or data.get("html") or "",
        "metadata": data.get("metadata") or {},
    }


def _strat_firecrawl(client: httpx.Client, landing_url: str, attempt: int) -> tuple:
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return None, "permanent_error", "no_firecrawl_api_key"
    scraped = _firecrawl_scrape(landing_url, api_key)
    if not scraped:
        return None, "miss", "firecrawl_no_data"
    pdf_url = scraped["metadata"].get("citation_pdf_url")
    if not pdf_url:
        m = META_PDF_RE.search(scraped["html"] or "")
        if m:
            pdf_url = m.group(1)
    if not pdf_url:
        return None, "miss", "firecrawl_no_pdf_url"
    pdf_url = _normalize_pdf_url(landing_url, pdf_url)
    out, outcome, reason = _strat_direct(client, pdf_url, attempt)
    if outcome == "hit":
        return out, "hit", None
    # First fetch missed — try once more with the landing URL as Referer
    # (BMJ/NEJM gate on this). Reuses the same client/cookies.
    return _strat_direct(client, pdf_url, attempt, referer=landing_url)


def _strat_browser(landing_url: str, attempt: int) -> tuple:
    from . import browser_agent
    out = browser_agent.fetch_pdf_via_browser(landing_url)
    if not out:
        return None, "miss", "browser_no_pdf"
    return out, "hit", None


def _unpaywall_lookup(client: httpx.Client, doi: str) -> dict | None:
    """Return Unpaywall's ``best_oa_location`` dict, or None. Metadata call —
    not retried (failure usually means the paper isn't in Unpaywall's index)."""
    api = f"https://api.unpaywall.org/v2/{doi}"
    try:
        r = client.get(api, params={"email": UNPAYWALL_EMAIL})
        if r.status_code != 200:
            return None
        body = r.json()
    except Exception as e:
        logger.info("Unpaywall lookup failed for %s: %s", doi, e)
        return None
    return body.get("best_oa_location") or None


# ---------- main orchestrator ----------

def fetch_pdf_for_result(
    result: dict,
    dest_dir: Path,
    use_firecrawl: bool = False,
    use_browser: bool = False,
    on_event: Optional[EventCallback] = None,
) -> dict | None:
    """Try every strategy in order; return the first PDF we land on, else None.

    ``result`` should expose ``pmcid``, ``doi``, ``url`` (any may be missing).
    With ``use_firecrawl=True`` an extra Firecrawl JS-rendering tier runs.
    With ``use_browser=True`` a final Playwright/Chromium tier runs.

    The return dict gains a ``tier`` key (``"free" | "firecrawl" | "browser"``)
    so callers can compute tiered credit costs in auto mode.

    Each strategy emits a single event via ``on_event(payload)`` if provided.
    """

    def _hit(out: dict, tier: str) -> dict:
        return {**out, "tier": tier}

    pmcid = (result.get("pmcid") or "").strip()
    if pmcid:
        out = _run_with_retry(
            "pmc", on_event,
            lambda attempt: _strat_pmc(pmcid, dest_dir, attempt),
            attempts=1,  # PMC is metadata-driven; retry won't change the answer
        )
        if out:
            return _hit(out, "free")

    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(
        follow_redirects=True, timeout=HTTP_TIMEOUT, headers=headers
    ) as client:
        unpaywall_loc = None
        doi = (result.get("doi") or "").strip()
        if doi:
            t0 = time.monotonic()
            unpaywall_loc = _unpaywall_lookup(client, doi)
            _emit(on_event,
                  strategy="unpaywall_lookup",
                  outcome="hit" if unpaywall_loc else "miss",
                  reason=None if unpaywall_loc else "no_oa_location",
                  duration_ms=int((time.monotonic() - t0) * 1000),
                  attempt=1)
            if unpaywall_loc:
                pdf_url = unpaywall_loc.get("url_for_pdf")
                if pdf_url:
                    out = _run_with_retry(
                        "unpaywall_direct", on_event,
                        lambda attempt: _strat_direct(client, pdf_url, attempt),
                    )
                    if out:
                        return _hit(out, "free")
                    referer = unpaywall_loc.get("url")
                    if referer and referer != pdf_url:
                        out = _run_with_retry(
                            "unpaywall_referer", on_event,
                            lambda attempt: _strat_direct(
                                client, pdf_url, attempt, referer=referer
                            ),
                        )
                        if out:
                            return _hit(out, "free")

        url = (result.get("url") or "").strip()
        if url:
            if url.lower().endswith(".pdf"):
                out = _run_with_retry(
                    "result_url_pdf", on_event,
                    lambda attempt: _strat_direct(client, url, attempt),
                )
                if out:
                    return _hit(out, "free")

            out = _run_with_retry(
                "meta_tag", on_event,
                lambda attempt: _strat_meta_tag(client, url, attempt),
            )
            if out:
                return _hit(out, "free")

            out = _run_with_retry(
                "result_url_direct", on_event,
                lambda attempt: _strat_direct(client, url, attempt),
            )
            if out:
                return _hit(out, "free")

            if use_firecrawl or use_browser:
                # Prefer the Unpaywall publisher landing URL — it usually has
                # citation_pdf_url. The PubMed URL rarely does + tends to 403.
                landing = (unpaywall_loc or {}).get("url") if unpaywall_loc else None

                if use_firecrawl:
                    for try_url, label in (
                        (landing, "firecrawl_landing"),
                        (url, "firecrawl_url"),
                    ):
                        if not try_url:
                            continue
                        out = _run_with_retry(
                            label, on_event,
                            lambda attempt, _u=try_url: _strat_firecrawl(
                                client, _u, attempt
                            ),
                        )
                        if out:
                            return _hit(out, "firecrawl")

                if use_browser:
                    for try_url, label in (
                        (landing, "browser_landing"),
                        (url, "browser_url"),
                    ):
                        if not try_url:
                            continue
                        out = _run_with_retry(
                            label, on_event,
                            lambda attempt, _u=try_url: _strat_browser(_u, attempt),
                            attempts=1,  # browser is slow + RAM-hungry; one shot only
                        )
                        if out:
                            return _hit(out, "browser")

    return None
