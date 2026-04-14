"""Literature Search — AI-powered multi-database search with chatbot.

Provides session-based conversational search where an AI strategist helps
build PICO-based queries, executes searches against PubMed and Europe PMC,
generates link-out URLs for other databases, and supports importing results
as papers into the existing AI Researcher system.
"""

import hashlib
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .helpers import call_anthropic, strip_markdown_fences
from .pubmed import (
    _apikey_param, _http_get, EUTILS_SLEEP, PUBMED_EUTILS,
    fetch_pmids_metadata, fetch_citation_counts, download_pmc_pdf,
    USER_AGENT,
)

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

SEARCH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS search_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT 'New Search',
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    pico_json   TEXT,
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ss_user ON search_sessions(user_id);

CREATE TABLE IF NOT EXISTS search_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
    content       TEXT    NOT NULL,
    metadata_json TEXT,
    created_at    TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sm_session ON search_messages(session_id);

CREATE TABLE IF NOT EXISTS search_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    query_version   INTEGER NOT NULL DEFAULT 0,
    database_name   TEXT    NOT NULL DEFAULT 'pubmed',
    pmid            TEXT,
    doi             TEXT,
    title           TEXT    NOT NULL,
    authors         TEXT,
    journal         TEXT,
    pub_date        TEXT,
    abstract        TEXT,
    pmcid           TEXT,
    citation_count  INTEGER DEFAULT 0,
    url             TEXT,
    selected        INTEGER DEFAULT 0,
    imported        INTEGER DEFAULT 0,
    paper_id        INTEGER REFERENCES papers(id) ON DELETE SET NULL,
    fetched_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sr_session ON search_results(session_id);
CREATE INDEX IF NOT EXISTS idx_sr_session_ver ON search_results(session_id, query_version);
"""


# ─────────────────────────────────────────────
# Session CRUD
# ─────────────────────────────────────────────

def create_session(conn: sqlite3.Connection, user_id: int,
                   title: str = "New Search") -> dict:
    with conn:
        cur = conn.execute(
            "INSERT INTO search_sessions (title, user_id) VALUES (?, ?)",
            (title[:200], user_id),
        )
        conn.commit()
    return get_session(conn, cur.lastrowid, user_id)


def list_sessions(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT ss.id, ss.title, ss.project_id, ss.updated_at,
                  (SELECT COUNT(*) FROM search_messages WHERE session_id = ss.id) AS message_count,
                  (SELECT COUNT(*) FROM search_results WHERE session_id = ss.id) AS result_count
           FROM search_sessions ss
           WHERE ss.user_id = ?
           ORDER BY ss.updated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(conn: sqlite3.Connection, session_id: int,
                user_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM search_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Search session not found")
    result = dict(row)
    try:
        result["pico"] = json.loads(result.pop("pico_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        result["pico"] = {}
    result["messages"] = get_messages(conn, session_id)

    # Latest results
    latest_ver = conn.execute(
        "SELECT MAX(query_version) AS v FROM search_results WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    ver = latest_ver["v"] if latest_ver and latest_ver["v"] is not None else 0
    results = conn.execute(
        "SELECT * FROM search_results WHERE session_id = ? AND query_version = ? ORDER BY citation_count DESC",
        (session_id, ver),
    ).fetchall()
    result["results"] = [dict(r) for r in results]
    result["query_version"] = ver
    return result


def delete_session(conn: sqlite3.Connection, session_id: int,
                   user_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM search_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    with conn:
        conn.execute("DELETE FROM search_sessions WHERE id = ?", (session_id,))
        conn.commit()


def update_session_title(conn: sqlite3.Connection, session_id: int,
                         user_id: int, title: str) -> None:
    row = conn.execute(
        "SELECT id FROM search_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    with conn:
        conn.execute(
            "UPDATE search_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title[:200].strip(), session_id),
        )
        conn.commit()


def update_session_project(conn: sqlite3.Connection, session_id: int,
                           user_id: int, project_id: int | None) -> None:
    row = conn.execute(
        "SELECT id FROM search_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Session not found")
    if project_id is not None:
        # Verify user owns or is a member of the target project
        proj = conn.execute(
            """SELECT 1 FROM projects WHERE id = ? AND user_id = ?
               UNION
               SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?""",
            (project_id, user_id, project_id, user_id),
        ).fetchone()
        if not proj:
            raise HTTPException(403, "You are not a member of this project")
    with conn:
        conn.execute(
            "UPDATE search_sessions SET project_id = ?, updated_at = datetime('now') WHERE id = ?",
            (project_id, session_id),
        )
        conn.commit()


def _generate_session_title(user_message: str) -> str:
    """Generate a short session title from the first user message."""
    text = user_message.strip()
    for prefix in [
        "I'm looking for", "I want to find", "I need to search for",
        "Find me", "Search for", "Can you help me find",
        "Help me search for", "I'm interested in", "Looking for",
        "I need", "I want", "Find",
    ]:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
            break
    # Take first sentence or first 60 chars
    for sep in ['.', '?', '\n']:
        idx = text.find(sep)
        if 0 < idx < 80:
            text = text[:idx]
            break
    title = text[:60].strip()
    if len(text) > 60:
        title += "..."
    return title or "New Search"


# ─────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────

def add_message(conn: sqlite3.Connection, session_id: int, role: str,
                content: str, metadata: dict | None = None) -> dict:
    meta_json = json.dumps(metadata) if metadata else None
    with conn:
        cur = conn.execute(
            "INSERT INTO search_messages (session_id, role, content, metadata_json) VALUES (?, ?, ?, ?)",
            (session_id, role, content, meta_json),
        )
        conn.execute(
            "UPDATE search_sessions SET updated_at = datetime('now') WHERE id = ?",
            (session_id,),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM search_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    result = dict(row)
    try:
        result["metadata"] = json.loads(result.pop("metadata_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        result["metadata"] = {}
    return result


def get_messages(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM search_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json", None) or "{}")
        except (json.JSONDecodeError, TypeError):
            d["metadata"] = {}
        result.append(d)
    return result


# ─────────────────────────────────────────────
# AI Chat
# ─────────────────────────────────────────────

SEARCH_SYSTEM_PROMPT = """You are an expert systematic review search strategist working within a literature search tool. Your job is to help researchers develop comprehensive, reproducible search strategies for biomedical databases.

Your capabilities:
1. Extract PICO elements (Population, Intervention, Comparator, Outcomes) from research questions or protocol descriptions
2. Generate structured Boolean search queries optimized for PubMed using MeSH terms and free-text synonyms
3. Translate queries to other database syntaxes (Ovid MEDLINE, Web of Science, Embase, CINAHL)
4. Refine queries conversationally based on results or user feedback
5. Suggest inclusion/exclusion criteria for screening results

RESPONSE FORMAT:
You MUST respond with valid JSON in this exact structure:

{
  "text": "Your conversational response in markdown format. Explain reasoning, describe what you did, suggest next steps.",
  "pico": {
    "population": "...",
    "intervention": "...",
    "comparator": "...",
    "outcomes": "...",
    "mesh_terms": ["MeSH Term 1", "MeSH Term 2"]
  },
  "search_query": {
    "pubmed": "the full PubMed Boolean query",
    "ovid_medline": "Ovid MEDLINE translation (optional)",
    "web_of_science": "WoS translation (optional)",
    "version_note": "v0: Initial query based on research question"
  },
  "follow_up_questions": [
    "Should we narrow the population to adults only?",
    "Want me to add date restrictions?",
    "Should I translate this to Embase syntax?"
  ]
}

Rules:
- Include "pico" only when you extract or update PICO elements
- Include "search_query" only when you generate or refine a query
- Always include 2-4 "follow_up_questions"
- Wrap MeSH terms: "Neoplasms"[Mesh]
- Use Boolean operators: AND, OR, NOT (capitalized)
- Group related terms with parentheses
- Include both MeSH and free-text variants for comprehensiveness
- Number each query version in version_note (v0, v1, v2...)
- If the user's question is vague, ask clarifying questions in the "text" field
- When refining, explain what changed and why

IMPORTANT — follow_up_questions style:
- follow_up_questions are rendered as clickable buttons the user can tap. They must be specific, actionable choices — NOT open-ended questions.
- Good examples: "Narrow to adults ≥18 years", "Add date filter: last 5 years", "Include observational studies", "Exclude animal studies", "Add outcome: all-cause mortality"
- Bad examples: "What age group are you interested in?", "What outcomes matter most?"
- If you need to ask an open-ended clarifying question, put it in the "text" field as part of your conversational response. The user can always type a free-text reply.
- Think of follow_up_questions as pre-built refinement options the user can click to quickly improve their search."""


def chat(conn: sqlite3.Connection, session_id: int, user_id: int,
         user_message: str) -> dict:
    """Orchestrate a chat turn: save user message, call LLM, parse response,
    save assistant message, update PICO if present."""

    # Save user message
    add_message(conn, session_id, "user", user_message)

    # Auto-title on first user message
    msg_count = conn.execute(
        "SELECT COUNT(*) AS c FROM search_messages WHERE session_id = ? AND role = 'user'",
        (session_id,),
    ).fetchone()["c"]
    if msg_count == 1:
        title = _generate_session_title(user_message)
        with conn:
            conn.execute(
                "UPDATE search_sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            conn.commit()

    # Build messages for Anthropic API
    messages = _build_chat_messages(conn, session_id)

    # Call LLM
    raw = call_anthropic(messages, SEARCH_SYSTEM_PROMPT, max_tokens=4096)
    parsed = _parse_ai_response(raw)

    # Update PICO if present
    if parsed.get("pico"):
        with conn:
            conn.execute(
                "UPDATE search_sessions SET pico_json = ? WHERE id = ?",
                (json.dumps(parsed["pico"]), session_id),
            )
            conn.commit()

    # Save assistant message with metadata
    metadata = {}
    if parsed.get("pico"):
        metadata["pico"] = parsed["pico"]
    if parsed.get("search_query"):
        metadata["search_query"] = parsed["search_query"]
    if parsed.get("follow_up_questions"):
        metadata["follow_ups"] = parsed["follow_up_questions"]

    assistant_msg = add_message(
        conn, session_id, "assistant", parsed.get("text", ""), metadata or None
    )

    return {
        "session_id": session_id,
        "message": assistant_msg,
        "pico": parsed.get("pico"),
        "search_query": parsed.get("search_query"),
        "follow_up_questions": parsed.get("follow_up_questions", []),
    }


def _build_chat_messages(conn: sqlite3.Connection,
                         session_id: int) -> list[dict]:
    """Format message history for Anthropic API."""
    rows = conn.execute(
        "SELECT role, content FROM search_messages WHERE session_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    messages = []
    for r in rows:
        if r["role"] in ("user", "assistant"):
            messages.append({"role": r["role"], "content": r["content"]})
    return messages


def _parse_ai_response(raw: str) -> dict:
    """Parse AI response. Expects JSON; graceful fallback to plain text."""
    cleaned = strip_markdown_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return {
            "text": raw,
            "follow_up_questions": [
                "Can you describe your research question?",
                "What population are you studying?",
            ],
        }


# ─────────────────────────────────────────────
# Search execution
# ─────────────────────────────────────────────

LINKOUT_URLS = {
    "scholar": "https://scholar.google.com/scholar?q={query}",
    "jstor": "https://www.jstor.org/action/doBasicSearch?Query={query}",
    "wos": "https://www.webofscience.com/wos/woscc/basic-search",
    "sciencedirect": "https://www.sciencedirect.com/search?qs={query}",
    "wiley": "https://onlinelibrary.wiley.com/action/doSearch?AllField={query}",
    "ovid": None,  # No public URL; syntax only
}


def execute_search(conn: sqlite3.Connection, session_id: int, user_id: int,
                   database: str, query: str,
                   page: int = 1, page_size: int = 50) -> dict:
    """Run a search with pagination. PubMed and Europe PMC return results; others return link-out URLs."""
    # Verify session ownership
    sess = conn.execute(
        "SELECT id FROM search_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if not sess:
        raise HTTPException(404, "Session not found")

    # Determine query version (only increment on page 1 / new search)
    latest = conn.execute(
        "SELECT MAX(query_version) AS v FROM search_results WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if page == 1:
        version = (latest["v"] or 0) + 1 if latest and latest["v"] is not None else 0
    else:
        version = latest["v"] if latest and latest["v"] is not None else 0

    offset = (page - 1) * page_size

    if database == "pubmed":
        result = _search_pubmed(query, page_size, offset)
        articles = result["articles"]
        total = result["total"]
        if articles:
            save_results(conn, session_id, version, "pubmed", articles)
        total_pages = (total + page_size - 1) // page_size if total else 1
        return {"count": len(articles), "total": total, "page": page,
                "total_pages": total_pages, "results": articles,
                "query_version": version, "database": "pubmed"}

    elif database == "europe_pmc":
        result = _search_europe_pmc(query, page_size, offset)
        articles = result["articles"]
        total = result["total"]
        if articles:
            save_results(conn, session_id, version, "europe_pmc", articles)
        total_pages = (total + page_size - 1) // page_size if total else 1
        return {"count": len(articles), "total": total, "page": page,
                "total_pages": total_pages, "results": articles,
                "query_version": version, "database": "europe_pmc"}

    else:
        # Link-out databases
        linkout_url = _build_linkout_url(database, query)
        return {
            "count": 0,
            "results": [],
            "query_version": version,
            "database": database,
            "linkout_url": linkout_url,
            "message": f"Full search on {database} requires opening the database directly. "
                       f"Your query has been formatted for this database.",
        }


def _search_pubmed(query: str, page_size: int = 50,
                   offset: int = 0) -> dict:
    """Search PubMed with pagination. Returns {articles, total}."""
    encoded = urllib.parse.quote(query)
    url = (
        f"{PUBMED_EUTILS}/esearch.fcgi?db=pubmed&term={encoded}"
        f"&retmax={page_size}&retstart={offset}&retmode=json{_apikey_param()}"
    )
    time.sleep(EUTILS_SLEEP)
    raw = _http_get(url)
    data = json.loads(raw)
    esearch = data.get("esearchresult", {})
    pmids = esearch.get("idlist", [])
    total = int(esearch.get("count", 0))

    if not pmids:
        return {"articles": [], "total": total}

    # Fetch metadata
    metas = _fetch_full_metadata(pmids)

    # Fetch citation counts
    cites = fetch_citation_counts(pmids)
    for m in metas:
        m["citation_count"] = cites.get(m["pmid"], 0)

    return {"articles": metas, "total": total}


def _fetch_full_metadata(pmids: list[str]) -> list[dict]:
    """Fetch metadata including abstract via efetch."""
    if not pmids:
        return []

    # First get basic metadata via esummary
    ids = ",".join(pmids)
    url = (
        f"{PUBMED_EUTILS}/esummary.fcgi?db=pubmed&id={ids}"
        f"&retmode=json{_apikey_param()}"
    )
    time.sleep(EUTILS_SLEEP)
    raw = _http_get(url)
    data = json.loads(raw)
    result_data = data.get("result", {})
    uids = result_data.get("uids", [])

    # Build metadata map
    meta_map: dict[str, dict] = {}
    for uid in uids:
        rec = result_data.get(uid, {})
        pmcid = None
        doi = None
        for aid in rec.get("articleids", []):
            if aid.get("idtype") == "pmc":
                pmcid = aid.get("value", "")
                if pmcid and not pmcid.startswith("PMC"):
                    pmcid = "PMC" + pmcid
            if aid.get("idtype") == "doi":
                doi = aid.get("value", "")
        authors = ", ".join(
            a.get("name", "") for a in rec.get("authors", [])[:5]
        )
        if len(rec.get("authors", [])) > 5:
            authors += " et al."
        meta_map[uid] = {
            "pmid": uid,
            "title": rec.get("title", "").strip(),
            "authors": authors,
            "journal": rec.get("fulljournalname", "") or rec.get("source", ""),
            "pub_date": rec.get("pubdate", ""),
            "pmcid": pmcid,
            "doi": doi,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
        }

    # Fetch abstracts via efetch
    time.sleep(EUTILS_SLEEP)
    try:
        efetch_url = (
            f"{PUBMED_EUTILS}/efetch.fcgi?db=pubmed&id={ids}"
            f"&rettype=abstract&retmode=xml{_apikey_param()}"
        )
        xml_raw = _http_get(efetch_url).decode("utf-8", errors="replace")
        _parse_abstracts_into(xml_raw, meta_map)
    except Exception as e:
        logger.warning("Failed to fetch abstracts: %s", e)

    return [meta_map[uid] for uid in uids if uid in meta_map]


def _parse_abstracts_into(xml_str: str, meta_map: dict) -> None:
    """Parse abstracts from efetch XML into meta_map entries."""
    import re
    # Simple regex parsing — avoid XML library dependency
    articles = re.findall(r'<PubmedArticle>(.*?)</PubmedArticle>', xml_str, re.DOTALL)
    for article in articles:
        pmid_match = re.search(r'<PMID[^>]*>(\d+)</PMID>', article)
        if not pmid_match:
            continue
        pmid = pmid_match.group(1)
        if pmid not in meta_map:
            continue
        # Extract abstract text
        abstract_match = re.search(r'<Abstract>(.*?)</Abstract>', article, re.DOTALL)
        if abstract_match:
            abstract_xml = abstract_match.group(1)
            # Remove XML tags, keep text
            abstract_text = re.sub(r'<[^>]+>', ' ', abstract_xml).strip()
            abstract_text = re.sub(r'\s+', ' ', abstract_text)
            meta_map[pmid]["abstract"] = abstract_text


def _search_europe_pmc(query: str, page_size: int = 50,
                       offset: int = 0) -> dict:
    """Search Europe PMC REST API with pagination. Returns {articles, total}."""
    encoded = urllib.parse.quote(query)
    # Europe PMC uses cursorMark for deep pagination, but offset works for first 1000
    page_num = (offset // page_size) + 1 if page_size else 1
    url = (
        f"https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={encoded}&format=json&pageSize={min(page_size, 1000)}&page={page_num}"
    )
    try:
        raw = _http_get(url)
        data = json.loads(raw)
    except Exception as e:
        logger.error("Europe PMC search failed: %s", e)
        return {"articles": [], "total": 0}

    total = int(data.get("hitCount", 0))
    articles = []
    for rec in data.get("resultList", {}).get("result", []):
        authors = rec.get("authorString", "")
        articles.append({
            "pmid": rec.get("pmid", ""),
            "doi": rec.get("doi", ""),
            "title": rec.get("title", "").strip(),
            "authors": authors[:200] if authors else "",
            "journal": rec.get("journalTitle", ""),
            "pub_date": rec.get("firstPublicationDate", ""),
            "abstract": rec.get("abstractText", ""),
            "pmcid": rec.get("pmcid", ""),
            "url": f"https://europepmc.org/article/MED/{rec.get('pmid', '')}" if rec.get("pmid") else "",
            "citation_count": rec.get("citedByCount", 0),
        })
    return {"articles": articles, "total": total}


def _build_linkout_url(database: str, query: str) -> str | None:
    """Build a link-out URL for databases without API access."""
    template = LINKOUT_URLS.get(database)
    if template is None:
        return None
    return template.format(query=urllib.parse.quote(query))


def save_results(conn: sqlite3.Connection, session_id: int,
                 query_version: int, database: str,
                 articles: list[dict]) -> int:
    """Cache search results in the database. Returns count saved."""
    count = 0
    with conn:
        for a in articles:
            conn.execute(
                """INSERT INTO search_results
                   (session_id, query_version, database_name, pmid, doi, title,
                    authors, journal, pub_date, abstract, pmcid, citation_count, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id, query_version, database,
                    a.get("pmid", ""), a.get("doi", ""),
                    a.get("title", ""), a.get("authors", ""),
                    a.get("journal", ""), a.get("pub_date", ""),
                    a.get("abstract", ""), a.get("pmcid", ""),
                    a.get("citation_count", 0), a.get("url", ""),
                ),
            )
            count += 1
        conn.commit()
    return count


# ─────────────────────────────────────────────
# Selection
# ─────────────────────────────────────────────

def toggle_result_selection(conn: sqlite3.Connection, result_ids: list[int],
                            selected: bool) -> None:
    if not result_ids:
        return
    placeholders = ",".join("?" * len(result_ids))
    with conn:
        conn.execute(
            f"UPDATE search_results SET selected = ? WHERE id IN ({placeholders})",
            [1 if selected else 0] + result_ids,
        )
        conn.commit()


def select_all_results(conn: sqlite3.Connection, session_id: int,
                       query_version: int, selected: bool) -> None:
    with conn:
        conn.execute(
            "UPDATE search_results SET selected = ? WHERE session_id = ? AND query_version = ?",
            (1 if selected else 0, session_id, query_version),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────

def import_results(conn: sqlite3.Connection, session_id: int,
                   result_ids: list[int], user_id: int,
                   papers_dir: Path,
                   project_id: int | None = None) -> dict:
    """Import selected search results as papers."""
    imported = 0
    skipped = 0
    failed = 0
    paper_ids = []

    for rid in result_ids:
        r = conn.execute(
            "SELECT * FROM search_results WHERE id = ? AND session_id = ?",
            (rid, session_id),
        ).fetchone()
        if not r:
            continue
        if r["imported"]:
            skipped += 1
            continue

        pdf_path = None
        sha256 = None

        # Try to download PDF if PMCID available
        if r["pmcid"]:
            try:
                pdf_path = download_pmc_pdf(r["pmcid"], papers_dir)
            except Exception as e:
                logger.warning("PDF download failed for %s: %s", r["pmcid"], e)

        if pdf_path:
            sha256 = pdf_path.stem  # filename is {sha256}.pdf
            filename = f"{r['pmcid']}_{r['title'][:60].replace(' ', '_')}.pdf"
        else:
            # No PDF available — create a placeholder record
            placeholder = f"pubmed:{r['pmid'] or r['doi'] or r['title'][:50]}"
            sha256 = hashlib.sha256(placeholder.encode()).hexdigest()
            filename = f"{r['title'][:80].replace(' ', '_')}.pdf"

        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM papers WHERE sha256 = ? AND user_id = ?",
            (sha256, user_id),
        ).fetchone()

        if existing:
            paper_id = existing["id"]
        else:
            try:
                with conn:
                    cur = conn.execute(
                        """INSERT INTO papers (filename, disk_filename, sha256, user_id, project_id)
                           VALUES (?, ?, ?, ?, ?)""",
                        (filename, f"{sha256}.pdf" if pdf_path else None, sha256, user_id, project_id),
                    )
                    paper_id = cur.lastrowid
                    conn.commit()
            except Exception as e:
                logger.error("Failed to import paper: %s", e)
                failed += 1
                continue

        # Mark as imported
        with conn:
            conn.execute(
                "UPDATE search_results SET imported = 1, paper_id = ? WHERE id = ?",
                (paper_id, rid),
            )
            conn.commit()

        paper_ids.append(paper_id)
        imported += 1

    return {"imported": imported, "skipped": skipped, "failed": failed, "paper_ids": paper_ids}


# ─────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────

def export_ris(conn: sqlite3.Connection, session_id: int,
               result_ids: list[int]) -> str:
    """Export selected results as RIS format."""
    if not result_ids:
        return ""
    placeholders = ",".join("?" * len(result_ids))
    rows = conn.execute(
        f"SELECT * FROM search_results WHERE id IN ({placeholders}) AND session_id = ?",
        result_ids + [session_id],
    ).fetchall()

    lines = []
    for r in rows:
        lines.append("TY  - JOUR")
        lines.append(f"TI  - {r['title']}")
        for author in (r["authors"] or "").split(","):
            a = author.strip()
            if a and a != "et al.":
                lines.append(f"AU  - {a}")
        lines.append(f"JO  - {r['journal'] or ''}")
        lines.append(f"PY  - {(r['pub_date'] or '')[:4]}")
        if r["doi"]:
            lines.append(f"DO  - {r['doi']}")
        if r["pmid"]:
            lines.append(f"AN  - PMID:{r['pmid']}")
        if r["abstract"]:
            lines.append(f"AB  - {r['abstract'][:2000]}")
        if r["url"]:
            lines.append(f"UR  - {r['url']}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


def export_bibtex(conn: sqlite3.Connection, session_id: int,
                  result_ids: list[int]) -> str:
    """Export selected results as BibTeX format."""
    if not result_ids:
        return ""
    placeholders = ",".join("?" * len(result_ids))
    rows = conn.execute(
        f"SELECT * FROM search_results WHERE id IN ({placeholders}) AND session_id = ?",
        result_ids + [session_id],
    ).fetchall()

    entries = []
    for r in rows:
        key = f"pmid{r['pmid']}" if r["pmid"] else f"result{r['id']}"
        authors = (r["authors"] or "").replace(",", " and")
        year = (r["pub_date"] or "")[:4]
        entry = (
            f"@article{{{key},\n"
            f"  title = {{{{{r['title']}}}}},\n"
            f"  author = {{{authors}}},\n"
            f"  journal = {{{r['journal'] or ''}}},\n"
            f"  year = {{{year}}},\n"
        )
        if r["doi"]:
            entry += f"  doi = {{{r['doi']}}},\n"
        if r["pmid"]:
            entry += f"  pmid = {{{r['pmid']}}},\n"
        entry += "}"
        entries.append(entry)
    return "\n\n".join(entries)
