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
     "query": "cancer AND (randomized OR randomised) AND clinical trial[pt]",
     "description": "Randomized trials in oncology"},
    {"name": "Cardiovascular cohort studies",
     "query": "cardiovascular AND cohort AND prospective",
     "description": "Prospective cohort studies of cardiovascular disease"},
    {"name": "Diagnostic accuracy",
     "query": "(sensitivity OR specificity) AND diagnostic AND accuracy",
     "description": "Diagnostic test accuracy studies"},
    {"name": "Meta-analyses",
     "query": "meta-analysis[pt] AND systematic review",
     "description": "Systematic reviews and meta-analyses"},
    {"name": "Prediction models",
     "query": "prediction model AND (validation OR development) AND cohort",
     "description": "Clinical prediction model development and validation"},
    {"name": "Infectious disease",
     "query": "infectious disease AND (epidemiology OR clinical trial)",
     "description": "Infectious disease research"},
    {"name": "Mental health interventions",
     "query": "(depression OR anxiety) AND (randomized OR intervention)",
     "description": "Mental health intervention trials"},
    {"name": "Pediatric clinical research",
     "query": "(pediatric OR children) AND clinical trial[pt]",
     "description": "Pediatric clinical research"},
    {"name": "Geriatric research",
     "query": "(elderly OR older adults OR geriatric) AND cohort",
     "description": "Research in older adult populations"},
    {"name": "Drug safety",
     "query": "(adverse event OR drug safety OR pharmacovigilance) AND cohort",
     "description": "Drug safety and pharmacoepidemiology"},
    {"name": "Surgical outcomes",
     "query": "surgical AND (outcomes OR randomized) AND clinical trial",
     "description": "Surgical intervention studies"},
    {"name": "Cost-effectiveness",
     "query": "(cost-effectiveness OR cost-utility) AND (QALY OR quality-adjusted)",
     "description": "Health economics evaluations"},
    {"name": "Clinical guidelines",
     "query": "guideline[pt] AND systematic review",
     "description": "Clinical practice guidelines"},
    {"name": "Global health",
     "query": "(low-income OR middle-income OR developing country) AND clinical trial",
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


def search_pubmed(query: str, retmax: int = 100, days_back: int = 3650) -> list[str]:
    """esearch.fcgi → list of PMIDs in PMC OA subset, within date window."""
    today = datetime.now(timezone.utc).date()
    mindate = (today - timedelta(days=days_back)).strftime("%Y/%m/%d")
    maxdate = today.strftime("%Y/%m/%d")
    full_query = f'({query}) AND "open access"[filter] AND hasabstract AND pubmed pmc[sb]'
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
        if not pmcid:
            continue
        metas.append({
            "pmid": uid,
            "pmcid": pmcid,
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
                           min_citations: int = 10) -> list[dict]:
    """
    Full flow for a daily run:
    1. esearch returns up to 100 PMIDs matching theme query
    2. esummary fetches metadata (links each to PMCID if in PMC)
    3. iCite gives citation counts → keep only those with >= min_citations
    4. For each surviving paper, attempt PMC PDF download
    5. Stop once we have `count` successful downloads

    Returns list of {pmid, pmcid, title, journal, citation_count, disk_path, sha256, filename}.
    """
    query = theme["query"]
    logger.info("fetch_papers_for_theme: theme=%r query=%r", theme["name"], query)

    pmids = search_pubmed(query, retmax=100)
    logger.info("  esearch returned %d PMIDs", len(pmids))
    if not pmids:
        return []

    metas = fetch_pmids_metadata(pmids)
    logger.info("  %d papers have linked PMCIDs", len(metas))
    if not metas:
        return []

    citation_map = fetch_citation_counts([m["pmid"] for m in metas])
    # Attach citation counts and filter
    filtered = []
    for m in metas:
        cc = citation_map.get(m["pmid"], 0)
        if cc >= min_citations:
            m["citation_count"] = cc
            filtered.append(m)
    logger.info("  %d papers pass citation threshold (>=%d)", len(filtered), min_citations)

    if not filtered:
        # Fallback tier 1: lower threshold to 5
        logger.warning("  no papers met threshold (>=%d), trying >=5", min_citations)
        filtered = [
            dict(m, citation_count=citation_map.get(m["pmid"], 0))
            for m in metas if citation_map.get(m["pmid"], 0) >= 5
        ]
    if not filtered:
        # Fallback tier 2: lower threshold to 1 (any cited paper)
        logger.warning("  still no papers at >=5, trying >=1")
        filtered = [
            dict(m, citation_count=citation_map.get(m["pmid"], 0))
            for m in metas if citation_map.get(m["pmid"], 0) >= 1
        ]
    if not filtered:
        # Fallback tier 3: accept any paper with a PMCID regardless of citations
        logger.warning("  no cited papers at all, accepting any PMC OA paper")
        filtered = [dict(m, citation_count=0) for m in metas]

    # Sort by citations desc (prefer higher-impact papers)
    filtered.sort(key=lambda m: m.get("citation_count", 0), reverse=True)

    papers: list[dict] = []
    for m in filtered:
        if len(papers) >= count:
            break
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
