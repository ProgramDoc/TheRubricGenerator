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
    id          SERIAL PRIMARY KEY,
    title       TEXT    NOT NULL DEFAULT 'New Search',
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id  INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    pico_json   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ss_user ON search_sessions(user_id);

CREATE TABLE IF NOT EXISTS search_messages (
    id            SERIAL PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,
    role          TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
    content       TEXT    NOT NULL,
    metadata_json TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sm_session ON search_messages(session_id);

CREATE TABLE IF NOT EXISTS search_results (
    id              SERIAL PRIMARY KEY,
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
    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sr_session ON search_results(session_id);
CREATE INDEX IF NOT EXISTS idx_sr_session_ver ON search_results(session_id, query_version);

CREATE TABLE IF NOT EXISTS pdf_fetch_runs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    session_id      INTEGER NOT NULL,
    project_id      INTEGER,
    result_ids_json TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'running',
    mode            TEXT    NOT NULL DEFAULT 'fetch',
    credit_per_paper INTEGER NOT NULL DEFAULT 2,
    total           INTEGER NOT NULL,
    succeeded       INTEGER NOT NULL DEFAULT 0,
    failed          INTEGER NOT NULL DEFAULT 0,
    refunded        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pfr_user ON pdf_fetch_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_pfr_session ON pdf_fetch_runs(session_id);

CREATE TABLE IF NOT EXISTS pdf_fetch_run_events (
    id          SERIAL PRIMARY KEY,
    run_id      INTEGER NOT NULL,
    event_type  TEXT    NOT NULL,
    message     TEXT,
    detail_json TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pfre_run ON pdf_fetch_run_events(run_id);
"""


# ─────────────────────────────────────────────
# Session CRUD
# ─────────────────────────────────────────────

def create_session(conn, user_id: int,
                   title: str = "New Search") -> dict:
    with conn:
        cur = conn.execute(
            "INSERT INTO search_sessions (title, user_id) VALUES (?, ?) RETURNING id",
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
            "UPDATE search_sessions SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
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
            "UPDATE search_sessions SET project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
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

def add_message(conn, session_id: int, role: str,
                content: str, metadata: dict | None = None) -> dict:
    meta_json = json.dumps(metadata) if metadata else None
    with conn:
        cur = conn.execute(
            "INSERT INTO search_messages (session_id, role, content, metadata_json) VALUES (?, ?, ?, ?) RETURNING id",
            (session_id, role, content, meta_json),
        )
        conn.execute(
            "UPDATE search_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
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
    """Cache search results in the database. Stamps the inserted DB row id back
    onto each article (as ``a["id"]``) so the response shape lets the frontend
    bind checkboxes to the same id ``/api/search/import`` expects.
    Returns count saved."""
    count = 0
    with conn:
        for a in articles:
            cur = conn.execute(
                """INSERT INTO search_results
                   (session_id, query_version, database_name, pmid, doi, title,
                    authors, journal, pub_date, abstract, pmcid, citation_count, url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (
                    session_id, query_version, database,
                    a.get("pmid", ""), a.get("doi", ""),
                    a.get("title", ""), a.get("authors", ""),
                    a.get("journal", ""), a.get("pub_date", ""),
                    a.get("abstract", ""), a.get("pmcid", ""),
                    a.get("citation_count", 0), a.get("url", ""),
                ),
            )
            a["id"] = cur.lastrowid
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

def _external_url_for(r) -> str | None:
    """Pick the best click-out URL for a search result (PubMed pointer preferred)."""
    url = (r["url"] or "").strip() if r["url"] else ""
    if url:
        return url
    if r["pmid"]:
        return f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/"
    if r["doi"]:
        return f"https://doi.org/{r['doi']}"
    return None


def _insert_metadata_paper(conn, r, user_id: int, project_id: int | None) -> int | None:
    """Create a metadata-only papers row for a search result. Returns paper id."""
    placeholder = f"pubmed:{r['pmid'] or r['doi'] or r['title'][:50]}"
    sha256 = hashlib.sha256(placeholder.encode()).hexdigest()
    filename = f"{r['title'][:80].replace(' ', '_')}.pdf"
    external_url = _external_url_for(r)

    existing = conn.execute(
        "SELECT id FROM papers WHERE sha256 = ? AND user_id = ?",
        (sha256, user_id),
    ).fetchone()
    if existing:
        return existing["id"]
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                                       user_id, project_id, source, external_url, pdf_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'search', ?, 'metadata_only') RETURNING id""",
                (filename, None, None, sha256, user_id, project_id, external_url),
            )
            paper_id = cur.lastrowid
            conn.commit()
        return paper_id
    except Exception as e:
        logger.error("Failed to insert metadata paper: %s", e)
        return None


def _insert_pdf_paper(conn, r, user_id: int, project_id: int | None,
                      pdf_result: dict) -> int | None:
    """Create a PDF-backed papers row from a successful pdf_fetcher result."""
    sha256 = pdf_result["sha256"]
    pmcid_pref = r["pmcid"] or "paper"
    filename = f"{pmcid_pref}_{r['title'][:60].replace(' ', '_')}.pdf"
    existing = conn.execute(
        "SELECT id FROM papers WHERE sha256 = ? AND user_id = ?",
        (sha256, user_id),
    ).fetchone()
    if existing:
        return existing["id"]
    try:
        with conn:
            cur = conn.execute(
                """INSERT INTO papers (filename, disk_filename, storage_path, sha256,
                                       user_id, project_id, source, external_url, pdf_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'search', ?, 'present') RETURNING id""",
                (
                    filename,
                    pdf_result["filename"],
                    pdf_result.get("storage_path"),
                    sha256,
                    user_id,
                    project_id,
                    _external_url_for(r),
                ),
            )
            paper_id = cur.lastrowid
            conn.commit()
        return paper_id
    except Exception as e:
        logger.error("Failed to insert PDF paper: %s", e)
        return None


def _upgrade_paper_to_pdf(conn, paper_id: int, r, pdf_result: dict) -> int | None:
    """Turn an existing metadata-only / fetch_failed papers row into a
    PDF-backed one. Updates filename, sha256, storage_path, disk_filename,
    and pdf_status='present' atomically. The id is preserved so other tables
    (annotations, rubrics, etc.) keep their references."""
    sha256 = pdf_result["sha256"]
    pmcid_pref = r["pmcid"] or "paper"
    filename = f"{pmcid_pref}_{r['title'][:60].replace(' ', '_')}.pdf"
    try:
        with conn:
            conn.execute(
                """UPDATE papers
                   SET filename = ?, disk_filename = ?, storage_path = ?,
                       sha256 = ?, pdf_status = 'present'
                   WHERE id = ?""",
                (
                    filename,
                    pdf_result["filename"],
                    pdf_result.get("storage_path"),
                    sha256,
                    paper_id,
                ),
            )
            conn.commit()
        return paper_id
    except Exception as e:
        logger.error("Failed to upgrade paper %s to PDF: %s", paper_id, e)
        return None


def _insert_failed_fetch_paper(conn, r, user_id: int,
                               project_id: int | None) -> int | None:
    """Same as metadata-only but marks pdf_status='fetch_failed' so the UI can
    distinguish 'we tried and missed' from 'never asked for a PDF'."""
    paper_id = _insert_metadata_paper(conn, r, user_id, project_id)
    if paper_id:
        try:
            with conn:
                conn.execute(
                    "UPDATE papers SET pdf_status = 'fetch_failed' WHERE id = ?",
                    (paper_id,),
                )
                conn.commit()
        except Exception:
            pass
    return paper_id


def import_results(conn: sqlite3.Connection, session_id: int,
                   result_ids: list[int], user_id: int,
                   papers_dir: Path,
                   project_id: int | None = None,
                   mode: str = "metadata") -> dict:
    """Import selected search results as papers (synchronous, metadata-only).

    For ``mode='fetch'``, the HTTP layer enqueues a background worker
    (:func:`run_pdf_fetch_job`) instead of calling this function — that path
    needs progress events and a daemon thread.
    """
    if mode != "metadata":
        raise ValueError("import_results only handles mode='metadata'; use run_pdf_fetch_job for 'fetch'")

    imported = 0
    skipped = 0
    failed = 0
    paper_ids: list[int] = []

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

        paper_id = _insert_metadata_paper(conn, r, user_id, project_id)
        if paper_id is None:
            failed += 1
            continue

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
# Background PDF-fetch worker
# ─────────────────────────────────────────────

def log_pdf_fetch_event(conn, run_id: int, event_type: str, message: str,
                        detail: dict | None = None) -> None:
    """Append a progress event for a pdf-fetch run. Best-effort — never raises."""
    try:
        conn.execute(
            """INSERT INTO pdf_fetch_run_events (run_id, event_type, message, detail_json)
               VALUES (?, ?, ?, ?)""",
            (run_id, event_type, message, json.dumps(detail) if detail else None),
        )
        conn.commit()
    except Exception as e:
        logger.warning("pdf_fetch_run_event log failed (run=%s type=%s): %s",
                       run_id, event_type, e)


def create_pdf_fetch_run(conn, user_id: int, session_id: int,
                         result_ids: list[int],
                         project_id: int | None,
                         mode: str = "fetch",
                         credit_per_paper: int = 2) -> int:
    with conn:
        cur = conn.execute(
            """INSERT INTO pdf_fetch_runs (user_id, session_id, project_id,
                                            result_ids_json, total, mode,
                                            credit_per_paper)
               VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (user_id, session_id, project_id, json.dumps(result_ids),
             len(result_ids), mode, credit_per_paper),
        )
        run_id = cur.lastrowid
        conn.commit()
    return run_id


def run_pdf_fetch_job(get_conn, run_id: int, papers_dir: Path,
                      refund_callback=None) -> None:
    """Background worker for the 'Get PDF' import mode.

    ``get_conn`` is a zero-arg callable that returns a fresh DB connection
    (so the worker can run on a daemon thread without sharing the request's
    connection). ``refund_callback(user_id, credits, reason)`` is invoked
    for each per-paper failure to refund pre-charged credits.
    """
    conn = get_conn()
    try:
        run = conn.execute("SELECT * FROM pdf_fetch_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return
        user_id = run["user_id"]
        session_id = run["session_id"]
        project_id = run["project_id"]
        result_ids = json.loads(run["result_ids_json"])
        try:
            mode = run["mode"] or "fetch"
        except Exception:
            mode = "fetch"
        try:
            credit_per_paper = run["credit_per_paper"] or 2
        except Exception:
            credit_per_paper = 2
        use_firecrawl = mode in ("firecrawl", "browser", "auto")
        use_browser = mode in ("browser", "auto")
        is_auto = (mode == "auto")

        # Auto mode pre-charges 15 cr/paper (browser tier) and refunds the
        # excess based on which tier actually delivered the PDF. Other modes
        # are flat per-mode pricing — no tier-based refund.
        TIER_EFFECTIVE_COST = {"free": 2, "firecrawl": 5, "browser": 15}

        if is_auto:
            extra = " (auto: free → Firecrawl → browser, tier-priced)"
        elif use_browser:
            extra = " (Firecrawl + browser-agent fallback)"
        elif use_firecrawl:
            extra = " (Firecrawl fallback enabled)"
        else:
            extra = ""
        log_pdf_fetch_event(conn, run_id, "run_started",
                            f"Fetching PDFs for {len(result_ids)} results" + extra,
                            {"mode": mode, "credit_per_paper": credit_per_paper})

        from . import pdf_fetcher

        succeeded = 0
        failed = 0
        refunded = 0

        for rid in result_ids:
            r = conn.execute(
                "SELECT * FROM search_results WHERE id = ? AND session_id = ?",
                (rid, session_id),
            ).fetchone()
            if not r:
                continue

            # Decide whether to skip already-imported results. We *don't* skip
            # if the linked paper is metadata-only or fetch_failed — those are
            # exactly the rows a fetch / firecrawl re-run is meant to upgrade.
            existing_paper = None
            if r["imported"] and r["paper_id"]:
                existing_paper = conn.execute(
                    "SELECT id, pdf_status FROM papers WHERE id = ?",
                    (r["paper_id"],),
                ).fetchone()
                if existing_paper and existing_paper["pdf_status"] == "present":
                    continue  # already has a real PDF, nothing to do

            log_pdf_fetch_event(conn, run_id, "result_started",
                                f"Fetching: {r['title'][:80]}",
                                {"result_id": rid, "pmid": r["pmid"], "doi": r["doi"],
                                 "upgrading": bool(existing_paper)})

            def _strategy_event_handler(payload, _rid=rid, _title=r["title"]):
                """Forward each pdf_fetcher per-strategy event into the run log."""
                strategy = payload.get("strategy", "?")
                outcome = payload.get("outcome", "?")
                try:
                    log_pdf_fetch_event(
                        conn, run_id, "strategy_attempt",
                        f"{strategy}: {outcome}",
                        {"result_id": _rid, **payload},
                    )
                except Exception as e:
                    logger.warning("strategy_event log failed: %s", e)

            try:
                result_dict = {
                    "pmcid": r["pmcid"],
                    "doi": r["doi"],
                    "url": r["url"],
                    "pmid": r["pmid"],
                    "title": r["title"],
                }
                pdf_result = pdf_fetcher.fetch_pdf_for_result(
                    result_dict, papers_dir,
                    use_firecrawl=use_firecrawl,
                    use_browser=use_browser,
                    on_event=_strategy_event_handler,
                )
            except Exception as e:
                logger.exception("pdf_fetcher crashed for result %s: %s", rid, e)
                pdf_result = None

            if pdf_result:
                tier = pdf_result.get("tier") or "browser"
                if existing_paper:
                    # Upgrade in place: turn the metadata-only row into a
                    # PDF-backed one without changing its id (which other
                    # tables reference).
                    paper_id = _upgrade_paper_to_pdf(conn, existing_paper["id"], r, pdf_result)
                else:
                    paper_id = _insert_pdf_paper(conn, r, user_id, project_id, pdf_result)
                if paper_id:
                    with conn:
                        conn.execute(
                            "UPDATE search_results SET imported = 1, paper_id = ? WHERE id = ?",
                            (paper_id, rid),
                        )
                        conn.commit()
                    succeeded += 1

                    # Tier-based refund (auto mode only). Pre-charge was 15
                    # cr/paper; refund the excess so a free-tier hit costs 2 cr
                    # and a Firecrawl-tier hit costs 5 cr.
                    tier_refund = 0
                    if is_auto and refund_callback:
                        effective = TIER_EFFECTIVE_COST.get(tier, credit_per_paper)
                        excess = credit_per_paper - effective
                        if excess > 0:
                            try:
                                refund_callback(user_id, excess,
                                                f"pdf_fetch_tier_{tier}_result_{rid}")
                                tier_refund = excess
                            except Exception as e:
                                logger.warning(
                                    "Tier refund failed for run=%s result=%s: %s",
                                    run_id, rid, e)

                    log_pdf_fetch_event(conn, run_id, "result_done",
                                        f"PDF fetched [{tier}]: {r['title'][:80]}"
                                        + (" (upgraded)" if existing_paper else ""),
                                        {"result_id": rid, "paper_id": paper_id,
                                         "upgraded": bool(existing_paper),
                                         "tier": tier,
                                         "tier_refund": tier_refund})
                    continue
                # insert/upgrade failed → fall through to failure path
                pdf_result = None

            # Failure path: keep / create the metadata-only row.
            if existing_paper:
                # Already have a metadata row — just leave it as-is, no DB churn.
                paper_id = existing_paper["id"]
            else:
                paper_id = _insert_failed_fetch_paper(conn, r, user_id, project_id)
                if paper_id:
                    with conn:
                        conn.execute(
                            "UPDATE search_results SET imported = 1, paper_id = ? WHERE id = ?",
                            (paper_id, rid),
                        )
                        conn.commit()
            failed += 1
            if refund_callback:
                try:
                    refund_callback(user_id, credit_per_paper,
                                    f"pdf_fetch_failed_result_{rid}")
                    refunded += 1
                except Exception as e:
                    logger.warning("Refund failed for run=%s result=%s: %s", run_id, rid, e)
            log_pdf_fetch_event(conn, run_id, "result_failed",
                                f"PDF unavailable — saved metadata only: {r['title'][:80]}",
                                {"result_id": rid, "paper_id": paper_id})

        with conn:
            conn.execute(
                """UPDATE pdf_fetch_runs
                   SET status = 'complete', succeeded = ?, failed = ?, refunded = ?,
                       completed_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (succeeded, failed, refunded, run_id),
            )
            conn.commit()
        log_pdf_fetch_event(conn, run_id, "run_complete",
                            f"Done: {succeeded} fetched, {failed} metadata-only",
                            {"succeeded": succeeded, "failed": failed, "refunded": refunded})
    except Exception as e:
        logger.exception("run_pdf_fetch_job crashed for run=%s: %s", run_id, e)
        try:
            with conn:
                conn.execute(
                    "UPDATE pdf_fetch_runs SET status = 'failed', completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (run_id,),
                )
                conn.commit()
            log_pdf_fetch_event(conn, run_id, "run_complete",
                                f"Run failed: {e}",
                                {"error": str(e)})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_pdf_fetch_run(conn, run_id: int, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM pdf_fetch_runs WHERE id = ? AND user_id = ?",
        (run_id, user_id),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def get_pdf_fetch_events(conn, run_id: int, user_id: int,
                         after: int = 0) -> list[dict]:
    # Auth via the run owner:
    owner = conn.execute(
        "SELECT user_id FROM pdf_fetch_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if not owner or owner["user_id"] != user_id:
        return []
    rows = conn.execute(
        """SELECT id, event_type, message, detail_json, created_at
           FROM pdf_fetch_run_events
           WHERE run_id = ? AND id > ?
           ORDER BY id ASC""",
        (run_id, after),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.pop("detail_json") or "null")
        except Exception:
            d["detail"] = None
        out.append(d)
    return out


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
