"""Daily challenge scheduler. Background asyncio task that fires once per
calendar day (UTC) to fetch fresh PubMed papers, create a challenge, and
run all three frontier models against it.

Design:
- A single asyncio loop checks every CHECK_INTERVAL_SECONDS
- Fires only if scheduler_state.last_daily_run_date != today (UTC)
- Writes last_daily_run_date BEFORE invoking the orchestrator to prevent
  double-fires if the run takes longer than the check interval
- DAILY_ENABLED env var can disable automatic fires (manual trigger still works)
- Hardcoded max 10 papers per run
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import challenges as bench
from . import pubmed

logger = logging.getLogger("rubricgen")

CHECK_INTERVAL_SECONDS = int(os.environ.get("DAILY_CHECK_INTERVAL_SECONDS", "600"))
DAILY_MAX_PAPERS = min(int(os.environ.get("DAILY_MAX_PAPERS", "10")), 10)

DAILY_FRONTIER_MODELS = ["gpt-4o", "gemini-2.5-pro", "claude-sonnet-4-20250514"]


def _is_enabled() -> bool:
    return os.environ.get("DAILY_ENABLED", "true").lower() in ("true", "1", "yes")


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM scheduler_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            """INSERT INTO scheduler_state (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
            (key, value),
        )
        conn.commit()


def get_scheduler_status(get_db_fn) -> dict:
    """Return current scheduler state for the admin panel."""
    conn = get_db_fn()
    try:
        last = _get_state(conn, "last_daily_run_date")
        last_challenge_id = _get_state(conn, "last_daily_challenge_id")
    finally:
        conn.close()
    today = _today_iso()
    theme = pubmed.theme_for_date(today)
    return {
        "enabled": _is_enabled(),
        "today_utc": today,
        "last_run_date": last,
        "already_ran_today": last == today,
        "today_theme": theme,
        "last_challenge_id": int(last_challenge_id) if last_challenge_id else None,
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        "max_papers": DAILY_MAX_PAPERS,
        "frontier_models": DAILY_FRONTIER_MODELS,
    }


def run_daily_challenge(get_db_fn, papers_dir: Path, vault_dir: Path,
                        system_user_id: int, date_iso: str | None = None,
                        force: bool = False) -> dict:
    """
    Execute the daily flow:
    1. Pick theme for date
    2. Fetch papers from PubMed
    3. Insert them into the papers table owned by the system user
    4. Create a daily challenge
    5. Launch background run
    6. Record state

    Returns {ok, challenge_id, paper_count, theme_name, message}

    If force=False (normal scheduler path), early-exits when today already ran.
    """
    date_iso = date_iso or _today_iso()
    conn = get_db_fn()
    try:
        if not force:
            last = _get_state(conn, "last_daily_run_date")
            if last == date_iso:
                return {"ok": False, "message": "already ran today", "date": date_iso}

        theme = pubmed.theme_for_date(date_iso)
        logger.info("Daily run: date=%s theme=%r", date_iso, theme["name"])

        # Write the date marker early so concurrent/background invocations don't double-fire
        _set_state(conn, "last_daily_run_date", date_iso)
    finally:
        conn.close()

    # Fetch papers (no DB connection held during network IO)
    papers = pubmed.fetch_papers_for_theme(theme, DAILY_MAX_PAPERS, papers_dir)
    if not papers:
        logger.error("Daily run: no papers fetched for theme %r; aborting", theme["name"])
        # Reset the date marker so we retry on the next tick
        conn = get_db_fn()
        try:
            with conn:
                conn.execute(
                    "DELETE FROM scheduler_state WHERE key='last_daily_run_date'"
                )
                conn.commit()
        finally:
            conn.close()
        return {"ok": False, "message": "no papers fetched", "theme": theme["name"]}

    # Insert papers + build paper_ids list
    conn = get_db_fn()
    paper_ids: list[int] = []
    try:
        for p in papers:
            existing = conn.execute(
                "SELECT id FROM papers WHERE sha256=? AND user_id=?",
                (p["sha256"], system_user_id),
            ).fetchone()
            if existing:
                paper_ids.append(existing["id"])
                continue
            with conn:
                cur = conn.execute(
                    """INSERT INTO papers (filename, disk_filename, sha256, user_id)
                       VALUES (?,?,?,?)""",
                    (p["filename"], p["disk_filename"], p["sha256"], system_user_id),
                )
                paper_ids.append(cur.lastrowid)
                conn.commit()
    finally:
        conn.close()

    if not paper_ids:
        return {"ok": False, "message": "no paper IDs after insert", "theme": theme["name"]}

    # Create the challenge
    title = f"Daily Challenge — {theme['name']} — {date_iso}"
    try:
        challenge_id = bench.create_challenge(
            get_db_fn, system_user_id, title, theme["name"],
            paper_ids, DAILY_FRONTIER_MODELS,
            visibility="private",
            kind="daily",
        )
    except ValueError as e:
        logger.error("Daily run: create_challenge failed: %s", e)
        return {"ok": False, "message": f"create_challenge failed: {e}", "theme": theme["name"]}

    # Persist the challenge id for the admin panel
    conn = get_db_fn()
    try:
        _set_state(conn, "last_daily_challenge_id", str(challenge_id))
    finally:
        conn.close()

    # Launch the orchestrator (background thread)
    bench.run_challenge_async(get_db_fn, challenge_id, papers_dir, vault_dir)

    logger.info("Daily run: challenge %d launched with %d papers", challenge_id, len(paper_ids))
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "paper_count": len(paper_ids),
        "theme_name": theme["name"],
        "date": date_iso,
    }


def maybe_run_daily(get_db_fn, papers_dir: Path, vault_dir: Path,
                    system_user_id: int) -> None:
    """One tick: check if today's run is pending; fire if so."""
    if not _is_enabled():
        return
    today = _today_iso()
    conn = get_db_fn()
    try:
        last = _get_state(conn, "last_daily_run_date")
    finally:
        conn.close()
    if last == today:
        return
    try:
        run_daily_challenge(get_db_fn, papers_dir, vault_dir, system_user_id, date_iso=today)
    except Exception as e:
        logger.error("maybe_run_daily tick failed: %s", e, exc_info=True)


async def daily_loop(get_db_fn, papers_dir: Path, vault_dir: Path,
                     system_user_id: int) -> None:
    """Long-running asyncio task. Started from FastAPI lifespan on app startup."""
    logger.info(
        "Daily scheduler started (enabled=%s, interval=%ds, max_papers=%d)",
        _is_enabled(), CHECK_INTERVAL_SECONDS, DAILY_MAX_PAPERS,
    )
    # Brief initial delay so startup isn't blocked on the first tick
    await asyncio.sleep(30)
    while True:
        try:
            maybe_run_daily(get_db_fn, papers_dir, vault_dir, system_user_id)
        except Exception as e:
            logger.error("Scheduler loop error: %s", e, exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
