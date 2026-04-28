"""Best-effort PDF fetcher for search-result imports.

Used by the Search Strategist's "Get PDF" import mode. Given a search-result
row (PMID / DOI / PMCID / URL), tries multiple strategies in order to locate an
open-access PDF and download it. First hit wins; returns ``None`` on miss so
callers can fall back to a metadata-only paper row.

Strategy order:

1. **PMC**  — if ``pmcid`` is set, reuse :func:`backend.pubmed.download_pmc_pdf`.
2. **Unpaywall** — if ``doi`` is set, GET ``api.unpaywall.org/v2/{doi}`` and try
   ``best_oa_location.url_for_pdf``. Free, no key, covers most OA papers.
3. **Direct URL** — if ``url`` ends in ``.pdf`` or returns
   ``Content-Type: application/pdf``, fetch it directly.
4. **Meta-tag fallback** — GET ``url`` (HTML), look for
   ``<meta name="citation_pdf_url">`` (Springer / BMJ / NEJM emit this).
5. **Firecrawl** (opt-in via ``mode='firecrawl'``) — when 1-4 all miss, ask
   Firecrawl (api.firecrawl.dev) to render the landing page with a real
   browser. Many publishers block plain ``httpx`` user-agents but reveal the
   ``citation_pdf_url`` meta tag once JS runs. Requires ``FIRECRAWL_API_KEY``.

Every download is validated by checking that the first 4 bytes equal
``b"%PDF"`` — rejects HTML disguised as PDFs, paywalls, captcha pages.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import httpx

from . import paper_files

logger = logging.getLogger("rubricgen")

UNPAYWALL_EMAIL = "tomckingsley@gmail.com"
USER_AGENT = "TheRubricGenerator/1.0 (mailto:tomckingsley@gmail.com)"
HTTP_TIMEOUT = 30.0
PDF_MAGIC = b"%PDF"
FIRECRAWL_BASE_URL = os.environ.get("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev")
FIRECRAWL_TIMEOUT = 60.0  # JS rendering can take a while
META_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:4] == PDF_MAGIC


def _store_pdf_bytes(data: bytes) -> dict:
    sha256 = hashlib.sha256(data).hexdigest()
    filename = f"{sha256}.pdf"
    storage_path = paper_files.write_paper_file(data, filename)
    return {"sha256": sha256, "filename": filename, "storage_path": storage_path}


def _try_pmc(pmcid: str, dest_dir: Path) -> dict | None:
    try:
        from . import pubmed
        out = pubmed.download_pmc_pdf(pmcid, dest_dir)
    except Exception as e:
        logger.warning("PMC fetch failed for %s: %s", pmcid, e)
        return None
    if not out:
        return None
    return {
        "sha256": out["sha256"],
        "filename": out["filename"],
        "storage_path": out.get("storage_path"),
    }


def _unpaywall_lookup(client: httpx.Client, doi: str) -> dict | None:
    """Return Unpaywall's ``best_oa_location`` dict, or None if missing/error.

    The dict typically has ``url_for_pdf`` (direct PDF link) and ``url`` (HTML
    landing page). Either may be useful: the PDF URL for a fast download, the
    landing URL for a Firecrawl JS-rendered fallback when direct download
    is blocked.
    """
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


def _try_unpaywall(client: httpx.Client, doi: str) -> dict | None:
    loc = _unpaywall_lookup(client, doi)
    if not loc:
        return None
    pdf_url = loc.get("url_for_pdf")
    if not pdf_url:
        return None
    return _try_direct(client, pdf_url)


def _try_direct(client: httpx.Client, url: str) -> dict | None:
    try:
        r = client.get(url)
    except Exception as e:
        logger.info("Direct GET failed for %s: %s", url, e)
        return None
    if r.status_code != 200:
        return None
    ct = (r.headers.get("content-type") or "").lower()
    if "pdf" not in ct and not url.lower().endswith(".pdf"):
        return None
    if not _is_pdf_bytes(r.content):
        return None
    return _store_pdf_bytes(r.content)


def _try_meta_tag(client: httpx.Client, landing_url: str) -> dict | None:
    try:
        r = client.get(landing_url)
    except Exception as e:
        logger.info("Landing GET failed for %s: %s", landing_url, e)
        return None
    if r.status_code != 200:
        return None
    ct = (r.headers.get("content-type") or "").lower()
    if "html" not in ct:
        return None
    m = META_PDF_RE.search(r.text or "")
    if not m:
        return None
    pdf_url = m.group(1)
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(landing_url)
        pdf_url = f"{u.scheme}://{u.netloc}{pdf_url}"
    return _try_direct(client, pdf_url)


def _firecrawl_scrape(landing_url: str, api_key: str) -> dict | None:
    """Ask Firecrawl to render the landing page and return its rendered HTML
    + parsed metadata. Returns ``{html, metadata}`` or ``None`` on failure."""
    try:
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
    except Exception as e:
        logger.warning("Firecrawl call failed for %s: %s", landing_url, e)
        return None


def _try_firecrawl(client: httpx.Client, landing_url: str) -> dict | None:
    """JS-render the landing page via Firecrawl, then look for citation_pdf_url
    or any embedded PDF link. Many paywalled publishers reveal the link only
    after their script runs.

    Skipped silently if ``FIRECRAWL_API_KEY`` is unset — caller should check
    that beforehand if it wants a hard error.
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return None
    scraped = _firecrawl_scrape(landing_url, api_key)
    if not scraped:
        return None
    # Firecrawl normalizes some metadata into a dict — try that first.
    pdf_url = scraped["metadata"].get("citation_pdf_url")
    if not pdf_url:
        m = META_PDF_RE.search(scraped["html"] or "")
        if m:
            pdf_url = m.group(1)
    if not pdf_url:
        return None
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(landing_url)
        pdf_url = f"{u.scheme}://{u.netloc}{pdf_url}"
    return _try_direct(client, pdf_url)


def fetch_pdf_for_result(result: dict, dest_dir: Path,
                         use_firecrawl: bool = False) -> dict | None:
    """Try every strategy in order; return the first PDF we land on, else None.

    ``result`` should expose ``pmcid``, ``doi``, ``url`` (any may be missing).
    With ``use_firecrawl=True``, an additional Firecrawl JS-rendering step is
    run as a final fallback for landing pages that block plain ``httpx``.
    Returns ``{sha256, filename, storage_path}`` shape on success, ``None`` on
    every-strategy-failed.
    """
    pmcid = result.get("pmcid")
    if pmcid:
        out = _try_pmc(pmcid, dest_dir)
        if out:
            return out

    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"}
    with httpx.Client(
        follow_redirects=True, timeout=HTTP_TIMEOUT, headers=headers
    ) as client:
        # Cache Unpaywall's response — used twice (PDF URL + landing URL fallback).
        unpaywall_loc = None
        doi = (result.get("doi") or "").strip()
        if doi:
            unpaywall_loc = _unpaywall_lookup(client, doi)
            if unpaywall_loc:
                pdf_url = unpaywall_loc.get("url_for_pdf")
                if pdf_url:
                    out = _try_direct(client, pdf_url)
                    if out:
                        return out
                    # Direct PDF URL was 403'd — try Firecrawl on it directly,
                    # which uses a real browser session.
                    if use_firecrawl:
                        out = _firecrawl_direct_pdf(pdf_url)
                        if out:
                            return out

        url = (result.get("url") or "").strip()
        if url:
            if url.lower().endswith(".pdf"):
                out = _try_direct(client, url)
                if out:
                    return out
            out = _try_meta_tag(client, url)
            if out:
                return out
            # Last resort plain: maybe the URL itself is a PDF behind a redirect.
            out = _try_direct(client, url)
            if out:
                return out
            # Final aggressive fallback: render with Firecrawl. Prefer the
            # Unpaywall landing URL (publisher's article page) over the
            # PubMed URL — PubMed rarely exposes citation_pdf_url, but
            # publisher landing pages do.
            if use_firecrawl:
                landing = (unpaywall_loc or {}).get("url") if unpaywall_loc else None
                for try_url in (landing, url):
                    if not try_url:
                        continue
                    out = _try_firecrawl(client, try_url)
                    if out:
                        return out

    return None


def _firecrawl_direct_pdf(pdf_url: str) -> dict | None:
    """Fetch a PDF URL through Firecrawl when plain ``httpx`` gets a 403.

    Firecrawl's scrape endpoint follows JS challenges and bot-protection
    (Cloudflare, akamai, etc.) for the URL. We ask for the raw HTML output
    — when the URL is a PDF, Firecrawl returns the PDF bytes encoded in the
    response body. We then validate %PDF magic bytes.
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=FIRECRAWL_TIMEOUT) as fc:
            r = fc.post(
                f"{FIRECRAWL_BASE_URL.rstrip('/')}/v1/scrape",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": pdf_url, "formats": ["rawHtml"]},
            )
        if r.status_code != 200:
            logger.info("Firecrawl direct-PDF failed %s: %s", r.status_code, r.text[:200])
            return None
        body = r.json()
        if not body.get("success"):
            return None
        raw = (body.get("data") or {}).get("rawHtml") or ""
        # Firecrawl base64-encodes binary if it can't render as text. For now
        # we only accept clear-text %PDF in the rawHtml — most paywall PDFs
        # come back as text/markdown after rendering, not binary. If this
        # path proves insufficient we'll add base64 detection.
        if not raw.startswith("%PDF"):
            return None
        return _store_pdf_bytes(raw.encode("latin-1", errors="ignore"))
    except Exception as e:
        logger.warning("Firecrawl direct-PDF crashed for %s: %s", pdf_url, e)
        return None
