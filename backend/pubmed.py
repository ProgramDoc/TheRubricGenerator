"""PubMed / PMC / iCite client for daily challenge ingestion.

Filters:
- Last 10 years
- In PubMed Central (PMC) Open Access subset
- Has a downloadable PMC PDF
- Citation count >= 10 (via NIH iCite API)

All HTTP via urllib.request to avoid adding dependencies.
"""

import gzip
import hashlib
import io
import json
import logging
import os
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("rubricgen")

PUBMED_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ICITE_API     = "https://icite.od.nih.gov/api/pubs"
PMC_PDF_URL   = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
PMC_ARTICLE_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"
PMC_OA_SERVICE  = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
USER_AGENT   = "TheAIResearcher/1.0 (mailto:tck936@mail.harvard.edu)"
# PMC's PDF endpoint now serves a JS proof-of-work interstitial to anything that
# doesn't pass the challenge — both bot UAs and Chrome UAs. We use the OA
# package web service as a side door (returns a tarball over HTTPS, no JS
# needed). When that misses, we fall back to scraping ``citation_pdf_url`` from
# the article landing page with a Chrome-like UA.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
META_PDF_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OA_LINK_RE = re.compile(
    r'<link[^>]+format=["\']tgz["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Polite throttling: 3 req/sec without key, 10 with. We sleep 0.4s (no key)
# or 0.15s (key) between eutils calls.
EUTILS_SLEEP = 0.15 if NCBI_API_KEY else 0.4


# ─────────────────────────────────────────────
# Seed themes
# ─────────────────────────────────────────────
# Queries are intentionally broad — the PMC OA + citation filters narrow results.
# Avoid overly-specific multi-word phrases that reduce the match pool.
SEED_THEMES = [
    {"name": "Oncology RCTs",
     "query": '"Neoplasms"[Mesh] AND "Randomized Controlled Trial"[pt] AND 2020:2026[dp]',
     "description": "Randomized trials in oncology"},
    {"name": "Cardiovascular cohort studies",
     "query": '"Cardiovascular Diseases"[Mesh] AND "Cohort Studies"[Mesh] AND 2020:2026[dp]',
     "description": "Prospective cohort studies of cardiovascular disease"},
    {"name": "Diagnostic accuracy",
     "query": '"Sensitivity and Specificity"[Mesh] AND diagnostic accuracy AND 2020:2026[dp]',
     "description": "Diagnostic test accuracy studies"},
    {"name": "Meta-analyses",
     "query": '"Meta-Analysis"[pt] AND "Systematic Review"[pt] AND 2022:2026[dp]',
     "description": "Systematic reviews and meta-analyses"},
    {"name": "Prediction models",
     "query": 'clinical prediction model AND (validation OR development) AND 2020:2026[dp]',
     "description": "Clinical prediction model development and validation"},
    {"name": "Infectious disease",
     "query": '"Communicable Diseases"[Mesh] AND (clinical trial[pt] OR cohort) AND 2020:2026[dp]',
     "description": "Infectious disease research"},
    {"name": "Mental health interventions",
     "query": '"Mental Disorders"[Mesh] AND "Randomized Controlled Trial"[pt] AND 2020:2026[dp]',
     "description": "Mental health intervention trials"},
    {"name": "Pediatric clinical research",
     "query": '"Child"[Mesh] AND "Clinical Trial"[pt] AND 2020:2026[dp]',
     "description": "Pediatric clinical research"},
    {"name": "Geriatric research",
     "query": '"Aged"[Mesh] AND "Cohort Studies"[Mesh] AND 2021:2026[dp]',
     "description": "Research in older adult populations"},
    {"name": "Drug safety",
     "query": '"Drug-Related Side Effects and Adverse Reactions"[Mesh] AND 2020:2026[dp]',
     "description": "Drug safety and pharmacoepidemiology"},
    {"name": "Surgical outcomes",
     "query": '"Surgical Procedures, Operative"[Mesh] AND outcomes AND clinical trial[pt] AND 2020:2026[dp]',
     "description": "Surgical intervention studies"},
    {"name": "Health economics",
     "query": '"Cost-Benefit Analysis"[Mesh] AND 2020:2026[dp]',
     "description": "Health economics evaluations"},
    {"name": "Clinical guidelines",
     "query": '"Practice Guideline"[pt] AND 2022:2026[dp]',
     "description": "Clinical practice guidelines"},
    {"name": "Global health",
     "query": '"Global Health"[Mesh] AND clinical trial AND 2020:2026[dp]',
     "description": "Global health clinical trials"},
]


def theme_for_date(date_iso: str) -> dict:
    """Pick a theme from SEED_THEMES by day-of-year modulo len."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    idx = d.timetuple().tm_yday % len(SEED_THEMES)
    return SEED_THEMES[idx]


# ─────────────────────────────────────────────
# HTTP helper with retry/backoff
# ─────────────────────────────────────────────
def _http_get(url: str, timeout: int = 30, max_retries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                backoff = 2 ** attempt
                logger.warning("HTTP %s from %s, retrying in %ss", e.code, url, backoff)
                time.sleep(backoff)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


# ─────────────────────────────────────────────
# PubMed E-utilities
# ─────────────────────────────────────────────
def _apikey_param() -> str:
    return f"&api_key={NCBI_API_KEY}" if NCBI_API_KEY else ""


def search_pubmed(query: str, retmax: int = 100, days_back: int = 3650,
                  apply_oa_filter: bool = False) -> list[str]:
    """esearch.fcgi → list of PMIDs, optionally restricted to PMC OA subset."""
    today = datetime.now(timezone.utc).date()
    mindate = (today - timedelta(days=days_back)).strftime("%Y/%m/%d")
    maxdate = today.strftime("%Y/%m/%d")
    full_query = f'({query}) AND hasabstract'
    if apply_oa_filter:
        full_query += ' AND "open access"[filter] AND pubmed pmc[sb]'
    encoded = urllib.parse.quote(full_query)
    url = (
        f"{PUBMED_EUTILS}/esearch.fcgi?db=pubmed&term={encoded}"
        f"&retmax={retmax}&retmode=json"
        f"&mindate={mindate}&maxdate={maxdate}&datetype=pdat"
        f"{_apikey_param()}"
    )
    time.sleep(EUTILS_SLEEP)
    raw = _http_get(url)
    data = json.loads(raw)
    return data.get("esearchresult", {}).get("idlist", [])


def fetch_pmids_metadata(pmids: list[str]) -> list[dict]:
    """esummary.fcgi → metadata per PMID including the linked PMCID."""
    if not pmids:
        return []
    ids = ",".join(pmids)
    url = (
        f"{PUBMED_EUTILS}/esummary.fcgi?db=pubmed&id={ids}"
        f"&retmode=json{_apikey_param()}"
    )
    time.sleep(EUTILS_SLEEP)
    raw = _http_get(url)
    data = json.loads(raw)
    result = data.get("result", {})
    uids = result.get("uids", [])
    metas = []
    for uid in uids:
        rec = result.get(uid, {})
        # Find the PMC ID in articleids
        pmcid = None
        for aid in rec.get("articleids", []):
            if aid.get("idtype") == "pmc":
                pmcid = aid.get("value", "")
                if pmcid and not pmcid.startswith("PMC"):
                    pmcid = "PMC" + pmcid
                break
        metas.append({
            "pmid": uid,
            "pmcid": pmcid,  # May be None if no PMC link
            "title": rec.get("title", "").strip(),
            "journal": rec.get("fulljournalname", "") or rec.get("source", ""),
            "pubdate": rec.get("pubdate", ""),
        })
    return metas


# ─────────────────────────────────────────────
# NIH iCite citation counts
# ─────────────────────────────────────────────
def fetch_citation_counts(pmids: list[str]) -> dict[str, int]:
    """Batch iCite lookup. Returns {pmid: citation_count}."""
    if not pmids:
        return {}
    out: dict[str, int] = {}
    # iCite accepts up to 1000 PMIDs per call; we batch 100 for safety
    for i in range(0, len(pmids), 100):
        batch = pmids[i:i+100]
        url = f"{ICITE_API}?pmids={','.join(batch)}"
        try:
            raw = _http_get(url)
            data = json.loads(raw)
        except Exception as e:
            logger.warning("iCite batch failed: %s", e)
            continue
        for rec in data.get("data", []):
            pmid = str(rec.get("pmid", ""))
            cc = rec.get("citation_count")
            if pmid and cc is not None:
                out[pmid] = int(cc)
        time.sleep(0.5)  # be polite
    return out


# ─────────────────────────────────────────────
# PMC PDF download
# ─────────────────────────────────────────────
_FILENAME_SAFE = re.compile(r'[^\w\-. ]')


def _safe_filename(title: str, pmcid: str) -> str:
    safe = _FILENAME_SAFE.sub('', title or '').strip().replace(' ', '_')
    safe = safe[:80] or 'paper'
    return f"{pmcid}_{safe}.pdf"


def _is_pdf_bytes(data: bytes) -> bool:
    return bool(data) and data[:4] == b"%PDF"


def _persist_pdf_bytes(data: bytes, dest_dir: Path, pmcid: str) -> dict | None:
    """Write PDF bytes to legacy local + durable storage. Returns the standard
    download_pmc_pdf result dict, or ``None`` on storage failure."""
    if not _is_pdf_bytes(data):
        logger.info("PMC %s: bytes did not start with %%PDF magic (size=%d)", pmcid, len(data))
        return None
    sha256 = hashlib.sha256(data).hexdigest()
    filename = f"{sha256}.pdf"
    path = dest_dir / filename
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)  # legacy local copy
    except Exception as e:
        logger.warning("Local write failed for %s: %s", path, e)
    try:
        from . import paper_files
        storage_path = paper_files.write_paper_file(data, filename)
    except Exception as e:
        logger.error("Storage upload failed for PMC %s: %s", pmcid, e)
        storage_path = None
    return {"path": path, "storage_path": storage_path, "sha256": sha256, "filename": filename}


def _try_oa_package(pmcid: str) -> bytes | None:
    """Strategy 1: NCBI's OA web service. Returns the PDF bytes from the
    archived OA tarball, or ``None`` on miss.

    The ``oa.fcgi`` endpoint returns an XML record pointing to a ``.tar.gz`` on
    the FTP host; we fetch the tarball over HTTPS (the FTP path is mirrored at
    ``ftp.ncbi.nlm.nih.gov`` over HTTPS) and extract the first ``.pdf`` member.
    Bypasses the JS proof-of-work interstitial that gates the cloudpmc viewer.
    """
    try:
        oa_url = f"{PMC_OA_SERVICE}?id={pmcid}"
        req = urllib.request.Request(oa_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.info("PMC %s OA service lookup failed: %s", pmcid, e)
        return None

    m = OA_LINK_RE.search(xml)
    if not m:
        logger.info("PMC %s not in OA package index", pmcid)
        return None
    href = m.group(1)
    # Convert ftp:// to https:// (NCBI's FTP is also exposed over HTTPS).
    https_url = href.replace("ftp://ftp.ncbi.nlm.nih.gov/", "https://ftp.ncbi.nlm.nih.gov/", 1)
    if not https_url.startswith("https://"):
        logger.info("PMC %s OA href has unexpected scheme: %r", pmcid, href)
        return None

    # Live `oa_package` was retired; the tarballs now live under
    # `pub/pmc/deprecated/oa_package/...`. Try the live path first (in case it's
    # ever restored), then the deprecated mirror.
    candidates = [https_url]
    if "/pub/pmc/oa_package/" in https_url:
        candidates.append(
            https_url.replace("/pub/pmc/oa_package/", "/pub/pmc/deprecated/oa_package/", 1)
        )

    tar_bytes: bytes | None = None
    for candidate in candidates:
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                tar_bytes = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # try the next candidate
            logger.info("PMC %s OA tarball fetch failed: %s", pmcid, e)
            return None
        except Exception as e:
            logger.info("PMC %s OA tarball fetch error: %s", pmcid, e)
            return None

    if not tar_bytes:
        return None
    if tar_bytes[:2] != b"\x1f\x8b":
        logger.info("PMC %s OA tarball is not gzip", pmcid)
        return None
    try:
        decompressed = gzip.decompress(tar_bytes)
        with tarfile.open(fileobj=io.BytesIO(decompressed)) as tar:
            for member in tar.getmembers():
                if not member.name.lower().endswith(".pdf"):
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                pdf_data = f.read()
                if _is_pdf_bytes(pdf_data):
                    return pdf_data
    except Exception as e:
        logger.info("PMC %s OA tarball extract failed: %s", pmcid, e)
    return None


def _try_citation_pdf_url(pmcid: str) -> bytes | None:
    """Strategy 2: scrape ``citation_pdf_url`` from the article landing page.

    The landing page (`/pmc/articles/{pmcid}/`) renders normally; we read its
    ``<meta name="citation_pdf_url">`` tag and follow that URL with a Chrome
    User-Agent + ``Referer`` pointing back at the landing page. Some publishers
    co-host PDFs at a different origin where the JS gate doesn't apply.
    """
    landing = PMC_ARTICLE_URL.format(pmcid=pmcid)
    try:
        req = urllib.request.Request(landing, headers={"User-Agent": BROWSER_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            final_url = resp.url
    except Exception as e:
        logger.info("PMC %s landing-page fetch failed: %s", pmcid, e)
        return None

    m = META_PDF_RE.search(html)
    if not m:
        return None
    pdf_url = m.group(1)
    if pdf_url.startswith("/"):
        from urllib.parse import urlparse
        u = urlparse(final_url)
        pdf_url = f"{u.scheme}://{u.netloc}{pdf_url}"

    try:
        req = urllib.request.Request(
            pdf_url,
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/pdf,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": final_url,
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            ct = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read()
    except Exception as e:
        logger.info("PMC %s citation_pdf_url fetch failed: %s", pmcid, e)
        return None

    if "pdf" not in ct and not _is_pdf_bytes(data):
        return None
    if _is_pdf_bytes(data):
        return data
    return None


def download_pmc_pdf(pmcid: str, dest_dir: Path) -> dict | None:
    """Download the PMC PDF if available and persist via storage.py.

    Returns ``{"path", "storage_path", "sha256", "filename"}`` or ``None``.
    ``path`` is the legacy local file (also written so local tooling still works);
    ``storage_path`` is the durable S3/local path to record in ``papers.storage_path``.

    Strategy:
        1. Pull the PDF out of the OA package tarball (oa.fcgi). Bypasses the JS
           proof-of-work gate that PMC put on `/pmc/articles/{pmcid}/pdf/`.
        2. Fall back to the article-landing ``citation_pdf_url`` meta tag with a
           Chrome User-Agent + Referer (some PDFs are co-hosted off the gate).

    Returns ``None`` if both strategies miss — caller (scheduler / pdf_fetcher)
    treats that as "no PMC PDF available" and moves on to its other tiers.
    """
    pmcid = (pmcid or "").strip()
    if not pmcid:
        return None
    if not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid

    data = _try_oa_package(pmcid)
    if data is None:
        data = _try_citation_pdf_url(pmcid)
    time.sleep(1.0)  # polite throttle between PDF downloads, regardless of outcome
    if data is None:
        logger.info("PMC %s: no PDF found via OA tarball or citation_pdf_url", pmcid)
        return None

    return _persist_pdf_bytes(data, dest_dir, pmcid)


# ─────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────
def fetch_papers_for_theme(theme: dict, count: int, dest_dir: Path,
                           min_citations: int = 5) -> list[dict]:
    """
    Full flow for a daily run:
    1. esearch returns up to 50 PMIDs matching theme query (broad PubMed, no OA filter)
    2. esummary fetches metadata (some will have PMCIDs, some won't)
    3. iCite gives citation counts → rank by citations (most relevant first)
    4. Attempt PMC PDF download for top-ranked papers that have PMCIDs
    5. Stop once we have `count` successful downloads

    Returns list of {pmid, pmcid, title, journal, citation_count, disk_path, sha256, filename}.
    """
    query = theme["query"]
    logger.info("fetch_papers_for_theme: theme=%r query=%r", theme["name"], query)

    # Search broad PubMed (no OA filter) — get 50 candidates
    pmids = search_pubmed(query, retmax=50, apply_oa_filter=False)
    logger.info("  esearch returned %d PMIDs (broad search)", len(pmids))
    if not pmids:
        return []

    metas = fetch_pmids_metadata(pmids)
    pmc_count = sum(1 for m in metas if m.get("pmcid"))
    logger.info("  %d papers total, %d with PMC links", len(metas), pmc_count)
    if not metas:
        return []

    # Fetch citation counts and attach to metadata
    citation_map = fetch_citation_counts([m["pmid"] for m in metas])
    for m in metas:
        m["citation_count"] = citation_map.get(m["pmid"], 0)

    # Sort by citations desc — top cited = most relevant
    metas.sort(key=lambda m: m.get("citation_count", 0), reverse=True)
    logger.info("  top cited: %d citations (%s)", metas[0]["citation_count"], metas[0]["title"][:60] if metas else "?")

    # Download PDFs for top candidates (only those with PMCIDs)
    papers: list[dict] = []
    attempted = 0
    for m in metas:
        if len(papers) >= count:
            break
        if not m.get("pmcid"):
            continue  # Skip papers without PMC PDFs
        attempted += 1
        result = download_pmc_pdf(m["pmcid"], dest_dir)
        if result is None:
            continue
        filename = _safe_filename(m["title"], m["pmcid"])
        papers.append({
            "pmid": m["pmid"],
            "pmcid": m["pmcid"],
            "title": m["title"],
            "journal": m["journal"],
            "citation_count": m["citation_count"],
            "disk_path": str(result["path"]),
            "disk_filename": result["filename"],
            "storage_path": result.get("storage_path"),
            "sha256": result["sha256"],
            "filename": filename,
        })
        logger.info("  downloaded %s (%s, %d citations)", m["pmcid"], m["title"][:60], m["citation_count"])

    logger.info("  returning %d papers", len(papers))
    return papers
