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

Every download is validated by checking that the first 4 bytes equal
``b"%PDF"`` — rejects HTML disguised as PDFs, paywalls, captcha pages.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import httpx

from . import paper_files

logger = logging.getLogger("rubricgen")

UNPAYWALL_EMAIL = "tomckingsley@gmail.com"
USER_AGENT = "TheRubricGenerator/1.0 (mailto:tomckingsley@gmail.com)"
HTTP_TIMEOUT = 30.0
PDF_MAGIC = b"%PDF"
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


def _try_unpaywall(client: httpx.Client, doi: str) -> dict | None:
    api = f"https://api.unpaywall.org/v2/{doi}"
    try:
        r = client.get(api, params={"email": UNPAYWALL_EMAIL})
        if r.status_code != 200:
            return None
        body = r.json()
    except Exception as e:
        logger.info("Unpaywall lookup failed for %s: %s", doi, e)
        return None
    loc = body.get("best_oa_location") or {}
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


def fetch_pdf_for_result(result: dict, dest_dir: Path) -> dict | None:
    """Try every strategy in order; return the first PDF we land on, else None.

    ``result`` should expose ``pmcid``, ``doi``, ``url`` (any may be missing).
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
        doi = (result.get("doi") or "").strip()
        if doi:
            out = _try_unpaywall(client, doi)
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
            # Last resort: maybe the URL itself is a PDF behind a redirect.
            out = _try_direct(client, url)
            if out:
                return out

    return None
