"""Phase 6 — Advanced Analytics & Reporting.

Provides per-model performance breakdown by theme/difficulty, historical trend
data, CSV/PDF export, analytics cache refresh, and daily-completion email
notifications.
"""

import csv
import io
import json
import logging
import smtplib
import sqlite3
from datetime import datetime
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger("rubricgen")


# ─────────────────────────────────────────────
# Analytics queries
# ─────────────────────────────────────────────

def get_model_breakdown(
    conn: sqlite3.Connection,
    model_id: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Per-model accuracy breakdown by theme and difficulty.

    Parses rubric_json (for per-question difficulty) and grade_json (for
    per-question score) to compute fine-grained accuracy.  Falls back to the
    analytics_snapshots cache when available and no date filters are applied.
    """
    rows = _query_graded_data(conn, model_id, theme, date_from, date_to)
    breakdown: dict[tuple[str, str, str], dict] = {}  # (model, theme, difficulty) -> stats

    for row in rows:
        rubric = _safe_json(row["rubric_json"])
        questions = {q["id"]: q for q in rubric.get("questions", [])}
        grades = _safe_json(row["grade_json"])
        row_theme = row["theme"] or "unknown"
        row_model = row["model_id"]

        for g in grades.get("grades", []):
            qid = g.get("question_id", "")
            q = questions.get(qid, {})
            q_diff = q.get("difficulty", "unknown")

            if difficulty and q_diff != difficulty:
                continue

            key = (row_model, row_theme, q_diff)
            if key not in breakdown:
                breakdown[key] = {"model_id": row_model, "theme": row_theme,
                                  "difficulty": q_diff, "correct": 0, "total": 0}
            breakdown[key]["total"] += 1
            if g.get("score", 0) >= g.get("max_points", 1):
                breakdown[key]["correct"] += 1

    result = []
    for stats in breakdown.values():
        stats["accuracy"] = round(stats["correct"] / stats["total"], 4) if stats["total"] else 0
        result.append(stats)
    result.sort(key=lambda r: (r["model_id"], r["theme"], r["difficulty"]))
    return result


def get_historical_trends(
    conn: sqlite3.Connection,
    model_ids: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Time-series of accuracy per model for daily challenges."""
    sql = """
        SELECT mp.model_id, c.completed_at AS date, mp.accuracy,
               c.theme, c.id AS challenge_id
        FROM model_participants mp
        JOIN challenges c ON c.id = mp.challenge_id
        WHERE mp.status = 'graded'
          AND c.kind = 'daily'
          AND c.status = 'complete'
    """
    params: list[Any] = []
    if model_ids:
        placeholders = ",".join("?" * len(model_ids))
        sql += f" AND mp.model_id IN ({placeholders})"
        params.extend(model_ids)
    if date_from:
        sql += " AND c.completed_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND c.completed_at <= ?"
        params.append(date_to)
    sql += " ORDER BY c.completed_at ASC, mp.model_id"

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_theme_stats(conn: sqlite3.Connection) -> list[dict]:
    """Aggregate stats per theme across all graded daily challenges."""
    rows = conn.execute("""
        SELECT c.theme,
               COUNT(DISTINCT c.id) AS total_challenges,
               AVG(mp.accuracy) AS avg_accuracy,
               MIN(mp.accuracy) AS min_accuracy,
               MAX(mp.accuracy) AS max_accuracy
        FROM model_participants mp
        JOIN challenges c ON c.id = mp.challenge_id
        WHERE mp.status = 'graded'
          AND c.kind = 'daily'
          AND c.status = 'complete'
          AND c.theme IS NOT NULL
        GROUP BY c.theme
        ORDER BY total_challenges DESC
    """).fetchall()

    result = []
    for r in rows:
        theme = r["theme"]
        # Best and worst model for this theme
        best = conn.execute("""
            SELECT mp.model_id, AVG(mp.accuracy) AS avg_acc
            FROM model_participants mp
            JOIN challenges c ON c.id = mp.challenge_id
            WHERE mp.status='graded' AND c.kind='daily' AND c.status='complete'
              AND c.theme = ?
            GROUP BY mp.model_id ORDER BY avg_acc DESC LIMIT 1
        """, (theme,)).fetchone()
        worst = conn.execute("""
            SELECT mp.model_id, AVG(mp.accuracy) AS avg_acc
            FROM model_participants mp
            JOIN challenges c ON c.id = mp.challenge_id
            WHERE mp.status='graded' AND c.kind='daily' AND c.status='complete'
              AND c.theme = ?
            GROUP BY mp.model_id ORDER BY avg_acc ASC LIMIT 1
        """, (theme,)).fetchone()
        result.append({
            "theme": theme,
            "total_challenges": r["total_challenges"],
            "avg_accuracy": round(r["avg_accuracy"] or 0, 4),
            "min_accuracy": round(r["min_accuracy"] or 0, 4),
            "max_accuracy": round(r["max_accuracy"] or 0, 4),
            "best_model": best["model_id"] if best else None,
            "best_accuracy": round(best["avg_acc"], 4) if best else None,
            "worst_model": worst["model_id"] if worst else None,
            "worst_accuracy": round(worst["avg_acc"], 4) if worst else None,
        })
    return result


def get_available_filters(conn: sqlite3.Connection) -> dict:
    """Return distinct values for filter dropdowns."""
    models = [r["model_id"] for r in conn.execute(
        "SELECT DISTINCT model_id FROM model_participants WHERE status='graded' ORDER BY model_id"
    ).fetchall()]
    themes = [r["theme"] for r in conn.execute(
        "SELECT DISTINCT theme FROM challenges WHERE theme IS NOT NULL AND status='complete' ORDER BY theme"
    ).fetchall()]
    difficulties = ["easy_breezy", "minor_league", "professional", "jedi"]
    return {"models": models, "themes": themes, "difficulties": difficulties}


# ─────────────────────────────────────────────
# Analytics cache (snapshot refresh)
# ─────────────────────────────────────────────

def refresh_analytics_snapshot(conn: sqlite3.Connection, challenge_id: int | None = None) -> None:
    """Rebuild the analytics_snapshots table from raw data.

    Called after each challenge completes.  Full rebuild is fast (parses
    JSON blobs for all graded participants — typically <1s on SQLite).
    """
    rows = _query_graded_data(conn)
    aggregated: dict[tuple[str, str, str], dict] = {}

    for row in rows:
        rubric = _safe_json(row["rubric_json"])
        questions = {q["id"]: q for q in rubric.get("questions", [])}
        grades = _safe_json(row["grade_json"])
        row_theme = row["theme"] or "unknown"
        row_model = row["model_id"]

        for g in grades.get("grades", []):
            qid = g.get("question_id", "")
            q = questions.get(qid, {})
            q_diff = q.get("difficulty", "unknown")
            key = (row_model, row_theme, q_diff)
            if key not in aggregated:
                aggregated[key] = {"correct": 0, "total": 0, "challenges": set()}
            aggregated[key]["total"] += 1
            aggregated[key]["challenges"].add(row["challenge_id"])
            if g.get("score", 0) >= g.get("max_points", 1):
                aggregated[key]["correct"] += 1

    with conn:
        conn.execute("DELETE FROM analytics_snapshots")
        for (model, theme, diff), stats in aggregated.items():
            acc = round(stats["correct"] / stats["total"], 4) if stats["total"] else 0
            conn.execute(
                """INSERT INTO analytics_snapshots
                   (model_id, theme, difficulty, challenges, correct, total, accuracy, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (model, theme, diff, len(stats["challenges"]), stats["correct"], stats["total"], acc),
            )
        conn.commit()
    logger.info("Analytics snapshot refreshed: %d entries", len(aggregated))


# ─────────────────────────────────────────────
# Export: CSV
# ─────────────────────────────────────────────

def generate_csv_report(
    conn: sqlite3.Connection,
    model_id: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bytes:
    """Generate a CSV report as bytes. One row per question per model."""
    rows = _query_graded_data(conn, model_id, theme, date_from, date_to)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "date", "challenge_id", "theme", "model", "question_id",
        "difficulty", "domain", "score", "max_points", "correct",
    ])

    for row in rows:
        rubric = _safe_json(row["rubric_json"])
        questions = {q["id"]: q for q in rubric.get("questions", [])}
        grades = _safe_json(row["grade_json"])

        for g in grades.get("grades", []):
            qid = g.get("question_id", "")
            q = questions.get(qid, {})
            q_diff = q.get("difficulty", "unknown")
            if difficulty and q_diff != difficulty:
                continue
            score = g.get("score", 0)
            max_pts = g.get("max_points", 1)
            correct = 1 if score >= max_pts else 0
            writer.writerow([
                row["completed_at"] or row["created_at"],
                row["challenge_id"],
                row["theme"] or "",
                row["model_id"],
                qid,
                q_diff,
                q.get("domain", ""),
                score,
                max_pts,
                correct,
            ])

    return buf.getvalue().encode("utf-8")


# ─────────────────────────────────────────────
# Export: PDF
# ─────────────────────────────────────────────

def generate_pdf_report(
    conn: sqlite3.Connection,
    model_id: str | None = None,
    theme: str | None = None,
    difficulty: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> bytes:
    """Generate a PDF benchmark report as bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet

    breakdown = get_model_breakdown(conn, model_id, theme, difficulty, date_from, date_to)
    theme_stats = get_theme_stats(conn)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph("OGAI Benchmark Analytics Report", styles["Title"]))
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    elements.append(Paragraph(f"Generated: {now_str}", styles["Normal"]))
    filters_text = []
    if model_id:
        filters_text.append(f"Model: {model_id}")
    if theme:
        filters_text.append(f"Theme: {theme}")
    if difficulty:
        filters_text.append(f"Difficulty: {difficulty}")
    if date_from:
        filters_text.append(f"From: {date_from}")
    if date_to:
        filters_text.append(f"To: {date_to}")
    if filters_text:
        elements.append(Paragraph(f"Filters: {', '.join(filters_text)}", styles["Normal"]))
    elements.append(Spacer(1, 0.3 * inch))

    # Model breakdown table
    if breakdown:
        elements.append(Paragraph("Performance Breakdown", styles["Heading2"]))
        table_data = [["Model", "Theme", "Difficulty", "Correct", "Total", "Accuracy"]]
        for b in breakdown:
            table_data.append([
                b["model_id"], b["theme"], b["difficulty"],
                str(b["correct"]), str(b["total"]),
                f"{b['accuracy']:.1%}",
            ])
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161b2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4ff")]),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.3 * inch))

    # Theme stats table
    if theme_stats:
        elements.append(Paragraph("Theme Summary", styles["Heading2"]))
        table_data = [["Theme", "Challenges", "Avg Accuracy", "Best Model", "Worst Model"]]
        for ts in theme_stats:
            table_data.append([
                ts["theme"],
                str(ts["total_challenges"]),
                f"{ts['avg_accuracy']:.1%}",
                f"{ts['best_model']} ({ts['best_accuracy']:.1%})" if ts["best_model"] else "—",
                f"{ts['worst_model']} ({ts['worst_accuracy']:.1%})" if ts["worst_model"] else "—",
            ])
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#161b2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f4ff")]),
            ("ALIGN", (1, 0), (2, -1), "RIGHT"),
        ]))
        elements.append(t)

    doc.build(elements)
    return buf.getvalue()


# ─────────────────────────────────────────────
# Email notifications
# ─────────────────────────────────────────────

def send_daily_complete_email(
    conn: sqlite3.Connection,
    challenge_id: int,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    smtp_from: str,
    app_base_url: str,
) -> None:
    """Send daily challenge completion notification to opted-in users.

    Unlike main._send_email(), this logs errors instead of raising
    HTTPException — safe for background threads.
    """
    if not smtp_host or not smtp_user or not smtp_pass:
        return

    # Get challenge details
    ch = conn.execute(
        "SELECT * FROM challenges WHERE id = ?", (challenge_id,)
    ).fetchone()
    if not ch:
        return

    # Get top-performing model
    top = conn.execute(
        """SELECT model_id, accuracy FROM model_participants
           WHERE challenge_id = ? AND status = 'graded'
           ORDER BY accuracy DESC LIMIT 1""",
        (challenge_id,),
    ).fetchone()

    # Get users who opted in
    users = conn.execute(
        """SELECT u.email, u.display_name
           FROM notification_preferences np
           JOIN users u ON u.id = np.user_id
           WHERE np.daily_complete = 1"""
    ).fetchall()

    if not users:
        return

    theme = ch["theme"] or "General"
    top_model = top["model_id"] if top else "N/A"
    top_accuracy = f"{top['accuracy']:.1%}" if top else "N/A"

    subject = f"Daily Challenge Complete: {theme}"
    body = (
        f"The Daily AI Researcher Challenge has completed!\n\n"
        f"Theme: {theme}\n"
        f"Top Model: {top_model} ({top_accuracy})\n\n"
        f"View results: {app_base_url}/challenges/{challenge_id}\n"
        f"Leaderboard: {app_base_url}/leaderboard\n"
    )

    for user in users:
        try:
            msg = EmailMessage()
            msg["From"] = smtp_from
            msg["To"] = user["email"]
            msg["Subject"] = subject
            msg.set_content(body)
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            logger.info("Sent daily-complete email to %s", user["email"])
        except Exception as e:
            logger.error("Failed to send daily-complete email to %s: %s", user["email"], e)


# ─────────────────────────────────────────────
# Rate limiter (in-memory, per-IP)
# ─────────────────────────────────────────────

_rate_limit_store: dict[str, list[float]] = {}
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 60     # requests per window


def check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    import time
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    if ip not in _rate_limit_store:
        _rate_limit_store[ip] = []

    # Prune old entries
    _rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if t > cutoff]

    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return False

    _rate_limit_store[ip].append(now)
    return True


# ─────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────

def _safe_json(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _query_graded_data(
    conn: sqlite3.Connection,
    model_id: str | None = None,
    theme: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[sqlite3.Row]:
    """Fetch joined graded data: model_participants + challenges + challenge_rubrics."""
    sql = """
        SELECT mp.model_id, mp.grade_json, mp.accuracy,
               c.id AS challenge_id, c.theme, c.completed_at, c.created_at,
               cr.rubric_json
        FROM model_participants mp
        JOIN challenges c ON c.id = mp.challenge_id
        JOIN challenge_rubrics cr ON cr.challenge_id = c.id
        WHERE mp.status = 'graded'
          AND c.status = 'complete'
    """
    params: list[Any] = []
    if model_id:
        sql += " AND mp.model_id = ?"
        params.append(model_id)
    if theme:
        sql += " AND c.theme = ?"
        params.append(theme)
    if date_from:
        sql += " AND c.completed_at >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND c.completed_at <= ?"
        params.append(date_to)
    return conn.execute(sql, params).fetchall()
