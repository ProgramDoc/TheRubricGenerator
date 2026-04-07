"""PubMed / PMC / iCite client for daily challenge ingestion.

Filters:
- Last 10 years
- In PubMed Central (PMC) Open Access subset
- Has a downloadable PMC PDF
- Citation count >= 10 (via NIH iCite API)

All HTTP via urllib.request to avoid adding dependencies.
"""

import hashlib
import json
import logging
import os
import re
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

NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
USER_AGENT   = "TheAIResearcher/1.0 (mailto:tck936@mail.harvard.edu)"

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


def download_pmc_pdf(pmcid: str, dest_dir: Path) -> Path | None:
    """Download the PMC PDF if available. Returns path or None."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = PMC_PDF_URL.format(pmcid=pmcid)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower():
                logger.info("PMC %s did not return a PDF (content-type=%s)", pmcid, content_type)
                return None
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        logger.warning("PMC %s download failed: %s", pmcid, e)
        return None
    except Exception as e:
        logger.warning("PMC %s download error: %s", pmcid, e)
        return None

    sha256 = hashlib.sha256(data).hexdigest()
    path = dest_dir / f"{sha256}.pdf"
    path.write_bytes(data)
    time.sleep(1.0)  # polite throttle between PDF downloads
    return path


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
        pdf_path = download_pmc_pdf(m["pmcid"], dest_dir)
        if pdf_path is None:
            continue
        sha256 = pdf_path.stem  # filename is <sha256>.pdf
        filename = _safe_filename(m["title"], m["pmcid"])
        papers.append({
            "pmid": m["pmid"],
            "pmcid": m["pmcid"],
            "title": m["title"],
            "journal": m["journal"],
            "citation_count": m["citation_count"],
            "disk_path": str(pdf_path),
            "disk_filename": pdf_path.name,
            "sha256": sha256,
            "filename": filename,
        })
        logger.info("  downloaded %s (%s, %d citations)", m["pmcid"], m["title"][:60], m["citation_count"])

    logger.info("  returning %d papers", len(papers))
    return papers
